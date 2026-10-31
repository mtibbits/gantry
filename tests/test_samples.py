"""Line-pattern checks over gantry's fictional sample data (samples/).

Every check is a plain function that takes a root (or a path) and returns a
list of offenders, so the real-repo test and each planted temp-folder test
call the same predicate.
"""

import sys

if sys.version_info < (3, 11):
    raise RuntimeError("tests/test_samples.py needs Python 3.11+ (tomllib)")

import functools
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pytest

import sample_roles
# One form list for the samples check, the source scan and the commit check (#44).
from commit_check import ABSOLUTE_START_RE, PATH_FORMS, decode_bytes, walk_strings, path_form_hits
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


def load_config(path=CONFIG):
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def config_violations(config_path, samples_root):
    cfg = load_config(config_path)
    base = Path(config_path).parent
    inside = Path(samples_root).resolve()
    offenders = []
    for dotted, value in walk_strings(cfg, ""):
        if ABSOLUTE_START_RE.match(value):
            offenders.append(f"{dotted}: absolute or ~ string")
        elif _is_path_key(dotted) and not (base / value).resolve().is_relative_to(inside):
            offenders.append(f"{dotted}: resolves outside the samples root")
    return offenders


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
    shutil.copyfile(CONFIG, copy)
    return copy, samples_root


def test_config_copy_control_passes(tmp_path):
    copy, samples_root = _temp_config(tmp_path)
    assert config_violations(copy, samples_root) == []


def _insert_after(text, anchor, line):
    if anchor is None:
        return text + "\n" + line + "\n"
    return text.replace(anchor + "\n", anchor + "\n" + line + "\n", 1)


# Each plant: (anchor line or None to append, planted line, dotted key the
# offender must name). Absolute forms are built at run time so this source
# holds none.
PLANTS = {
    "worktree_root": ("[project.harbor]", 'worktree_root = "../../.."', "project.harbor.worktree_root"),
    "build_dir": ("[project.harbor]", 'build_dir = "../../.."', "project.harbor.build_dir"),
    "paths_entry": (
        None,
        '[project.harbor.paths]\nplanted = "../../outside.md"',
        "project.harbor.paths.planted",
    ),
    "scratch_dir": ("[defaults]", 'scratch_dir = "../.."', "defaults.scratch_dir"),
    "absolute_string": ("[defaults]", "note = '" + "C" + ":" + "/" + "x" + "'", "defaults.note"),
    "tilde_string": ("[defaults]", "note = '" + "~" + "/" + "x" + "'", "defaults.note"),
    "slash_string": ("[defaults]", "note = '" + "/" + "x" + "'", "defaults.note"),
    "backslash_string": ("[defaults]", "note = '" + "\\" + "x" + "'", "defaults.note"),
}


@pytest.mark.parametrize("plant", sorted(PLANTS))
def test_config_planted_value_fails(tmp_path, plant):
    anchor, line, key = PLANTS[plant]
    copy, samples_root = _temp_config(tmp_path)
    before = copy.read_text(encoding="utf-8")
    after = _insert_after(before, anchor, line)
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


def wbs_text(site):
    return (SAMPLES / site / "WBS.md").read_bytes().decode("utf-8")


def wbs(site):
    return parse_nodes(wbs_text(site))


def issue_leaves(nodes):
    return [n for n in nodes if n.is_leaf and "issue" in n.meta]


def has_cross_epic_dep(nodes):
    epic_of = {n.meta["issue"]: n.epic for n in issue_leaves(nodes)}
    return any(
        dep in epic_of and epic_of[dep] is not n.epic
        for n in nodes if n.is_leaf for dep in n.deps()
    )


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
    wbs_text(site)


def test_marsh_decode_error_at_offset():
    data = (SAMPLES / UNREADABLE / "WBS.md").read_bytes()
    with pytest.raises(UnicodeDecodeError) as err:
        data.decode("utf-8")
    assert err.value.start == MARSH_BAD_OFFSET
    assert data[MARSH_BAD_OFFSET] == 0xE9
    text = data.decode("latin-1")
    assert len(parse_nodes(text)) >= 3


