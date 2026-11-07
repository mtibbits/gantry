"""The build stamp and sample mode (#11).

Sample mode holds when every configured root resolves inside the sample
directory; #59 will swap in its own config_roots, outside and sample_dir with
no change in behavior. Every path here is built at run time from tmp_path.
"""

import os
import shutil
from datetime import datetime, timezone

import pytest

from gantry import stamp
from gantry.build import build_all, write_all
from gantry.config import load_config
from gantry.sites import load_sites
from gantry.stamp import BUILT_STAMP, SAMPLE_STAMP, outside, sample_dir, sample_mode, stamp_text
from helpers_config import append_bytes, copy_samples, demo_build
from sample_roles import BUSY, SMALL
from test_samples import ROOT

NOW = datetime(2026, 11, 7, 14, 3, tzinfo=timezone.utc)


def loaded(path):
    config = load_config(path)
    return config, load_sites(config)[0]


def literal(path):
    """A TOML literal string for a path: no escapes, so a Windows path needs none."""
    text = str(path)
    assert "'" not in text
    return f"'{text}'"


def test_sample_dir_from_source_tree(tmp_path):
    assert os.path.samefile(sample_dir(), ROOT / "samples")
    assert sample_dir(package_dir=tmp_path / "gantry") is None
    # The folder alone is not enough: it must hold the sample config.
    (tmp_path / "samples").mkdir()
    assert sample_dir(package_dir=tmp_path / "gantry") is None


def test_sample_config_is_sample_mode():
    assert sample_mode(*loaded(ROOT / "samples" / "gantry.sample.toml")) is True


def _move_config(copy, outside_dir):
    """The config file itself outside the copy, every path in it absolute and inside."""
    text = (copy / "gantry.sample.toml").read_bytes().decode("utf-8")
    text = text.replace('devagent_config = "devagent/config.toml"',
                        f"devagent_config = {literal(copy / 'devagent' / 'config.toml')}")
    text = text.replace('output_dir = "out"', f"output_dir = {literal(copy / 'out')}")
    text = text.replace(f'requirements = "{SMALL}/requirements.md"',
                        f"requirements = {literal(copy / SMALL / 'requirements.md')}")
    moved = outside_dir / "gantry.toml"
    moved.write_bytes(text.encode("utf-8"))
    return moved


def _move_devagent(copy, outside_dir):
    devagent = copy / "devagent" / "config.toml"
    text = devagent.read_bytes().decode("utf-8")
    for name in ("harbor", "orchard", "quarry", "lighthouse", "bakery", "marsh"):
        text = text.replace(f'devdoc_dir = "../{name}"', f"devdoc_dir = {literal(copy / name)}")
    (outside_dir / "config.toml").write_bytes(text.encode("utf-8"))
    config = copy / "gantry.sample.toml"
    text = config.read_bytes().decode("utf-8").replace(
        'devagent_config = "devagent/config.toml"', f"devagent_config = {literal(outside_dir / 'config.toml')}")
    config.write_bytes(text.encode("utf-8"))
    return config


def _move_output(copy, outside_dir):
    config = copy / "gantry.sample.toml"
    text = config.read_bytes().decode("utf-8").replace('output_dir = "out"', f"output_dir = {literal(outside_dir)}")
    config.write_bytes(text.encode("utf-8"))
    return config


def _move_devdoc(copy, outside_dir):
    shutil.copytree(copy / BUSY, outside_dir / BUSY)
    devagent = copy / "devagent" / "config.toml"
    text = devagent.read_bytes().decode("utf-8").replace(
        f'devdoc_dir = "../{BUSY}"', f"devdoc_dir = {literal(outside_dir / BUSY)}")
    devagent.write_bytes(text.encode("utf-8"))
    return copy / "gantry.sample.toml"


def _move_requirements(copy, outside_dir):
    config = copy / "gantry.sample.toml"
    text = config.read_bytes().decode("utf-8").replace(
        f'requirements = "{SMALL}/requirements.md"', f"requirements = {literal(outside_dir / 'requirements.md')}")
    config.write_bytes(text.encode("utf-8"))
    return config


def _move_estimates(copy, outside_dir):
    config = copy / "gantry.sample.toml"
    append_bytes(config, f"[site.{BUSY}]\nestimates = {literal(outside_dir / 'estimates.md')}\n")
    return config


MOVES = {"config": _move_config, "devagent_config": _move_devagent, "output_dir": _move_output,
         "devdoc_dir": _move_devdoc, "requirements": _move_requirements, "estimates": _move_estimates}


@pytest.mark.parametrize("root", list(MOVES))
def test_root_outside_not_sample_mode(tmp_path, root):
    copy = copy_samples(tmp_path)
    assert sample_mode(*loaded(copy / "gantry.sample.toml"), samples=copy) is True
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    config = MOVES[root](copy, elsewhere)
    assert sample_mode(*loaded(config), samples=copy) is False


def test_outside_cases(tmp_path):
    base = copy_samples(tmp_path)
    up, sibling, out = base / ".." / "x", tmp_path / "samples-x", base / "out"
    assert not out.exists()
    assert outside([("up", up), ("sibling", sibling), ("out", out), ("self", base)], base) == [up, sibling]


def test_no_sample_dir_never_sample_mode(monkeypatch):
    monkeypatch.setattr(stamp, "sample_dir", lambda package_dir=None: None)
    assert sample_mode(*loaded(ROOT / "samples" / "gantry.sample.toml")) is False


def test_stamp_texts(tmp_path):
    copy = copy_samples(tmp_path)
    config, sites = loaded(copy / "gantry.sample.toml")
    assert stamp_text(config, sites, now=NOW, samples=copy) == "sample data as of 2026-01-20"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    config, sites = loaded(_move_output(copy, elsewhere))
    assert stamp_text(config, sites, now=NOW, samples=copy) == "built 2026-11-07 14:03 UTC; data as of 2026-01-20"
    with pytest.raises(ValueError):
        stamp_text(config, sites, now=datetime(2026, 11, 7, 14, 3), samples=copy)


def test_stamp_doc():
    doc = (ROOT / "docs" / "worksite-page.md").read_bytes().decode("utf-8")
    assert f"`{SAMPLE_STAMP}`" in doc
    assert f"`{BUILT_STAMP}`" in doc


# --- repeatability --------------------------------------------------------------------


def written(out):
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def test_demo_builds_byte_identical(tmp_path, monkeypatch):
    first = demo_build(tmp_path / "a", monkeypatch, copy=copy_samples(tmp_path / "a"))
    second = demo_build(tmp_path / "b", monkeypatch, copy=copy_samples(tmp_path / "b"))
    assert first[0] == second[0] == 0
    one, two = written(first[1]), written(second[1])
    assert len(one) == 5
    assert one == two
    for rel, data in one.items():
        text = data.decode("utf-8")
        assert "sample data as of 2026-01-20" in text, rel
        assert " UTC" not in text, rel


def test_outside_root_stamp_has_time(tmp_path):
    copy = copy_samples(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    config = load_config(_move_output(copy, elsewhere))
    result = build_all(config, now=NOW, samples=copy)
    expected = BUILT_STAMP.format(when="2026-11-07 14:03 UTC", date="2026-01-20")
    assert result.stamp == expected
    result, paths, error = write_all(result)
    assert error is None and len(paths) == 5
    assert all(expected in path.read_bytes().decode("utf-8") for path in paths)
