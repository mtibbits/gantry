"""The build stamp and sample mode (#11).

Sample mode holds when every root of the run resolves inside the sample
directory, with #59's config_roots, outside and sample_dir. Every path here
is built at run time from tmp_path.
"""

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePath

import pytest

from gantry import stamp
from gantry.build import build_all, write_all
from gantry.config import load_config
from gantry.roots import directory_roots
from gantry.stamp import BUILT_STAMP, SAMPLE_STAMP, outside, sample_dir, sample_mode, stamp_text
from helpers_config import add_site_key, copy_samples, demo_build, literal, written_bytes
from sample_roles import BUSY, SMALL
from test_samples import ROOT

NOW = datetime(2026, 11, 7, 14, 3, tzinfo=timezone.utc)


def loaded(path):
    return load_config(path)


def test_sample_dir_from_source_tree(tmp_path):
    assert os.path.samefile(sample_dir(), ROOT / "samples")
    assert isinstance(sample_dir(), PurePath) and not isinstance(sample_dir(), Path)


def test_sample_config_is_sample_mode():
    assert sample_mode(loaded(ROOT / "samples" / "gantry.sample.toml")) is True


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
    add_site_key(config, BUSY, f"estimates = {literal(outside_dir / 'estimates.md')}\n")
    return config


MOVES = {"config": _move_config, "devagent_config": _move_devagent, "output_dir": _move_output,
         "devdoc_dir": _move_devdoc, "requirements": _move_requirements, "estimates": _move_estimates}


@pytest.mark.parametrize("root", list(MOVES))
def test_root_outside_not_sample_mode(tmp_path, root):
    copy = copy_samples(tmp_path)
    assert sample_mode(loaded(copy / "gantry.sample.toml"), samples=copy) is True
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    config = MOVES[root](copy, elsewhere)
    assert sample_mode(loaded(config), samples=copy) is False


def test_outside_cases(tmp_path):
    base = copy_samples(tmp_path)
    up, sibling, out = base / ".." / "x", tmp_path / "samples-x", base / "out"
    assert not out.exists()
    assert [r.key for r in outside(directory_roots([up, sibling, out, base]), base)] == ["x", "samples-x"]


def test_no_sample_dir_never_sample_mode(monkeypatch):
    monkeypatch.setattr(stamp, "sample_dir", lambda: None)
    assert sample_mode(loaded(ROOT / "samples" / "gantry.sample.toml")) is False


def test_stamp_texts(tmp_path):
    copy = copy_samples(tmp_path)
    config = loaded(copy / "gantry.sample.toml")
    assert stamp_text(config, now=NOW, samples=copy) == "sample data as of 2026-01-20"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    config = loaded(_move_output(copy, elsewhere))
    assert stamp_text(config, now=NOW, samples=copy) == "built 2026-11-07 14:03 UTC; data as of 2026-01-20"
    with pytest.raises(ValueError):
        stamp_text(config, now=datetime(2026, 11, 7, 14, 3), samples=copy)


def test_stamp_doc():
    doc = (ROOT / "docs" / "worksite-page.md").read_bytes().decode("utf-8")
    assert f"`{SAMPLE_STAMP}`" in doc
    assert f"`{BUILT_STAMP}`" in doc


# --- repeatability --------------------------------------------------------------------


def test_demo_builds_byte_identical(tmp_path, monkeypatch):
    first = demo_build(tmp_path / "a", monkeypatch, copy=copy_samples(tmp_path / "a"))
    second = demo_build(tmp_path / "b", monkeypatch, copy=copy_samples(tmp_path / "b"))
    assert first[0] == second[0] == 0
    one, two = written_bytes(first[1]), written_bytes(second[1])
    assert len(one) == 6
    assert one == two
    # The identity covers the cross-site path (#68): the pages link 6 boxes on another site.
    assert sum(text.count(b'class="fbox fbox-open" href="') for rel, text in one.items() if rel.startswith("sites/")) == 6
    for rel, data in one.items():
        if rel == "week-plan.json":
            continue  # the plan file carries the date in its own as_of field, and no stamp (#61)
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
    assert all(expected in Path(path).read_bytes().decode("utf-8") for path in paths)
