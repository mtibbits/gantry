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
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import sample_roles
from sample_roles import (
    BUSY,
    HOSTILE_SITE,
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


# --- WBS sites ----------------------------------------------------------------

# The first two patterns copy devagent's _BULLET_RE and _META_BLOCK_RE
# (scripts/lib/wbs-parser.py:103-108); the rest is this check's own grammar.
NODE_RE = re.compile(r"^(?P<indent>\s*)- \[(?P<glyph>[ x\-!~?P])\]\s+(?P<rest>.+)$")
META_RE = re.compile(r"\s*\{(?P<body>[^{}]*)\}\s*$")
META_ITEM_RE = re.compile(r"(?P<key>[a-z_]+):\s*(?P<val>\[[^\]]*\]|[^,]*)")
ISSUE_ID_RE = re.compile(r"^Issue-[1-9][0-9]*$")
EST_SHAPES = {"Nd": r"^\d+d$", "Nw": r"^\d+w$", "?w": r"^\?w$", "Npt": r"^\d+pt$"}
OUTLINE_RE = re.compile(r"^\d+(\.\d+)+$")
FINISHED, SKIPPED = "x", "-"   # open = glyph not in {"x", "-"}
FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
MARSH_BAD_OFFSET = 88


@dataclass
class Node:
    line: int
    glyph: str
    depth: int
    title: str
    meta: dict = field(default_factory=dict)
    is_leaf: bool = False
    is_epic: bool = False
    epic: "Node | None" = None

    @property
    def is_open(self):
        return self.glyph not in (FINISHED, SKIPPED)

    def deps(self):
        value = self.meta.get("depends_on", [])
        return value if isinstance(value, list) else [value]


def _parse_meta(body):
    meta = {}
    for m in META_ITEM_RE.finditer(body):
        val = m.group("val").strip()
        if val.startswith("["):
            meta[m.group("key")] = [v.strip() for v in val[1:-1].split(",") if v.strip()]
        else:
            meta[m.group("key")] = val
    return meta


def parse_nodes(text):
    nodes = []
    for number, line in enumerate(text.splitlines(), 1):
        m = NODE_RE.match(line)
        if not m:
            continue
        rest = m.group("rest")
        meta_m = META_RE.search(rest)
        meta = _parse_meta(meta_m.group("body")) if meta_m else {}
        title = (rest[: meta_m.start()] if meta_m else rest).strip()
        depth = len(m.group("indent").expandtabs(2)) // 2
        nodes.append(Node(number, m.group("glyph"), depth, title, meta))
    epic = None
    for i, node in enumerate(nodes):
        deeper_next = i + 1 < len(nodes) and nodes[i + 1].depth > node.depth
        node.is_leaf = not deeper_next
        node.is_epic = node.depth == 0 and deeper_next
        if node.depth == 0:
            epic = node
        node.epic = epic
    return nodes


def wbs(site):
    return parse_nodes((SAMPLES / site / "WBS.md").read_bytes().decode("utf-8"))


def issue_leaves(nodes):
    return [n for n in nodes if n.is_leaf and "issue" in n.meta]


def test_site_dirs_and_wbs_presence():
    for name in sorted(load_config()["project"]):
        folder = SAMPLES / name
        assert folder.is_dir(), name
        if name == NO_WBS:
            assert (folder / "README.md").is_file()
            assert not (folder / "WBS.md").exists()
        else:
            assert (folder / "WBS.md").is_file(), name


@pytest.mark.parametrize("site", sorted(READABLE))
def test_readable_wbs_decode_utf8(site):
    (SAMPLES / site / "WBS.md").read_bytes().decode("utf-8")


def test_marsh_decode_error_at_offset():
    data = (SAMPLES / UNREADABLE / "WBS.md").read_bytes()
    with pytest.raises(UnicodeDecodeError) as err:
        data.decode("utf-8")
    assert err.value.start == MARSH_BAD_OFFSET
    assert data[MARSH_BAD_OFFSET] == 0xE9
    text = data.decode("latin-1")
    assert sum(1 for line in text.splitlines() if NODE_RE.match(line)) >= 3


@pytest.mark.parametrize("site", sorted(READABLE))
def test_wbs_line_patterns_apply(site):
    lines = (SAMPLES / site / "WBS.md").read_bytes().decode("utf-8").splitlines()
    assert not [line for line in lines if FENCE_RE.match(line)]
    node_lines = [NODE_RE.match(line) for line in lines if NODE_RE.match(line)]
    for m in node_lines:
        indent = m.group("indent")
        assert set(indent) <= {" "} and len(indent) % 2 == 0, m.group(0)
    assert len(node_lines) >= 5


def test_harbor_shapes():
    nodes = wbs(BUSY)
    epics = [n for n in nodes if n.is_epic]
    assert len(epics) >= 2
    assert all("issue" in e.meta for e in epics)
    priorities = {n.meta.get("priority") for n in nodes}
    assert {"P1", "P2", "P3", "P4"} <= priorities
    assert any(n.is_leaf and "priority" not in n.meta for n in nodes)
    leaves = issue_leaves(nodes)
    assert sum(n.is_open for n in leaves) >= 8
    assert sum(n.glyph == FINISHED for n in leaves) >= 5
    assert any(n.glyph == "~" for n in leaves)
    assert any(n.is_leaf and n.glyph == SKIPPED for n in nodes)
    epic_of = {n.meta["issue"]: n.epic for n in leaves}
    assert any(
        dep in epic_of and epic_of[dep] is not n.epic
        for n in nodes if n.is_leaf for dep in n.deps()
    )


def test_orchard_shapes():
    nodes = wbs(OWN_UNIT)
    ests = [n.meta["est"] for n in nodes if "est" in n.meta]
    for shape, pattern in EST_SHAPES.items():
        assert any(re.match(pattern, e) for e in ests), shape
    assert "priority:" not in (SAMPLES / OWN_UNIT / "WBS.md").read_text(encoding="utf-8")
    assert sum(1 for n in nodes if n.is_leaf and "issue" not in n.meta) >= 3
    assert any(
        OUTLINE_RE.match(dep) and not ISSUE_ID_RE.match(dep)
        for n in nodes for dep in n.deps()
    )
    leaves = issue_leaves(nodes)
    assert any(n.is_open for n in leaves)
    assert sum(n.glyph == FINISHED for n in leaves) >= 3


def test_quarry_shapes():
    nodes = wbs(UNESTIMATED)
    assert not any("est" in n.meta for n in nodes)
    assert not list((SAMPLES / UNESTIMATED).glob("estimates-*.md"))
    leaves = [n for n in nodes if n.is_leaf]
    assert any("priority" in n.meta for n in leaves)
    assert any("priority" not in n.meta for n in leaves)
    epic_of = {n.meta["issue"]: n.epic for n in issue_leaves(nodes)}
    assert any(
        dep in epic_of and epic_of[dep] is not n.epic
        for n in leaves for dep in n.deps()
    )
    ids = {n.meta["issue"] for n in nodes if "issue" in n.meta}
    assert any(
        ISSUE_ID_RE.match(dep) and dep not in ids
        for n in nodes for dep in n.deps()
    )


def test_lighthouse_shapes():
    assert HOSTILE_SITE == SMALL
    nodes = wbs(HOSTILE_SITE)
    assert any(n.is_epic for n in nodes)
    assert any(n.depth == 0 and n.is_leaf for n in nodes)
    assert any(n.is_leaf and n.is_open and n.title == HOSTILE_TITLE for n in nodes)


@pytest.mark.parametrize("site", sorted(READABLE))
def test_every_readable_site_has_open_and_finished_issue_leaves(site):
    leaves = issue_leaves(wbs(site))
    assert any(n.is_open for n in leaves)
    assert sum(n.glyph == FINISHED for n in leaves) >= 3


@pytest.mark.parametrize("site", sorted(READABLE))
def test_ids_well_formed_and_unique_per_site(site):
    ids = [n.meta["issue"] for n in wbs(site) if "issue" in n.meta]
    assert ids
    assert all(ISSUE_ID_RE.match(i) for i in ids), ids
    assert len(ids) == len(set(ids)), ids


def test_shared_open_id_across_sites():
    open_ids = {
        site: {n.meta["issue"] for n in issue_leaves(wbs(site)) if n.is_open}
        for site in sorted(READABLE)
    }
    sites = sorted(open_ids)
    assert any(
        open_ids[a] & open_ids[b]
        for i, a in enumerate(sites) for b in sites[i + 1:]
    )


@pytest.mark.parametrize("site", sorted(READABLE))
def test_no_stuck_blocked_parked_glyphs(site):
    assert not [n.title for n in wbs(site) if n.glyph in "!?P"]


def test_depends_on_prefixes_not_public_tools():
    for site in sorted(READABLE):
        for n in wbs(site):
            for dep in n.deps():
                assert not dep.startswith(("devagent:", "gantry:")), (site, dep)
