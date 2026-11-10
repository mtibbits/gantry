"""The WBS.md history reader (#28): the line scanner, the repository rule,
the walk, committed text and the uncommitted check.

Every fixture repository is built under tmp_path with pinned identities and
dates (tests/helpers_history.py); every path is built at run time.
"""

import ast
import random
import re
import time
from pathlib import Path

from gantry import wbs_history
from gantry.wbs_history import decode_version, scan_nodes, split_meta, state_changes

ROOT = Path(__file__).resolve().parents[1]

# devagent's META_RE (scripts/lib/wbs-parser.py `_META_BLOCK_RE`), verbatim.
DEVAGENT_META_RE = re.compile(r"\s*\{(?P<body>[^{}]*)\}\s*$")


def issue(identity, n=1):
    return ("issue", identity, n)


def text(identity, n=1):
    return ("text", identity, n)


def changes(old, new):
    return state_changes(scan_nodes(old), scan_nodes(new))


# --- scan_nodes --------------------------------------------------------------------


def test_glyph_change_on_issue_leaf_is_a_change():
    old = "# T\n- [ ] Epic\n  - [ ] A {issue: Issue-7}\n"
    new = "# T\n- [ ] Epic\n  - [x] A {issue: Issue-7}\n"
    assert changes(old, new) == {issue("Issue-7")}


def test_glyph_change_on_parent_is_not():
    old = "- [ ] Epic {issue: Issue-1}\n  - [ ] A {issue: Issue-7}\n"
    new = "- [x] Epic {issue: Issue-1}\n  - [ ] A {issue: Issue-7}\n"
    assert issue("Issue-1") not in scan_nodes(old)
    assert changes(old, new) == set()


def test_quoted_and_unquoted_issue_are_one_identity():
    old = '- [ ] A {issue: "Issue-7", est: 2d}\n'
    new = "- [x] A renamed {issue: Issue-7}\n"
    assert set(scan_nodes(old)) == set(scan_nodes(new)) == {issue("Issue-7")}
    assert changes(old, new) == {issue("Issue-7")}
    assert set(scan_nodes("- [ ] A {issue: 'Issue-7'}\n")) == {issue("Issue-7")}


def test_reordered_block_is_not_a_change():
    old = "- [ ] A {issue: Issue-1}\n- [x] B {issue: Issue-2}\n- [ ] C\n"
    new = "- [ ] C\n- [x] B {issue: Issue-2}\n- [ ] A {issue: Issue-1}\n"
    assert scan_nodes(old) == scan_nodes(new)
    assert changes(old, new) == set()


def test_added_or_removed_line_is_not_a_change():
    old = "- [ ] A {issue: Issue-1}\n"
    new = "- [ ] A {issue: Issue-1}\n- [x] B {issue: Issue-2}\n"
    assert changes(old, new) == set()
    assert changes(new, old) == set()


def test_issueless_text_edit_reads_as_removed_and_added():
    old_nodes, new_nodes = scan_nodes("- [ ] Paint the shed\n"), scan_nodes("- [x] Paint the barn\n")
    assert set(old_nodes) == {text("Paint the shed")}
    assert set(new_nodes) == {text("Paint the barn")}
    assert state_changes(old_nodes, new_nodes) == set()


def test_node_in_fence_is_ignored():
    body = "```\n- [ ] Hidden {issue: Issue-9}\n```\n- [ ] Seen {issue: Issue-1}\n"
    assert set(scan_nodes(body)) == {issue("Issue-1")}
    assert set(scan_nodes(body.replace("```", "~~~~"))) == {issue("Issue-1")}


def test_node_after_unclosed_opener_is_read():
    body = "```\n- [ ] Read {issue: Issue-1}\n"
    assert set(scan_nodes(body)) == {issue("Issue-1")}
    # A closer of the other fence character never closes it.
    assert set(scan_nodes("```\n- [ ] A {issue: Issue-1}\n~~~\n")) == {issue("Issue-1")}


def test_backtick_info_with_backtick_is_not_an_opener():
    body = "```py`x\n- [ ] Read {issue: Issue-1}\n```\n- [ ] After {issue: Issue-2}\n"
    # The first line is no opener, so the ``` on line 3 opens a fence with no closer.
    assert set(scan_nodes(body)) == {issue("Issue-1"), issue("Issue-2")}