@pytest.mark.parametrize("site", sorted(READABLE))
def test_wbs_line_patterns_apply(site):
    lines = wbs_text(site).splitlines()
    assert not [line for line in lines if FENCE_RE.match(line)]
    node_lines = [m for line in lines if (m := NODE_RE.match(line))]
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
    assert has_cross_epic_dep(nodes)


def test_orchard_shapes():
    nodes = wbs(OWN_UNIT)
    ests = [n.meta["est"] for n in nodes if "est" in n.meta]
    for shape, pattern in EST_SHAPES.items():
        assert any(re.match(pattern, e) for e in ests), shape
    assert "priority:" not in wbs_text(OWN_UNIT)
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
    assert has_cross_epic_dep(nodes)
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
                assert not dep.startswith(tuple(f"{t}:" for t in PUBLIC_TOOLS)), (site, dep)


# --- harbor's estimates -------------------------------------------------------

ESTIMATES_RE = re.compile(r"^estimates-(\d{4}-\d{2}-\d{2}).*\.md$")
ORDER_EPIC = ["#", "P", "title", "effort", "hours", "LOC", "thinking", "checking", "tier", "flags", "note"]
ORDER_ADDENDUM = ["#", "title", "type", "effort", "hours", "LOC", "thinking", "checking", "tier", "flags", "note"]
TIERS = {"sonnet", "opus", "fable"}
BUCKETS = {"XS", "S", "M", "L", "XL"}
HOURS_RE = re.compile(r"^(\d+)-(\d+)$")
ROW_ID_RE = re.compile(r"^#(\d+)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")
GANTRY_SAMPLE = SAMPLES / "gantry.sample.toml"


@dataclass
class Table:
    heading: str
    level: int
    header: list
    rows: list
    start_line: int


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def md_tables(text):
    tables, heading, level, run = [], "", 0, []

    def close():
        if run:
            rows = [_cells(line) for _, line in run]
            body = [r for r in rows[1:] if not all(SEPARATOR_CELL_RE.match(c) for c in r)]
            tables.append(Table(heading, level, rows[0], body, run[0][0]))
            run.clear()

    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("|"):
            run.append((number, line))
            continue
        close()
        m = HEADING_RE.match(line)
        if m:
            heading, level = m.group(2), len(m.group(1))
    close()
    return tables


def estimates_files(site_dir):
    found = [p for p in Path(site_dir).iterdir() if p.is_file() and ESTIMATES_RE.match(p.name)]
    return sorted(found, key=lambda p: ESTIMATES_RE.match(p.name).group(1))


def estimates_file(site_dir):
    found = estimates_files(site_dir)
    assert len(found) >= 1, f"no estimates-YYYY-MM-DD*.md in {Path(site_dir).name}"
    return found[-1]


def estimates_date(path):
    return date.fromisoformat(ESTIMATES_RE.match(Path(path).name).group(1))


def id_tables():
    text = estimates_file(SAMPLES / BUSY).read_text(encoding="utf-8")
    return [t for t in md_tables(text) if t.header and t.header[0] == "#"]


def est_rows():
    rows = {}
    for table in id_tables():
        for row in table.rows:
            m = ROW_ID_RE.match(row[0])
            assert m, row
            key = f"Issue-{m.group(1)}"
            assert key not in rows, f"duplicate estimates row {key}"
            rows[key] = dict(zip(table.header, row))
    return rows


def _find_key(node, key):
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for value in node.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    return None


