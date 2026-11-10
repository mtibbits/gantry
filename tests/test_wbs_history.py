"""The WBS.md history reader (#28): the line scanner, the repository rule,
the walk, committed text and the uncommitted check.

Every fixture repository is built under tmp_path with pinned identities and
dates (tests/helpers_history.py); every path is built at run time.
"""

import ast
import os
import random
import re
import time
from pathlib import Path, PurePath

import pytest

from gantry import roots as roots_module
from gantry import wbs_history
from gantry.roots import directory_roots
from gantry.wbs_history import decode_version, scan_nodes, split_meta, state_changes
from helpers_gate import gate_run
from helpers_history import CI_SUFFIX, GIT_MISSING, make_repo, spy_git

ROOT = Path(__file__).resolve().parents[1]
BS = "\\"
# Floors for the git gate's end-to-end run, set below the real counts.
GATED_MODULES = ["tests/test_wbs_history.py"]
GATED_TESTS = 9
UNGATED_TESTS = 30

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


# --- the isolated git helper and the repository rule (Task 3) -----------------------


def norm(path):
    return os.path.normcase(os.path.realpath(str(path)))


def repo_below(tmp_path, *parts):
    """A fixture repository with an empty planning directory `parts` below its top."""
    top = make_repo(tmp_path / "planning")
    directory = top.joinpath(*parts)
    directory.mkdir(parents=True, exist_ok=True)
    return top, directory


def find(roots, path, gantry, site="harbor"):
    return wbs_history.history_repository(roots, path, gantry, key="devdoc_dir", site=site)


def fixture_checkout(tmp_path):
    """A fixture repository standing in for the gantry checkout."""
    return PurePath(os.path.realpath(make_repo(tmp_path / "gantry-checkout")))


def test_git_env_strips_every_git_variable():
    environ = {"GIT_DIR": "x", "GIT_INDEX_FILE": "x", "git_work_tree": "x", "GIT_DEFAULT_HASH": "sha256",
               "PATH": "p", "HOME": "h"}
    env = wbs_history.git_env(PurePath("ceiling"), environ)
    assert {k for k in env if k.upper().startswith("GIT_")} == {
        "GIT_CEILING_DIRECTORIES", "GIT_NO_LAZY_FETCH", "GIT_TERMINAL_PROMPT"}
    assert env["GIT_CEILING_DIRECTORIES"] == "ceiling"
    assert (env["GIT_NO_LAZY_FETCH"], env["GIT_TERMINAL_PROMPT"]) == ("1", "0")
    assert (env["PATH"], env["HOME"], env["LC_ALL"]) == ("p", "h", "C")


REFUSED = [["log"], ["show", "HEAD"], ["hash-object", "-w", "--", "x"], ["hash-object", "--no-filters", "--", "x"],
           ["hash-object", "--stdin"], ["cat-file", "--batch"], ["cat-file", "--batch-check"],
           ["cat-file", "--textconv", "HEAD:x"], ["cat-file", "--filters", "HEAD:x"], ["cat-file", "-p", "HEAD"],
           ["rev-list", "--all"], ["rev-parse", "--git-dir"], []]


@pytest.mark.parametrize("args", REFUSED, ids=lambda a: " ".join(a) or "empty")
def test_run_git_refuses_porcelain(args, tmp_path, monkeypatch):
    def no_child(*a, **k):
        raise AssertionError("no child process may start")

    monkeypatch.setattr(wbs_history.subprocess, "run", no_child)
    with pytest.raises(wbs_history.GitRefused):
        wbs_history.run_git(args, cwd=tmp_path, ceiling=tmp_path)


def test_gantry_root_is_the_package_parent():
    assert wbs_history.gantry_root is roots_module.gantry_root
    assert norm(wbs_history.gantry_root()) == norm(Path(wbs_history.__file__).resolve().parent.parent)


def test_planning_directory_below_top_level_is_accepted(tmp_path, git_ready):
    top, planning = repo_below(tmp_path, "plans", "harbor")
    repo = find(directory_roots([tmp_path]), planning, fixture_checkout(tmp_path))
    assert isinstance(repo, wbs_history.Repo), repo
    assert repo.prefix == "plans/harbor"
    assert repo.site == "harbor"


def test_toplevel_equals_resolved_path(tmp_path, git_ready):
    top, planning = repo_below(tmp_path, "plans")
    repo = find(directory_roots([tmp_path]), planning, fixture_checkout(tmp_path))
    assert os.path.normcase(str(repo.top)) == norm(top)


def test_missing_history_directory_makes_no_git_call(tmp_path, monkeypatch, git_ready):
    calls = spy_git(monkeypatch)
    roots = directory_roots([tmp_path])
    gantry = fixture_checkout(tmp_path)
    (tmp_path / "a-file").write_bytes(b"x")
    for path in (tmp_path / "nope", tmp_path / "a-file", tmp_path / "a-file" / "below", None):
        assert find(roots, path, gantry) == wbs_history.NoData(wbs_history.DIR_NOT_FOUND), path
    assert calls == []
    _, planning = repo_below(tmp_path, "plans")
    assert isinstance(find(roots, planning, gantry), wbs_history.Repo)
    assert len(calls) == 1