def test_shorter_closer_does_not_close():
    body = "````\n- [ ] Hidden {issue: Issue-9}\n```\n- [ ] Still hidden {issue: Issue-8}\n````\n- [ ] Seen\n"
    assert set(scan_nodes(body)) == {text("Seen")}
    # A closer with an info string does not close either.
    body = "```\n- [ ] Hidden\n``` x\n- [ ] Hidden too\n```\n- [ ] Seen\n"
    assert set(scan_nodes(body)) == {text("Seen")}


def test_one_space_deeper_is_two_leaves():
    old = "- [ ] A {issue: Issue-1}\n - [ ] B {issue: Issue-2}\n"
    new = "- [x] A {issue: Issue-1}\n - [ ] B {issue: Issue-2}\n"
    assert set(scan_nodes(old)) == {issue("Issue-1"), issue("Issue-2")}
    assert changes(old, new) == {issue("Issue-1")}


def test_tab_indent_is_one_level_deeper():
    body = "- [ ] A {issue: Issue-1}\n\t- [ ] B {issue: Issue-2}\n"
    assert set(scan_nodes(body)) == {issue("Issue-2")}


def test_duplicate_issue_keeps_two_identities():
    old = "- [ ] A {issue: Issue-7}\n- [ ] B {issue: Issue-7}\n"
    new = "- [ ] A {issue: Issue-7}\n- [x] B {issue: Issue-7}\n"
    assert set(scan_nodes(old)) == {issue("Issue-7"), issue("Issue-7", 2)}
    assert changes(old, new) == {issue("Issue-7", 2)}


def test_duplicate_text_keeps_two_identities():
    old = "- [ ] X\n- [ ] X\n"
    new = "- [ ] X\n- [x] X\n"
    assert set(scan_nodes(old)) == {text("X"), text("X", 2)}
    assert changes(old, new) == {text("X", 2)}


def test_text_and_issue_identities_never_collide():
    nodes = scan_nodes("- [ ] Issue-7 {est: 2d}\n- [x] B {issue: Issue-7}\n- [ ] X#2\n- [ ] X\n- [ ] X\n")
    assert nodes == {text("Issue-7"): " ", issue("Issue-7"): "x", text("X#2"): " ",
                     text("X"): " ", text("X", 2): " "}


def test_invalid_utf8_is_scanned():
    body = decode_version(b"- [ ] Caf\xe9 {issue: Issue-1}\n- [x] B\n")
    assert scan_nodes(body) == {issue("Issue-1"): " ", text("B"): "x"}


def test_crlf_to_lf_is_no_change():
    old = "- [ ] A {issue: Issue-1}\r\n  - [x] B\r\n"
    assert scan_nodes(old) == scan_nodes(old.replace("\r\n", "\n"))
    assert changes(old, old.replace("\r\n", "\n")) == set()


def test_meta_split_matches_devagent_regex():
    rng = random.Random(28)
    for _ in range(2000):
        s = "".join(rng.choice(" \t{}a:") for _ in range(rng.randint(0, 24)))
        m = DEVAGENT_META_RE.search(s)
        expected = None if m is None else (m.start(), m.group("body"))
        assert split_meta(s) == expected, repr(s)


def test_hostile_shapes_are_linear():
    spaces = " " * 30_000
    line = "- [ ] a" + spaces + "b"
    started = time.perf_counter()
    nodes = scan_nodes(line + "\n" + "- [x] c {" + spaces + "\n" + spaces + "\n")
    assert time.perf_counter() - started < 1
    assert nodes == {text("a" + spaces + "b"): " ", text("c {"): "x"}

    # Openers with an info string can never be closed: no pairing rescans the file.
    openers = "".join(("`" if i % 2 else "~") * (3 + i % 5) + "info\n" for i in range(20_000))
    started = time.perf_counter()
    nodes = scan_nodes(openers + "- [ ] tail\n")
    assert time.perf_counter() - started < 1
    assert nodes == {text("tail"): " "}


def _gantry_imports(path):
    tree = ast.parse(path.read_bytes().decode("utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return {name for name in found if name == "gantry" or name.startswith("gantry.")}


def test_module_never_imports_the_stopgap():
    package = ROOT / "gantry"
    seen, todo = set(), ["gantry.wbs_history"]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        path = package / (name.split(".", 1)[1].replace(".", "/") + ".py") if "." in name else None
        if path is None or not path.is_file():
            continue  # a name imported from a module, not a module
        seen.add(name)
        todo += sorted(_gantry_imports(path))
    assert "gantry.wbs_stopgap" not in seen
    assert {"gantry.wbs_history", "gantry.roots"} <= seen
    assert wbs_history.__name__ == "gantry.wbs_history"