def as_of_violation(sample_toml, est_date):
    cfg = load_config(sample_toml)
    as_of = _find_key(cfg, "as_of")
    if as_of is None:
        return f"{Path(sample_toml).name}: no as_of key"
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    elif isinstance(as_of, str):
        try:
            as_of = date.fromisoformat(as_of)
        except ValueError:
            return f"{Path(sample_toml).name}: as_of {as_of!r} is not an ISO date"
    if not isinstance(as_of, date):
        return f"{Path(sample_toml).name}: as_of {as_of!r} is not a date"
    if as_of < est_date:
        return f"{Path(sample_toml).name}: as_of {as_of} is before the estimates date {est_date}"
    return None


def harbor_issue_nodes():
    return {n.meta["issue"]: n for n in wbs(BUSY) if "issue" in n.meta}


def test_estimates_file_name_and_date():
    found = estimates_files(SAMPLES / BUSY)
    assert found
    for path in found:
        estimates_date(path)


def test_estimates_table_structure():
    tables = md_tables(estimates_file(SAMPLES / BUSY).read_text(encoding="utf-8"))
    headed = [t for t in tables if t.header[0] == "#"]
    assert len(headed) >= 2
    other = [t for t in tables if t.header[0] != "#"]
    assert other and other[0].start_line < headed[0].start_line
    assert all(t.header in (ORDER_EPIC, ORDER_ADDENDUM) for t in headed)
    assert any(t.header == ORDER_EPIC for t in headed)
    assert any(t.header == ORDER_ADDENDUM for t in headed)
    for t in headed:
        assert t.level == 3, t.heading
        if t.header == ORDER_ADDENDUM:
            assert "addendum" in t.heading.lower(), t.heading
        for row in t.rows:
            assert len(row) == len(t.header), row
            assert ROW_ID_RE.match(row[0]), row


def test_estimates_rows_name_harbor_nodes():
    nodes = harbor_issue_nodes()
    assert set(est_rows()) <= set(nodes)


def test_every_open_harbor_issue_leaf_has_row():
    open_leaves = {n.meta["issue"] for n in issue_leaves(wbs(BUSY)) if n.is_open}
    assert open_leaves
    assert open_leaves <= set(est_rows())


def test_estimates_epic_and_finished_rows():
    nodes = harbor_issue_nodes()
    rows = est_rows()
    assert any(nodes[i].is_epic for i in rows)
    assert any(nodes[i].is_leaf and nodes[i].glyph == FINISHED for i in rows)


def test_estimates_cells():
    rows = est_rows()
    used = set()
    for issue, row in sorted(rows.items()):
        assert row["effort"] in BUCKETS, issue
        m = HOURS_RE.match(row["hours"])
        assert m and int(m.group(1)) <= int(m.group(2)), issue
        assert row["thinking"] in TIERS and row["checking"] in TIERS, issue
        used |= {row["thinking"], row["checking"]}
    assert {"sonnet", "fable"} <= used


def test_estimates_flag_rows():
    nodes = harbor_issue_nodes()
    rows = est_rows()
    flags = {issue: set(row["flags"].split()) for issue, row in rows.items()}
    for wanted in ({"research"}, {"spike"}):
        matches = [i for i, f in flags.items() if f == wanted]
        assert matches, wanted
        for issue in matches:
            assert rows[issue]["checking"] == "fable", issue
            node = nodes[issue]
            assert node.is_leaf and not node.is_epic and node.is_open, issue
    assert any(f == {"research", "spike"} for f in flags.values())


def test_harbor_qw_leaves_have_rows():
    rows = est_rows()
    qw = [n for n in wbs(BUSY) if n.is_leaf and n.is_open and n.meta.get("est") == "?w"]
    assert sum(n.meta.get("issue") in rows for n in qw) >= 2


def test_as_of_not_before_estimates():
    if not GANTRY_SAMPLE.exists():
        pytest.skip("samples/gantry.sample.toml is #7's; absent")
    est_date = estimates_date(estimates_file(SAMPLES / BUSY))
    assert as_of_violation(GANTRY_SAMPLE, est_date) is None


