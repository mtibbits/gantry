"""No new test reads site data through the stopgap parser (#84).

The unit tests read site data from portfolio documents (tests/helpers_portfolio.py:
hp.site, hp.fixture_site, hp.sample_site). gantry/wbs_stopgap.py stays until #12
switches the build; until then the tests below hold every use of it under tests/
to ALLOWED, each entry with its reason. #12 deletes this file with the stopgap.

A use is one of two things in a test file's syntax tree:
  1. an identifier named parse_wbs, read_wbs or wbs_stopgap: a name, an
     attribute, an import (module or alias) or a def;
  2. a string, or the literal head of an f-string, that is a direct argument of
     a call and holds one of those names as a dotted part: the module or
     anything in it (importlib.import_module, __import__), or a patch target
     (monkeypatch.setattr(build, "read_wbs", ...), mock.patch("gantry.build.read_wbs"),
     getattr(build, "read_wbs")).
A comment, a docstring, or a string anywhere else (a set, a path) is not a use.
LIMIT: a name assembled at run time (f"{pkg}.wbs_stopgap", a variable) is not
seen; CONTROLS pins that. Nor is a name in a string that is not a direct call
argument, as in sys.modules["gantry.wbs_stopgap"] or __import__("gantry",
fromlist=["wbs_stopgap"]).

The scan walks the files on disk, untracked ones included, so a new test is
caught before it is committed (tests/test_confine_scan.py scans the same way).
"""

import ast
import functools
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
NAMES = frozenset({"parse_wbs", "read_wbs", "wbs_stopgap"})
SKIPPED_PARTS = frozenset({"node_modules", "__pycache__"})
# git ls-files tests '*.py' at the branch base (72), plus test_site_builder.py and this file.
TRACKED_FLOOR = 74


def _names_stopgap(dotted):
    return dotted is not None and any(part in NAMES for part in dotted.split("."))


