"""Line-pattern checks over gantry's fictional sample data (samples/).

Every check is a plain function that takes a root (or a path) and returns a
list of offenders, so the real-repo test and each planted temp-folder test
call the same predicate.
"""

import sys

if sys.version_info < (3, 11):
    raise RuntimeError("tests/test_samples.py needs Python 3.11+ (tomllib)")

import re
import tomllib
from pathlib import Path

import pytest

import sample_roles
from sample_roles import (
    BUSY,
    HOSTILE_TITLE,
    NO_WBS,
    NOT_BUILT,
    OWN_UNIT,
    READABLE,
    SMALL,
    UNESTIMATED,
    UNREADABLE,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
CONFIG = SAMPLES / "devagent" / "config.toml"

RESERVED_FOLDERS = {"devagent", "out", "history", "actuals", "forecast"}
PUBLIC_TOOLS = {"devagent", "gantry"}


# --- config and roles -------------------------------------------------------

# devagent's path fields (scripts/lib/config.sh:38-39), plus any key ending _dir.
PROJECT_PATH_KEYS = {"source_dir", "devdoc_dir", "worktree_root", "build_dir"}
ABSOLUTE_START_RE = re.compile(r"^(?:[/\\~]|[A-Za-z]:)")


def _walk_strings(node, dotted):
    """Yield (dotted key, string) for every string anywhere under node."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_strings(value, f"{dotted}.{key}" if dotted else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_strings(value, f"{dotted}[{i}]")
    elif isinstance(node, str):
        yield dotted, node


def _is_path_key(dotted):
    parts = dotted.split(".")
    leaf = re.sub(r"\[\d+\]$", "", parts[-1])
    if leaf.endswith("_dir"):
        return True
    if len(parts) >= 3 and parts[0] == "project":
        if len(parts) == 3 and leaf in PROJECT_PATH_KEYS:
            return True
        if parts[2] == "paths" and len(parts) >= 4:
            return True
    return False


def config_violations(config_path, samples_root):
    cfg = tomllib.loads(Path(config_path).read_text(encoding="utf-8"))
    base = Path(config_path).parent
    inside = Path(samples_root).resolve()
    offenders = []
    for dotted, value in _walk_strings(cfg, ""):
        if ABSOLUTE_START_RE.match(value):
            offenders.append(f"{dotted}: absolute or ~ string")
        elif _is_path_key(dotted) and not (base / value).resolve().is_relative_to(inside):
            offenders.append(f"{dotted}: resolves outside the samples root")
    return offenders


def load_config(path=CONFIG):
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def test_config_parses_and_lists_sites():
    cfg = load_config()
    assert set(cfg["project"]) == {BUSY, OWN_UNIT, UNESTIMATED, SMALL, NO_WBS, UNREADABLE}
    assert isinstance(cfg.get("defaults"), dict)
    assert any(
        isinstance(value, dict)
        for project in cfg["project"].values()
        for value in project.values()
    )


def test_config_paths_relative_and_inside():
    assert config_violations(CONFIG, SAMPLES) == []
    cfg = load_config()
    for name in sorted(cfg["project"]):
        devdoc = cfg["project"][name]["devdoc_dir"]
        assert isinstance(devdoc, str)
        assert not ABSOLUTE_START_RE.match(devdoc), name
        assert (CONFIG.parent / devdoc).resolve() == (SAMPLES / name).resolve()


def _temp_config(tmp_path):
    samples_root = tmp_path / "samples"
    copy = samples_root / "devagent" / "config.toml"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(CONFIG.read_bytes())
    return copy, samples_root


def test_config_copy_control_passes(tmp_path):
    copy, samples_root = _temp_config(tmp_path)
    assert config_violations(copy, samples_root) == []


def _insert_after(text, anchor, line):
    return text.replace(anchor + "\n", anchor + "\n" + line + "\n", 1)


# Each plant: (edit of the config text, dotted key the offender must name).
# Absolute forms are built at run time so this source holds none.
PLANTS = {
    "worktree_root": (
        lambda t: _insert_after(t, "[project.harbor]", 'worktree_root = "../../.."'),
        "project.harbor.worktree_root",
    ),
    "build_dir": (
        lambda t: _insert_after(t, "[project.harbor]", 'build_dir = "../../.."'),
        "project.harbor.build_dir",
    ),
    "paths_entry": (
        lambda t: t + '\n[project.harbor.paths]\nplanted = "../../outside.md"\n',
        "project.harbor.paths.planted",
    ),
    "scratch_dir": (
        lambda t: _insert_after(t, "[defaults]", 'scratch_dir = "../.."'),
        "defaults.scratch_dir",
    ),
    "absolute_string": (
        lambda t: _insert_after(t, "[defaults]", "note = '" + "C" + ":" + "/" + "x" + "'"),
        "defaults.note",
    ),
    "tilde_string": (
        lambda t: _insert_after(t, "[defaults]", "note = '" + "~" + "/" + "x" + "'"),
        "defaults.note",
    ),
    "slash_string": (
        lambda t: _insert_after(t, "[defaults]", "note = '" + "/" + "x" + "'"),
        "defaults.note",
    ),
    "backslash_string": (
        lambda t: _insert_after(t, "[defaults]", "note = '" + "\\" + "x" + "'"),
        "defaults.note",
    ),
}


@pytest.mark.parametrize("plant", sorted(PLANTS))
def test_config_planted_value_fails(tmp_path, plant):
    edit, key = PLANTS[plant]
    copy, samples_root = _temp_config(tmp_path)
    before = copy.read_text(encoding="utf-8")
    after = edit(before)
    assert after != before, "the planted edit did not change the text"
    copy.write_bytes(after.encode("utf-8"))
    offenders = config_violations(copy, samples_root)
    assert any(o.startswith(key + ":") for o in offenders), offenders


def test_config_names_not_public_tools():
    cfg = load_config()
    for name in sorted(cfg["project"]):
        assert name not in PUBLIC_TOOLS
        repo = cfg["project"][name].get("issue_source", {}).get("repo", "")
        assert repo not in PUBLIC_TOOLS
        assert repo.split("/")[0] not in PUBLIC_TOOLS


def test_roles_name_config_projects():
    cfg = load_config()
    names = {
        attr: value
        for attr, value in vars(sample_roles).items()
        if attr.isupper() and isinstance(value, str) and attr != "HOSTILE_TITLE"
    }
    assert len(names) == 7, sorted(names)
    for attr in sorted(names):
        assert names[attr] in cfg["project"], attr
    assert READABLE == {BUSY, OWN_UNIT, UNESTIMATED, SMALL}
    assert NOT_BUILT == {NO_WBS, UNREADABLE}
    assert len(READABLE) == 4
    assert READABLE.isdisjoint(NOT_BUILT)
    assert not set(cfg["project"]) & RESERVED_FOLDERS