AS_OF_PLANTS = {
    "before": ("as_of = 2026-01-14\n", True),
    "same": ("as_of = 2026-01-15\n", False),
    "after": ("as_of = 2026-02-01\n", False),
    "missing": ('name = "sample"\n', True),
    "not_iso": ('as_of = "15/01/2026"\n', True),
    "not_date": ("as_of = 20260115\n", True),
}


@pytest.mark.parametrize("case", sorted(AS_OF_PLANTS))
def test_as_of_check_planted(tmp_path, case):
    text, violates = AS_OF_PLANTS[case]
    sample = tmp_path / "gantry.sample.toml"
    sample.write_bytes(text.encode("ascii"))
    result = as_of_violation(sample, date(2026, 1, 15))
    assert (result is not None) == violates, result


# --- sample file list, bytes, attributes and path forms -----------------------

GITATTRIBUTES = ROOT / ".gitattributes"
GITIGNORE = ROOT / ".gitignore"
MARSH_PATH = "samples/marsh/WBS.md"
MARSH_OVERRIDE = MARSH_PATH + " -text"
LF_LINES = [
    "samples/**/*.md text eol=lf",
    "samples/**/*.toml text eol=lf",
    "samples/**/*.json text eol=lf",
]
ISSUE_FILES = [
    "samples/README.md",
    "samples/devagent/config.toml",
    "samples/harbor/WBS.md",
    "samples/orchard/WBS.md",
    "samples/quarry/WBS.md",
    "samples/lighthouse/WBS.md",
    "samples/marsh/WBS.md",
    "samples/bakery/README.md",
    "samples/harbor/estimates-2026-01-15-effort-and-model-tiers.md",
]

HAVE_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAVE_GIT, reason="git not on PATH")