def test_history_outside_the_roots_is_refused_before_git(tmp_path, monkeypatch, git_ready):
    calls = spy_git(monkeypatch)
    _, planning = repo_below(tmp_path, "plans")
    got = find(directory_roots([tmp_path / "elsewhere"]), planning, fixture_checkout(tmp_path))
    assert got.text == "no data: history directory is outside the configured roots"
    assert calls == []


def test_history_at_or_above_gantry_root_is_refused_before_git(tmp_path, monkeypatch, git_ready):
    gantry = fixture_checkout(tmp_path)
    calls = spy_git(monkeypatch)
    roots = directory_roots([tmp_path])
    for path in (gantry, gantry.parent, Path(str(gantry)) / "sub" / ".."):
        got = find(roots, path, gantry)
        assert got.text == "no data: history would come from Gantry's own repository", path
    assert calls == []


def test_repository_holding_gantry_root_is_refused(tmp_path, monkeypatch, git_ready):
    outer = make_repo(tmp_path / "outer")
    gantry = PurePath(os.path.realpath(make_repo(outer / "inner" / "gantry-checkout")))
    (outer / "plans").mkdir()
    calls = spy_git(monkeypatch)
    got = find(directory_roots([tmp_path]), outer / "plans", gantry)
    assert got.text == "no data: history would come from Gantry's own repository"
    assert len(calls) == 1


def test_gantry_root_need_not_be_a_work_tree(tmp_path, git_ready):
    a = make_repo(tmp_path / "a")
    gantry = a / "plain-gantry"
    gantry.mkdir()
    b = make_repo(tmp_path / "b")
    repo = find(directory_roots([tmp_path]), b, PurePath(os.path.realpath(gantry)))
    assert isinstance(repo, wbs_history.Repo), repo
    assert repo.prefix == ""


def test_discovery_runs_under_the_ceiling(tmp_path, monkeypatch, git_ready):
    gantry = fixture_checkout(tmp_path)
    calls = spy_git(monkeypatch)
    _, planning = repo_below(tmp_path, "plans")
    plain = Path(str(gantry)) / "plain"
    plain.mkdir()
    find(directory_roots([tmp_path]), planning, gantry)
    find(directory_roots([tmp_path]), plain, gantry)
    assert len(calls) == 2 and all(ceiling == gantry for _, _, ceiling in calls)
    assert calls[0][0] == ["rev-parse", "--show-toplevel", "--show-prefix"]


def test_in_no_repository_below_the_ceiling(tmp_path, git_ready):
    gantry = fixture_checkout(tmp_path)
    plain = Path(str(gantry)) / "samples" / "quarry"
    plain.mkdir(parents=True)
    got = find(directory_roots([tmp_path]), plain, gantry)
    assert got == wbs_history.NoData(wbs_history.NO_REPOSITORY)
    assert got.text == "no data: in no repository"


def test_without_git_reads_git_not_found(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    planning = tmp_path / "plans"
    planning.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    got = find(directory_roots([tmp_path]), planning, PurePath(os.path.realpath(tmp_path / "g")))
    assert got.text == "no data: git not found"


def test_clean_removes_absolute_paths():
    drive = "D" + ":"
    known = drive + BS + "work" + BS + "plans"
    cases = {
        f"fatal: '{drive}/a b/c' is bad": "fatal: 'harbor' is bad",
        "fatal: '/srv/a b' is bad": "fatal: 'harbor' is bad",
        f"fatal: cannot change to {drive}{BS}x{BS}y: no": "fatal: cannot change to harbor",
        "error: /srv/repo/.git is broken": "error: harbor",
        "fatal: not a git repository: /a b/.git": "fatal: not a git repository: harbor",
        f"error: {known}{BS}sub near .git/objects/ab/cd": f"error: harbor{BS}sub near .git/objects/ab/cd",
        "error: " + known.replace(BS, "/") + "/x": "error: harbor/x",
        "error: object file .git/objects/ab/cd is empty": "error: object file .git/objects/ab/cd is empty",
    }
    assert {m: wbs_history.clean(m, [known], "harbor") for m in cases} == cases


@pytest.mark.parametrize("ci", [None, "", "true"], ids=["unset", "empty", "true"])
def test_git_gate_end_to_end(tmp_path, ci):
    code, cases = gate_run(tmp_path, ci, module=GATED_MODULES)
    passed = [kind for kind, _ in cases].count("passed")
    others = [(kind, message) for kind, message in cases if kind != "passed"]
    if ci:
        assert all(kind in ("failure", "error") and GIT_MISSING + CI_SUFFIX in message
                   for kind, message in others), others
        assert code == 1
    else:
        assert all(kind == "skipped" and message == GIT_MISSING for kind, message in others), others
        assert code == 0
    assert len(others) >= GATED_TESTS
    assert passed >= UNGATED_TESTS