def _literal_head(arg):
    """A call argument's literal text: the string, or an f-string's text before its first field."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.JoinedStr) and arg.values:
        head = arg.values[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value
    return None


def _owners(tree):
    """[(first line, last line, owner)] for each top-level def and class."""
    spans = []
    for item in tree.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = min([item.lineno] + [d.lineno for d in item.decorator_list])
            spans.append((first, item.end_lineno, item.name))
    return spans


def stopgap_hits(source) -> list[tuple[int, str, str]]:
    """(line, what, owner) for every use of the stopgap in `source`, in line order.

    owner is the top-level def (or class) whose span, from its first decorator,
    holds the use, else "<module>". The guard, its controls and the pins on real
    files all call this one predicate.
    """
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in NAMES:
            found.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute) and node.attr in NAMES:
            found.append((node.lineno, node.attr))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in NAMES:
            found.append((node.lineno, node.name))
        elif isinstance(node, ast.ImportFrom) and _names_stopgap(node.module):
            found.append((node.lineno, node.module))
        elif isinstance(node, ast.alias):
            for name in (node.name, node.asname):
                if _names_stopgap(name):
                    found.append((node.lineno, name))
        elif isinstance(node, ast.Call):
            for arg in [*node.args, *(keyword.value for keyword in node.keywords)]:
                head = _literal_head(arg)
                if _names_stopgap(head):
                    found.append((arg.lineno, head))
    spans = _owners(tree)
    hits = []
    for line, what in found:
        owner = next((name for first, last, name in spans if first <= line <= last), "<module>")
        hits.append((line, what, owner))
    return sorted(hits)


def scanned_files() -> list[Path]:
    """Every .py file under tests/ on disk, outside node_modules and __pycache__."""
    return sorted(path for path in TESTS.rglob("*.py") if not SKIPPED_PARTS & set(path.relative_to(TESTS).parts))


@functools.cache
def tree_hits() -> tuple[tuple[str, int, str, str], ...]:
    """(path relative to tests/, line, what, owner) over scanned_files(), once per session.

    Prefilter: every use spells one of NAMES (gantry.wbs_stopgap holds wbs_stopgap), so a
    file holding none of them is not parsed."""
    hits = []
    for path in scanned_files():
        data = path.read_bytes()
        if not any(name.encode() in data for name in NAMES):
            continue
        rel = path.relative_to(TESTS).as_posix()
        hits += [(rel, *hit) for hit in stopgap_hits(data.decode("utf-8"))]
    return tuple(hits)


ALLOWED = {  # (path relative to tests/, owner or "*"): why
    ("test_wbs_stopgap.py", "*"): "the parser's own tests; #12 deletes them with it",
    ("test_portfolio_json.py", "*"): "#83's reader tests compare against the stopgap's texts",
    ("test_build.py", "test_parse_error_isolated"): "monkeypatches build.read_wbs; moves with #12",
    ("test_build.py", "test_site_mode_parse_error_is_one_line"): "monkeypatches build.read_wbs; moves with #12",
    ("test_worksite_page.py", "assert_quarry_lists_bad_h"): "the demo-build cases' helper; moves with #12",
    ("test_priority.py", "test_p0_not_reported_p5_p_minus_1_are"): "stopgap-path smoke: bad_priority from raw text",
    ("test_inputs.py", "test_est_fixture_totals"): "stopgap-path smoke: est.md totals",
    ("test_attention.py", "test_h_text_forms"): "stopgap-path smoke: the quoted h: forms",
    ("test_site_builder.py", "test_fixture_sites_agree_with_the_stopgap"): "the agreement check; #12 deletes it",
    ("test_portfolio_equivalence.py", "stopgap_snapshot"):
        "#83's stopgap-vs-reader comparison (AC1 amended); #12 deletes it",
    ("test_fires.py", "test_p0_report_problems_fail_closed"): "stopgap reading semantics (AC1 amended); #12 restates",
    ("test_fires.py", "test_wbs_with_no_node_lines_fails_closed"): "same",
    ("test_fires.py", "test_stuck_marker_behind_an_unread_line_fails_closed"): "same",
    ("test_fires.py", "test_unread_line_without_a_stray_marker_keeps_stuck_read"): "same",
    ("test_fires.py", "test_stuck_marker_on_a_parent_row_has_no_open_leaf"): "same",
    ("test_fires.py", "test_doc_p0_blind_shapes_are_examples"): "same, with docs/daily-screen.md",
}

# (source, expected hits): each is written here as a string assignment, never a call argument.
CONTROLS = (
    ("from gantry.wbs_stopgap import parse_wbs\n",
     [(1, "gantry.wbs_stopgap", "<module>"), (1, "parse_wbs", "<module>")]),
    ("import gantry.wbs_stopgap as w\n", [(1, "gantry.wbs_stopgap", "<module>")]),
    ("from gantry import wbs_stopgap\n", [(1, "wbs_stopgap", "<module>")]),
    ('importlib.import_module("gantry.wbs_stopgap")\n', [(1, "gantry.wbs_stopgap", "<module>")]),
    ('__import__(f"gantry.wbs_stopgap")\n', [(1, "gantry.wbs_stopgap", "<module>")]),
    ('def test_m(monkeypatch):\n    monkeypatch.setattr("gantry.wbs_stopgap.read_wbs", f)\n',
     [(2, "gantry.wbs_stopgap.read_wbs", "test_m")]),
    ("def test_x():\n    def inner():\n        return read_wbs(a, b)\n", [(3, "read_wbs", "test_x")]),
    ('x = f"{parse_wbs}"\n', [(1, "parse_wbs", "<module>")]),
    ("@pytest.mark.parametrize('a', [1])\ndef test_d(a):\n    parse_wbs(a)\n", [(3, "parse_wbs", "test_d")]),
    ("class TestK:\n    def test_k(self):\n        parse_wbs(1)\n", [(3, "parse_wbs", "TestK")]),
    ('def test_p(monkeypatch):\n    monkeypatch.setattr(build, "read_wbs", lambda *a: None)\n',
     [(2, "read_wbs", "test_p")]),
    ('@mock.patch("gantry.build.read_wbs")\ndef test_q(m):\n    pass\n', [(1, "gantry.build.read_wbs", "test_q")]),
    ('getattr(build, "parse_wbs")\n', [(1, "parse_wbs", "<module>")]),
    ('importlib.import_module(".wbs_stopgap", "gantry")\n', [(1, ".wbs_stopgap", "<module>")]),
    # Not uses.
    ("# parse_wbs\nx = 1\n", []),
    ('def f():\n    """Calls parse_wbs on the text."""\n', []),
    ('assert not {"gantry.wbs_stopgap", "gantry.model"} & seen\n', []),
    ('KEPT = ("gantry/wbs_stopgap.py",)\n', []),
    # LIMIT: a name assembled at run time is not seen.
    ('importlib.import_module(f"{pkg}.wbs_stopgap")\n', []),
)


def allowed(path, owner):
    return (path, owner) in ALLOWED or (path, "*") in ALLOWED


def test_no_stopgap_use_outside_the_allowlist():
    outside = [hit for hit in tree_hits() if not allowed(hit[0], hit[3])]
    assert outside == [], "stopgap uses outside ALLOWED; build the site with tests/helpers_portfolio.py:\n" + "\n".join(
        f"{path}:{line} {what} {owner}" for path, line, what, owner in outside)


def test_every_allowlist_entry_is_used():
    hits = tree_hits()
    used = {(path, owner) for path, _, _, owner in hits} | {(path, "*") for path, *_ in hits}
    assert sorted(set(ALLOWED) - used) == []


def test_guard_scans_the_test_tree():
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", "tests/*.py"],
                            capture_output=True, check=True).stdout.decode("utf-8").split("\0")
    tracked = {ROOT / name for name in listed if name and not SKIPPED_PARTS & set(Path(name).parts)}
    assert len(tracked) >= TRACKED_FLOOR
    scanned = set(scanned_files())
    assert tracked <= scanned
    assert {TESTS / path for path, _ in ALLOWED} <= scanned
    me = Path(__file__).resolve()
    assert me in scanned
    assert stopgap_hits(me.read_text(encoding="utf-8")) == []


def test_stopgap_hits_controls():
    for source, expected in CONTROLS:
        assert stopgap_hits(source) == expected, source


def test_known_non_uses_are_not_hits():
    for name in ("test_wbs_history.py", "test_confine_scan.py"):
        assert stopgap_hits((TESTS / name).read_text(encoding="utf-8")) == [], name