def git_env(hermetic=False):
    """The environment without GIT_*; hermetic also drops global and system config."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if hermetic:
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def git(root, *args, input=None, hermetic=False):
    return subprocess.run(
        ["git", *args], cwd=root, input=input, capture_output=True, env=git_env(hermetic)
    )


# Cached: a root's git-ness does not change once probed, and each probe spawns git.
@functools.cache
def git_work_tree(root):
    if not HAVE_GIT:
        return False
    result = git(root, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        return False
    return os.path.samefile(decode_bytes(result.stdout).strip(), root)


def sample_files(root, hermetic=False):
    root = Path(root)
    if git_work_tree(root):
        result = git(
            root, "ls-files", "-z", "--cached", "--others", "--exclude-standard",
            "--", "samples/", hermetic=hermetic,
        )
        assert result.returncode == 0, decode_bytes(result.stderr)
        return sorted(p for p in decode_bytes(result.stdout).split("\0") if p)
    samples = root / "samples"
    return sorted(
        p.relative_to(root).as_posix()
        for p in samples.rglob("*")
        if p.is_file() and "out" not in p.relative_to(samples).parts[:-1]
    )


def cr_offenders(root, files):
    return [f for f in files if b"\r" in (Path(root) / f).read_bytes()]


def bom_offenders(root, files):
    return [f for f in files if (Path(root) / f).read_bytes().startswith(b"\xef\xbb\xbf")]


def path_form_offenders(root, files):
    return [
        f for f in files
        if path_form_hits((Path(root) / f).read_bytes().decode("latin-1"))
    ]


def unpinned(root, files, hermetic=False):
    stdin = "".join(f + "\0" for f in files).encode("utf-8", "surrogateescape")
    result = git(root, "check-attr", "-z", "--stdin", "eol", "text", input=stdin, hermetic=hermetic)
    assert result.returncode == 0, decode_bytes(result.stderr)
    fields = decode_bytes(result.stdout).split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    triples = [fields[i:i + 3] for i in range(0, len(fields), 3)]
    assert len(triples) == 2 * len(files), "check-attr did not report both attributes per file"
    attrs = {}
    for path, attr, value in triples:
        attrs.setdefault(path, {})[attr] = value
    return [
        f for f in files
        if attrs[f].get("eol") != "lf" and attrs[f].get("text") != "unset"
    ]


def _gitglob_re(pattern):
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    prefix = "" if "/" in pattern.rstrip("/") else "(?:.*/)?"
    return re.compile(prefix + out + "$")


def test_gitattributes_lines():
    lines = GITATTRIBUTES.read_text(encoding="utf-8").splitlines()
    for line in LF_LINES:
        assert line in lines, line
    assert MARSH_OVERRIDE in lines
    after = lines[lines.index(MARSH_OVERRIDE) + 1:]
    patterns = [l.split()[0] for l in after if l.strip() and not l.lstrip().startswith("#")]
    assert not [p for p in patterns if _gitglob_re(p).match(MARSH_PATH)], patterns


def test_sample_file_list_floor():
    files = sample_files(ROOT)
    assert set(ISSUE_FILES) <= set(files), sorted(set(ISSUE_FILES) - set(files))
    assert not [f for f in files if "out" in f.split("/")[:-1]]


def test_no_carriage_returns():
    assert cr_offenders(ROOT, sample_files(ROOT)) == []


def test_no_bom():
    assert bom_offenders(ROOT, sample_files(ROOT)) == []


def test_no_path_forms_in_samples():
    assert path_form_offenders(ROOT, sample_files(ROOT)) == []


# One positive control per form, built so this source holds none of them;
# tests/test_source_scan.py and tests/test_commit_check.py reuse the list.
PATH_FORM_CONTROLS = [
    ("drive_backslash", "D" + ":" + "\\" + "work"),
    ("drive_slash", "D" + ":" + "/" + "work"),
    ("unc", "\\" * 2 + "server" + "\\" + "share"),
    ("unc", "/" * 2 + "server" + "/" + "share"),
    ("posix_home", "/" + "home" + "/x"),
    ("posix_home", "/" + "Users" + "/x"),
    ("posix_home", "/" + "root" + "/x"),
    ("posix_home", "/" + "mnt" + "/x"),
    ("msys_drive", "/" + "c" + "/x"),
    ("tilde", "~" + "/x"),
    ("tilde", "~" + "someone" + "/x"),
    ("file_url", "file:" + "/" * 3 + "srv" + "/x"),
    ("file_url", "file:" + "/" * 2 + "srv" + "/share" + "/x"),
]


def test_path_form_controls_cover_every_form():
    assert {form for form, _ in PATH_FORM_CONTROLS} == set(PATH_FORMS)
    assert len(PATH_FORMS) == 7


@pytest.mark.parametrize("shape", ["alone", "quoted"])
@pytest.mark.parametrize("form,sample", PATH_FORM_CONTROLS)
def test_path_form_positive_controls(form, sample, shape):
    text = sample if shape == "alone" else 'path = "' + sample + '"'
    assert form in {f for f, _ in path_form_hits(text)}, (text, path_form_hits(text))


# tests/test_commit_check.py reuses the list.
PATH_FORM_NEGATIVES = [
    "https://github.com/sample_owner/harbor",
    "../harbor",
    "samples/devagent/out/",
    "samples/c/x",
    "origin/main",
    "and/or",
    "- [~] Erect crane one {issue: Issue-6}",
    "est: ?w",
    "6-12",
    "2026-01-15",
    "P1:",
]


@pytest.mark.parametrize("text", PATH_FORM_NEGATIVES)
def test_path_form_negative_controls(text):
    assert path_form_hits(text) == []


def test_test_sources_hold_no_path_form():
    for name in ("test_samples.py", "sample_roles.py"):
        text = (ROOT / "tests" / name).read_bytes().decode("utf-8")
        assert path_form_hits(text) == [], name


@needs_git
def test_attrs_pin_every_sample_file():
    if not git_work_tree(ROOT):
        pytest.skip("the gantry root is not a git work tree")
    assert unpinned(ROOT, sample_files(ROOT)) == []


def _temp_tree(tmp_path, with_git):
    tree = tmp_path / "tree"
    shutil.copytree(SAMPLES, tree / "samples", ignore=shutil.ignore_patterns("out"))
    shutil.copyfile(GITIGNORE, tree / ".gitignore")
    shutil.copyfile(GITATTRIBUTES, tree / ".gitattributes")
    if with_git:
        result = git(tree, "init", "-q", hermetic=True)
        assert result.returncode == 0, decode_bytes(result.stderr)
    return tree


PLANTED_BYTES = b"\xef\xbb\xbfx\r\n" + ("D" + ":" + "\\" + "work").encode("ascii") + b"\n"


@pytest.mark.parametrize("mode", [pytest.param("git", marks=needs_git), "nogit"])
def test_out_folder_excluded(tmp_path, mode):
    with_git = mode == "git"
    tree = _temp_tree(tmp_path, with_git)
    assert git_work_tree(tree) == with_git
    (tree / "samples/harbor/out").mkdir()
    (tree / "samples/harbor/out/planted.txt").write_bytes(PLANTED_BYTES)
    (tree / "samples/harbor/out.md").write_bytes(b"# Clean\n")
    files = sample_files(tree, hermetic=True)
    assert "samples/harbor/out/planted.txt" not in files
    assert "samples/harbor/out.md" in files
    assert cr_offenders(tree, files) == []
    assert bom_offenders(tree, files) == []
    assert path_form_offenders(tree, files) == []
    if with_git:
        assert unpinned(tree, files, hermetic=True) == []

    planted = "samples/harbor/planted.txt"
    (tree / planted).write_bytes(PLANTED_BYTES)
    files = sample_files(tree, hermetic=True)
    assert planted in files
    assert planted in cr_offenders(tree, files)
    assert planted in bom_offenders(tree, files)
    assert planted in path_form_offenders(tree, files)
    if with_git:
        assert planted in unpinned(tree, files, hermetic=True)


@needs_git
def test_attr_check_names_unpinned_stuck(tmp_path):
    tree = _temp_tree(tmp_path, with_git=True)
    (tree / "samples/harbor/STUCK").write_bytes(b"x\n")
    files = sample_files(tree, hermetic=True)
    assert unpinned(tree, files, hermetic=True) == ["samples/harbor/STUCK"]

    attrs = tree / ".gitattributes"
    before = attrs.read_bytes().decode("utf-8")
    # Line-wise, keeping the line's own ending: an autocrlf checkout makes
    # .gitattributes CRLF, since no attribute line covers it.
    lines = before.splitlines(keepends=True)
    at = next(i for i, line in enumerate(lines) if line.rstrip("\r\n") == MARSH_OVERRIDE)
    ending = lines[at][len(MARSH_OVERRIDE):] or "\n"
    lines.insert(at, "samples/**/STUCK text eol=lf" + ending)
    after = "".join(lines)
    assert after != before, "the STUCK line was not inserted"
    attrs.write_bytes(after.encode("utf-8"))
    assert unpinned(tree, sample_files(tree, hermetic=True), hermetic=True) == []
    marsh = git(tree, "check-attr", "text", "--", MARSH_PATH, hermetic=True)
    assert decode_bytes(marsh.stdout).strip().endswith("text: unset")


# --- samples/README.md --------------------------------------------------------

README = SAMPLES / "README.md"
CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")


def test_readme_hostile_title_span():
    spans = [s for s in CODE_SPAN_RE.findall(README.read_text(encoding="utf-8")) if "<b>" in s]
    assert spans == [HOSTILE_TITLE]


def test_readme_states_rules():
    text = README.read_text(encoding="utf-8")
    wanted = ["fictional", "tests/sample_roles.py", "out/", "eol=lf", "-text", "estimates"]
    wanted += [BUSY, OWN_UNIT, UNESTIMATED, SMALL, NO_WBS, UNREADABLE]
    wanted += [f"`{name}`" for name in sorted(RESERVED_FOLDERS)]
    assert [w for w in wanted if w not in text] == []
