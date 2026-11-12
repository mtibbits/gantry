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
from datetime import date
from pathlib import Path, PurePath

import pytest

from gantry import roots as roots_module
from gantry import wbs_history
from gantry.roots import directory_roots
from gantry.sites import SiteEntry
from gantry.wbs_history import HistoryCache, decode_version, scan_nodes, split_meta, state_changes
from helpers_gate import gate_run
from helpers_history import (
    CI_SUFFIX,
    GIT_MISSING,
    branch,
    checkout,
    commit,
    corrupt,
    git,
    make_repo,
    merge,
    shallow_clone,
    site_config,
    spy_git,
    spy_repository,
    write,
)
from sample_roles import HISTORY_LESS, HISTORY_RECENT, HISTORY_STALE, HISTORY_YOUNG
from test_samples import GANTRY_SAMPLE, SAMPLES, git_work_tree
from test_samples import git as sample_git

ROOT = Path(__file__).resolve().parents[1]
BS = "\\"
# Floors for the git gate's end-to-end run, set below the real counts.
GATED_MODULES = ["tests/test_wbs_history.py", "tests/test_sample_history.py", "tests/test_demo_reads.py"]
GATED_TESTS = 80
UNGATED_TESTS = 95

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
    # gantry.model is #8's, and #12 changes its meta reading with the stopgap.
    assert not {"gantry.wbs_stopgap", "gantry.model"} & seen
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


# --- last_state_change: the walk (Task 4) ---------------------------------------------

AS_OF = date(2026, 1, 20)  # weeks 4: the window is 2025-12-24 to 2026-01-20
NO_COMMITS_TEXT = "no data: `WBS.md` has no commits on or before 2026-01-20"


def wbs(a=" ", b=" ", note=""):
    """A WBS.md version: two issue leaves under an epic, glyphs `a` and `b`."""
    return f"# Plan\n- [ ] Epic\n  - [{a}] A {{issue: Issue-1}}\n  - [{b}] B {{issue: Issue-2}}{note}\n"


def answer(tmp_path, history, *, as_of=AS_OF, weeks=4, cache=None, planning=None, sites=("harbor",)):
    """last_state_change for each site, every site's `history` naming `history`."""
    planning = wbs().encode() if planning is None else planning
    config, roots, found = site_config(tmp_path / "inst", {s: planning for s in sites},
                                       history={s: history for s in sites})
    cache = HistoryCache() if cache is None else cache
    got = [wbs_history.last_state_change(roots, found[s], config, as_of, weeks, cache) for s in sites]
    return got[0] if len(got) == 1 else got


def resolved_versions(calls):
    """The `<commit>:<path>` arguments of the spied rev-parse calls, in order."""
    return [a[-1] for a, _, _ in calls if a[0] == "rev-parse" and ":" in a[-1] and "^" not in a[-1]]


def blob_reads(calls):
    return [a[-1] for a, _, _ in calls if a[0] == "cat-file"]


def test_change_on_window_first_day_is_returned(tmp_path, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2025-12-01")
    commit(repo, "WBS.md", wbs("x"), "2025-12-24")
    got = answer(tmp_path, repo)
    assert (got.date, got.history_start, got.no_data) == (date(2025, 12, 24), False, None)


def test_change_in_newest_commit_before_window_is_returned(tmp_path, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2025-11-01")
    commit(repo, "WBS.md", wbs("x"), "2025-12-23")
    commit(repo, "WBS.md", wbs("x", note=" more"), "2026-01-05")
    got = answer(tmp_path, repo)
    assert (got.date, got.commits_read) == (date(2025, 12, 23), 2)


def test_change_behind_the_stop_commit_is_not_read(tmp_path, monkeypatch, git_ready):
    repo = make_repo(tmp_path / "plans")
    c0 = commit(repo, "WBS.md", wbs(), "2025-11-01")
    commit(repo, "WBS.md", wbs("x"), "2025-11-15")
    commit(repo, "WBS.md", wbs("x", note=" edited"), "2025-12-01")
    commit(repo, "WBS.md", wbs("x", note=" again"), "2026-01-05")
    calls = spy_git(monkeypatch)
    got = answer(tmp_path, repo)
    assert (got.date, got.commits_read, got.no_data) == (None, 2, None)
    assert f"{c0}:WBS.md" not in resolved_versions(calls)


def test_latest_date_wins_when_chain_order_differs(tmp_path, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2025-11-01")
    commit(repo, "WBS.md", wbs("x"), "2026-01-15")
    commit(repo, "WBS.md", wbs("x", "x"), "2026-01-02")
    assert answer(tmp_path, repo).date == date(2026, 1, 15)


def test_commit_after_as_of_is_skipped_and_older_one_read(tmp_path, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2025-11-01")
    commit(repo, "WBS.md", wbs("x"), "2026-01-10")
    commit(repo, "WBS.md", wbs("x", "x"), "2026-02-01")
    got = answer(tmp_path, repo)
    assert (got.date, got.commits_read) == (date(2026, 1, 10), 2)


def test_merge_counts_side_branch_change_at_the_merge(tmp_path, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2025-11-01")
    branch(repo, "side")
    checkout(repo, "side")
    commit(repo, "WBS.md", wbs("x"), "2026-01-18")
    checkout(repo, "main")
    commit(repo, "other.md", "x\n", "2026-01-05")
    merge(repo, "side", "2026-01-10")
    got = answer(tmp_path, repo)
    assert got.date == date(2026, 1, 10)


def test_date_is_in_the_commits_own_offset(tmp_path, monkeypatch, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2025-11-01")
    commit(repo, "WBS.md", wbs("x"), "2026-01-12T23:30:00-1100")
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    assert answer(tmp_path, repo).date == date(2026, 1, 12)


def test_history_start_inside_window(tmp_path, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "other.md", "x\n", "2025-10-01")
    commit(repo, "WBS.md", wbs(), "2026-01-05")
    commit(repo, "WBS.md", wbs(note=" edited"), "2026-01-10")
    got = answer(tmp_path, repo)
    assert (got.date, got.history_start, got.commits_read) == (date(2026, 1, 5), True, 2)


def test_history_start_is_false_when_a_change_followed(tmp_path, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2026-01-05")
    commit(repo, "WBS.md", wbs("x"), "2026-01-10")
    got = answer(tmp_path, repo)
    assert (got.date, got.history_start) == (date(2026, 1, 10), False)


def test_each_version_is_read_once_per_build(tmp_path, monkeypatch, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2025-11-01")
    commit(repo, "other.md", "a\n", "2025-11-20")
    commit(repo, "WBS.md", wbs("x"), "2025-12-01")
    commit(repo, "WBS.md", wbs("x", "x"), "2026-01-10")
    calls = spy_git(monkeypatch)
    cache = HistoryCache()
    first = answer(tmp_path, repo, cache=cache, sites=("harbor", "orchard"))
    assert [r.date for r in first] == [date(2026, 1, 10)] * 2
    versions, blobs = resolved_versions(calls), blob_reads(calls)
    assert versions and blobs
    assert len(versions) == len(set(versions)), versions
    assert len(blobs) == len(set(blobs)), blobs
    before = len(calls)
    config, roots, found = site_config(tmp_path / "again", {"harbor": wbs().encode()}, history={"harbor": repo})
    again = wbs_history.last_state_change(roots, found["harbor"], config, AS_OF, 4, cache)
    assert again.date == date(2026, 1, 10)
    assert resolved_versions(calls[before:]) == [] and blob_reads(calls[before:]) == []


def test_walk_stops_after_first_commit_before_window(tmp_path, monkeypatch, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2025-10-01")
    commit(repo, "other.md", "1\n", "2025-10-02")
    commit(repo, "WBS.md", wbs("x"), "2025-11-01")
    p_stop = commit(repo, "other.md", "2\n", "2025-11-02")
    stop = commit(repo, "WBS.md", wbs("x", "x"), "2025-12-01")
    p_new = commit(repo, "other.md", "3\n", "2025-12-02")
    new = commit(repo, "WBS.md", wbs("x", "x", " edited"), "2026-01-05")
    calls = spy_git(monkeypatch)
    got = answer(tmp_path, repo)
    assert got.commits_read == 2
    assert resolved_versions(calls) == [f"{c}:WBS.md" for c in (new, p_new, stop, p_stop)]


def _shallow_source(tmp_path):
    repo = make_repo(tmp_path / "source")
    commit(repo, "WBS.md", wbs(), "2025-10-01")
    commit(repo, "WBS.md", wbs("x"), "2025-11-01")
    commit(repo, "WBS.md", wbs("x", note=" edited"), "2025-12-01")
    commit(repo, "WBS.md", wbs("x", note=" again"), "2026-01-10")
    return repo


def test_shallow_clone_reads_shallow_history(tmp_path, git_ready):
    source = _shallow_source(tmp_path)
    one = shallow_clone(source, tmp_path / "one", depth=1)
    assert answer(tmp_path / "a", one).no_data.text == "no data: shallow history"
    # Depth 2: the parentless boundary is the stop commit, whose version before it is missing.
    two = shallow_clone(source, tmp_path / "two", depth=2)
    assert answer(tmp_path / "b", two).no_data.text == "no data: shallow history"
    # No commit counted (the only one is after as_of): shallow, not "no commits".
    got = answer(tmp_path / "c", one, as_of=date(2026, 1, 5))
    assert got.no_data.text == "no data: shallow history"


def test_full_clone_control_answers(tmp_path, git_ready):
    source = _shallow_source(tmp_path)
    full = tmp_path / "full"
    git(tmp_path, "clone", "-q", source.resolve().as_uri(), str(full))
    got = answer(tmp_path, full)
    assert (got.no_data, got.date, got.commits_read) == (None, None, 2)


def test_corrupt_commit_reads_git_failed(tmp_path, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2025-11-01")
    head = commit(repo, "WBS.md", wbs("x"), "2026-01-05")
    corrupt(repo, head)
    got = answer(tmp_path, repo)
    assert got.no_data.text.startswith("no data: git failed: "), got.no_data
    for form in (str(tmp_path), str(tmp_path).replace(BS, "/")):
        assert form not in got.no_data.text


def test_untracked_wbs_has_no_commits(tmp_path, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "other.md", "x\n", "2025-11-01")
    (repo / "WBS.md").write_bytes(wbs().encode())
    got = answer(tmp_path, repo)
    assert (got.no_data.text, got.uncommitted, got.uncommitted_reason) == (NO_COMMITS_TEXT, None, NO_COMMITS_TEXT)


def test_all_commits_after_as_of_has_no_commits(tmp_path, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2026-02-01")
    got = answer(tmp_path, repo)
    assert (got.no_data.text, got.uncommitted) == (NO_COMMITS_TEXT, None)


def test_unborn_head_has_no_commits(tmp_path, git_ready):
    repo = make_repo(tmp_path / "plans")
    assert answer(tmp_path, repo).no_data.text == NO_COMMITS_TEXT


def _case_missing(tmp_path, monkeypatch):
    return tmp_path / "nope", None


def _case_own(tmp_path, monkeypatch):
    return Path(str(wbs_history.gantry_root())), None


def _case_no_repository(tmp_path, monkeypatch):
    gantry = fixture_checkout(tmp_path)
    monkeypatch.setattr(wbs_history, "gantry_root", lambda: gantry)
    plain = Path(str(gantry)) / "plain"
    plain.mkdir()
    return plain, None


def _case_no_commits(tmp_path, monkeypatch):
    return make_repo(tmp_path / "plans"), None


def _case_shallow(tmp_path, monkeypatch):
    return shallow_clone(_shallow_source(tmp_path), tmp_path / "one", depth=1), None


def _case_git_failed(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "plans")
    corrupt(repo, commit(repo, "WBS.md", wbs(), "2025-11-01"))
    return repo, None


def _case_git_missing(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2025-11-01")
    empty = tmp_path / "empty"
    empty.mkdir()
    return repo, lambda: monkeypatch.setenv("PATH", str(empty))


NO_DATA_CASES = {
    "missing": (_case_missing, "no data: history directory not found"),
    "own": (_case_own, "no data: history would come from Gantry's own repository"),
    "no-repository": (_case_no_repository, "no data: in no repository"),
    "no-commits": (_case_no_commits, NO_COMMITS_TEXT),
    "shallow": (_case_shallow, "no data: shallow history"),
    "git-failed": (_case_git_failed, "no data: git failed: "),
    "git-missing": (_case_git_missing, "no data: git not found"),
}


@pytest.mark.parametrize("case", list(NO_DATA_CASES))
def test_no_data_cases_never_raise(tmp_path, monkeypatch, case, git_ready):
    make, expected = NO_DATA_CASES[case]
    history, before = make(tmp_path, monkeypatch)
    config, roots, found = site_config(tmp_path / "inst", {"harbor": wbs().encode()}, history={"harbor": history})
    given = spy_repository(monkeypatch)
    if before is not None:
        before()
    got = wbs_history.last_state_change(roots, found["harbor"], config, AS_OF, 4, HistoryCache())
    assert got.no_data.text.startswith(expected), got.no_data
    assert (got.date, got.history_start, got.uncommitted) == (None, False, None)
    assert got.uncommitted_reason == got.no_data.text
    assert len(given) == 1 and got.repo is given[0]


def test_site_without_planning_directory_never_raises(tmp_path, git_ready):
    config, roots, _ = site_config(tmp_path / "inst", {"harbor": wbs().encode()})
    for site in (SiteEntry("harbor", None, "no devdoc_dir"), SiteEntry("nul", PurePath("a\x00b"), None),
                 SiteEntry("unlisted", tmp_path / "nope", None)):
        got = wbs_history.last_state_change(roots, site, config, AS_OF, 4, HistoryCache())
        assert got.no_data is not None and got.date is None, site
    got = wbs_history.last_state_change(roots, SiteEntry("harbor", None, "x"), config, AS_OF, 4, HistoryCache())
    assert got.no_data.text == "no data: the site has no planning directory"


def test_answering_site_repo_is_what_history_repository_gave(tmp_path, monkeypatch, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2025-11-01")
    given = spy_repository(monkeypatch)
    got = answer(tmp_path, repo)
    assert isinstance(got.repo, wbs_history.Repo) and got.repo is given[0]


def test_history_dir_below_top_and_glob_characters(tmp_path, git_ready):
    repo = make_repo(tmp_path / "plans")
    for name in ("sites/harbor", "sites/h[ab]"):
        commit(repo, f"{name}/WBS.md", wbs(), "2025-11-01")
        commit(repo, f"{name}/WBS.md", wbs("x"), "2026-01-05")
    commit(repo, "sites/hb/WBS.md", wbs("x", "x"), "2026-01-15")  # matched only by a glob
    for name in ("harbor", "h[ab]"):
        got = answer(tmp_path / name.replace("[", "").replace("]", ""), repo / "sites" / name)
        assert (got.no_data, got.date) == (None, date(2026, 1, 5)), name
        assert got.repo.prefix == f"sites/{name}"


# --- committed_text and the uncommitted check (Task 5) ----------------------------------


def repo_for(tmp_path, directory):
    return find(directory_roots([tmp_path]), directory, fixture_checkout(tmp_path))


def test_committed_text_reads_head_relative_to_history_dir(tmp_path, git_ready):
    top, planning = repo_below(tmp_path, "plans", "harbor")
    commit(top, "plans/harbor/Issue-7/checklist.md", "- [x] 0. pull\n", "2026-01-05")
    commit(top, "Issue-7/checklist.md", "top-level decoy\n", "2026-01-06")
    repo = repo_for(tmp_path, planning)
    assert wbs_history.committed_text(repo, "Issue-7/checklist.md") == "- [x] 0. pull\n"


def test_committed_text_at_the_top_level(tmp_path, git_ready):
    top = make_repo(tmp_path / "planning")
    commit(top, "Issue-7/checklist.md", "caf\xe9\n".encode("latin-1"), "2026-01-05")
    repo = repo_for(tmp_path, top)
    assert repo.prefix == ""
    assert wbs_history.committed_text(repo, "Issue-7/checklist.md") == "caf�\n"


def test_committed_text_absent(tmp_path, monkeypatch, git_ready):
    top, planning = repo_below(tmp_path, "plans")
    commit(top, "plans/kept.md", "x\n", "2026-01-05")
    (planning / "working-only.md").write_bytes(b"x\n")
    repo = repo_for(tmp_path, planning)
    calls = spy_git(monkeypatch)
    for rel in ("missing.md", "working-only.md"):
        assert wbs_history.committed_text(repo, rel) == wbs_history.Absent(), rel
    assert len(calls) == 2
    for rel in (str(planning / "kept.md"), "/kept.md", BS + "kept.md", "C" + ":kept.md", "../kept.md",
                "a/../kept.md", "a" + BS + ".." + BS + "kept.md", "", "kept\n.md", "kept\0.md"):
        assert wbs_history.committed_text(repo, rel) == wbs_history.Absent(), rel
    assert len(calls) == 2
    assert wbs_history.committed_text(repo, "kept.md") == "x\n"


def test_committed_text_passes_nodata_through(tmp_path, monkeypatch, git_ready):
    gantry = fixture_checkout(tmp_path)
    plain = Path(str(gantry)) / "plain"
    plain.mkdir()
    roots = directory_roots([tmp_path])
    given = [find(roots, plain, gantry), find(roots, gantry, gantry), find(roots, tmp_path / "nope", gantry)]
    assert [g.reason for g in given] == [wbs_history.NO_REPOSITORY, wbs_history.OWN_REPOSITORY,
                                         wbs_history.DIR_NOT_FOUND]
    calls = spy_git(monkeypatch)
    for no_data in given:
        assert wbs_history.committed_text(no_data, "Issue-7/checklist.md") is no_data
    assert calls == []


def test_committed_text_corrupt_blob_is_git_failed(tmp_path, git_ready):
    top = make_repo(tmp_path / "planning")
    commit(top, "a.md", "x\n", "2026-01-05")
    corrupt(top, git(top, "rev-parse", "HEAD:a.md").strip())
    got = wbs_history.committed_text(repo_for(tmp_path, top), "a.md")
    assert isinstance(got, wbs_history.NoData) and got.text.startswith("no data: git failed: "), got
    assert str(tmp_path) not in got.text and str(tmp_path).replace(BS, "/") not in got.text


def _uncommitted_site(tmp_path, planning_bytes):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2025-11-01")
    commit(repo, "WBS.md", wbs("x"), "2026-01-05")
    return repo, answer(tmp_path, repo, planning=planning_bytes)


def test_uncommitted_true_when_working_tree_differs(tmp_path, git_ready):
    _, got = _uncommitted_site(tmp_path, wbs("x", "x").encode())
    assert (got.no_data, got.uncommitted, got.uncommitted_reason) == (None, True, None)


def test_uncommitted_false_when_it_matches(tmp_path, git_ready):
    _, got = _uncommitted_site(tmp_path, wbs("x").encode())
    assert (got.no_data, got.uncommitted, got.uncommitted_reason) == (None, False, None)


def test_uncommitted_true_when_absent_at_head(tmp_path, git_ready):
    top = make_repo(tmp_path / "plans")
    commit(top, "WBS.md", wbs(), "2025-11-01")
    git(top, "rm", "-q", "WBS.md")
    git(top, "commit", "-q", "-m", "gone", when="2026-01-05")
    got = answer(tmp_path, top)
    assert (got.no_data, got.uncommitted) == (None, True)


def _store(repo):
    git_dir = Path(repo) / ".git"
    return (git_dir / "index").read_bytes(), sorted(
        p.relative_to(git_dir / "objects").as_posix() for p in (git_dir / "objects").rglob("*"))


def test_uncommitted_check_writes_nothing(tmp_path, monkeypatch, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2026-01-05")
    before = _store(repo)
    calls = spy_git(monkeypatch)
    got = answer(tmp_path, repo, planning=wbs("x", "x", " new text").encode())
    assert got.uncommitted is True
    assert _store(repo) == before
    hashes = [a for a, _, _ in calls if a[0] == "hash-object"]
    assert len(hashes) == 1 and "-w" not in hashes[0]


def test_uncommitted_not_checked_in_no_data_case(tmp_path, git_ready):
    got = answer(tmp_path, make_repo(tmp_path / "plans"))
    assert (got.uncommitted, got.uncommitted_reason) == (None, NO_COMMITS_TEXT)


def test_uncommitted_not_checked_without_a_working_tree_wbs(tmp_path, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2026-01-05")
    config, roots, found = site_config(tmp_path / "inst", {"harbor": None}, history={"harbor": repo})
    got = wbs_history.last_state_change(roots, found["harbor"], config, AS_OF, 4, HistoryCache())
    assert got.no_data is None and got.uncommitted is None
    assert got.uncommitted_reason.startswith("no data: git failed: ")
    assert str(tmp_path) not in got.uncommitted_reason



def test_crlf_blob_under_autocrlf_reads_uncommitted(tmp_path, git_ready):
    """The documented limit: hash-object converts a CRLF file where git status would not."""
    repo = make_repo(tmp_path / "plans")
    crlf = wbs("x").replace("\n", "\r\n")
    commit(repo, "WBS.md", crlf, "2025-11-01")
    git(repo, "config", "core.autocrlf", "true")
    got = answer(tmp_path, repo, planning=crlf.encode())
    assert (got.no_data, got.uncommitted, got.uncommitted_reason) == (None, True, None)


def test_unreadable_commit_date_is_git_failed(tmp_path, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2025-11-01")
    write(repo, "WBS.md", wbs("x"))
    git(repo, "add", "WBS.md")
    tree, parent = git(repo, "write-tree").strip(), git(repo, "rev-parse", "HEAD").strip()
    body = (f"tree {tree}\nparent {parent}\nauthor Test <test@example.invalid> 1767614400 +0000\n"
            "committer Test <test@example.invalid> notadate +0000\n\nbad date\n")
    obj = write(tmp_path, "commit.txt", body)
    bad = git(repo, "hash-object", "-t", "commit", "-w", "--literally", str(obj)).strip()
    git(repo, "update-ref", "refs/heads/main", bad)
    got = answer(tmp_path, repo)
    assert got.no_data.text == f"no data: git failed: commit {bad[:12]} has no readable date"
    assert got.date is None and got.uncommitted is None


def test_nul_in_a_git_argument_is_git_failed(tmp_path, git_ready):
    got = wbs_history.run_git(["rev-parse", "--verify", "a\0b"], cwd=tmp_path, ceiling=tmp_path)
    assert isinstance(got, wbs_history.NoData) and got.text.startswith("no data: git failed: "), got


def test_extreme_windows_never_raise(tmp_path, git_ready):
    repo = make_repo(tmp_path / "plans")
    commit(repo, "WBS.md", wbs(), "2025-11-01")
    commit(repo, "WBS.md", wbs("x"), "2026-01-05")
    got = answer(tmp_path / "early", repo, as_of=date(1, 1, 10))
    assert got.no_data.text == "no data: `WBS.md` has no commits on or before 0001-01-10"
    got = answer(tmp_path / "long", repo, weeks=10**7)
    assert (got.no_data, got.date, got.commits_read) == (None, date(2026, 1, 5), 2)


def test_git_warning_does_not_turn_absent_into_failure(tmp_path, git_ready):
    top = make_repo(tmp_path / "planning")
    commit(top, "a.md", "x\n", "2026-01-05")
    (top / ".git" / "objects" / "info").mkdir(exist_ok=True)
    (top / ".git" / "objects" / "info" / "alternates").write_bytes(b"harbor\n")
    repo = repo_for(tmp_path, top)
    assert wbs_history.committed_text(repo, "nope.md") == wbs_history.Absent()
    assert wbs_history.committed_text(repo, "a.md") == "x\n"


def test_without_git_everything_reads_git_not_found(tmp_path, monkeypatch):
    planning = tmp_path / "plans"
    planning.mkdir()
    config, roots, found = site_config(tmp_path / "inst", {"harbor": b"x\n"}, history={"harbor": planning})
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    got = wbs_history.last_state_change(roots, found["harbor"], config, AS_OF, 4, HistoryCache())
    assert got.no_data.text == "no data: git not found"
    assert wbs_history.committed_text(got.repo, "Issue-7/checklist.md") is got.repo


# --- the sample legs, on the real checkout (Task 7) ------------------------------------

HISTORY_SITES = (HISTORY_RECENT, HISTORY_STALE, HISTORY_YOUNG)
SAMPLE_ROLES = {
    "recent": (HISTORY_RECENT, (date(2026, 1, 8), False, 3, None)),
    "stale": (HISTORY_STALE, (None, False, 2, None)),
    "young": (HISTORY_YOUNG, (date(2026, 1, 5), True, 2, None)),
    "less": (HISTORY_LESS, (None, False, 0, "no data: in no repository")),
}


def sample_answers(names=None):
    """{site: HistoryResult} on the sample installation, as_of and weeks passed."""
    from gantry.config import load_config
    from gantry.sites import config_roots, load_sites

    config = load_config(GANTRY_SAMPLE)
    roots = config_roots(config)
    found, _ = load_sites(config, roots)
    cache = HistoryCache()
    return {s.name: wbs_history.last_state_change(roots, s, config, AS_OF, 4, cache)
            for s in found if names is None or s.name in names}


def shape(result):
    return (result.date, result.history_start, result.commits_read,
            None if result.no_data is None else result.no_data.text)


@pytest.mark.parametrize("role", list(SAMPLE_ROLES))
def test_sample_roles_answer(role, built_sample_history):
    site, expected = SAMPLE_ROLES[role]
    assert shape(sample_answers({site})[site]) == expected


def test_git_dir_cannot_redirect_the_reader(monkeypatch, built_sample_history):
    if not git_work_tree(ROOT):
        pytest.skip("the gantry root is not a git work tree")
    monkeypatch.setenv("GIT_DIR", str(ROOT / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(ROOT))
    got = sample_answers({s for s, _ in SAMPLE_ROLES.values()})
    assert {role: shape(got[site]) for role, (site, _) in SAMPLE_ROLES.items()} == {
        role: expected for role, (_, expected) in SAMPLE_ROLES.items()}


def test_history_less_discovery_runs_under_the_ceiling(monkeypatch, built_sample_history):
    calls = spy_git(monkeypatch)
    assert shape(sample_answers({HISTORY_LESS})[HISTORY_LESS])[3] == "no data: in no repository"
    assert len(calls) == 1
    args, cwd, ceiling = calls[0]
    assert args[0] == "rev-parse" and ceiling == wbs_history.gantry_root()
    assert norm(cwd) == norm(SAMPLES / HISTORY_LESS)


def test_no_working_tree_wbs_is_opened(monkeypatch, built_sample_history):
    import builtins

    real_open, real_os_open = builtins.open, os.open

    def refuse(path):
        if os.path.basename(os.fsdecode(path)) == "WBS.md":
            raise PermissionError("a working-tree WBS.md was opened")

    def guarded_open(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            refuse(file)
        return real_open(file, *args, **kwargs)

    def guarded_os_open(path, *args, **kwargs):
        refuse(path)
        return real_os_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    with pytest.raises(PermissionError):
        builtins.open(SAMPLES / HISTORY_RECENT / "WBS.md", "rb")  # the control: the patch bites
    got = sample_answers(set(HISTORY_SITES))
    monkeypatch.undo()
    assert {s: got[s].no_data for s in HISTORY_SITES} == {s: None for s in HISTORY_SITES}
    assert shape(got[HISTORY_RECENT])[0] == date(2026, 1, 8)


def test_sample_sites_with_history_are_committed(built_sample_history):
    got = sample_answers(set(HISTORY_SITES))
    assert {s: (got[s].uncommitted, got[s].uncommitted_reason) for s in HISTORY_SITES} == {
        s: (False, None) for s in HISTORY_SITES}


def _home_without_autocrlf(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_bytes(b"[core]\n\tautocrlf = false\n")
    for name in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(name, str(home))


def test_crlf_copy_is_not_uncommitted(tmp_path, monkeypatch, built_sample_history):
    _home_without_autocrlf(tmp_path, monkeypatch)
    copies = {}
    for site in HISTORY_SITES:
        source = (SAMPLES / site / "WBS.md").read_bytes()
        copies[site] = source.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        assert copies[site] != source, site
    out = SAMPLES / "history" / "out"
    config, roots, found = site_config(tmp_path / "inst", copies, history={s: out / s for s in HISTORY_SITES})
    cache = HistoryCache()
    got = {s: wbs_history.last_state_change(roots, found[s], config, AS_OF, 4, cache) for s in HISTORY_SITES}
    assert {s: (r.no_data, r.uncommitted) for s, r in got.items()} == {s: (None, False) for s in HISTORY_SITES}
    # The control, under the same home: with no info/attributes, a CRLF copy is a change.
    repo = make_repo(tmp_path / "plain")
    commit(repo, "WBS.md", wbs(), "2026-01-05")
    assert answer(tmp_path / "control", repo, planning=wbs().replace("\n", "\r\n").encode()).uncommitted is True


def test_sample_wbs_eol_attr_is_lf(git_ready):
    if not git_work_tree(ROOT):
        pytest.skip("the gantry root is not a git work tree")
    sites = sorted(p.parent.name for p in SAMPLES.glob("*/WBS.md"))
    assert len(sites) == 5, sites
    for site in sites:
        result = sample_git(ROOT, "check-attr", "eol", "--", f"samples/{site}/WBS.md")
        assert result.returncode == 0
        assert result.stdout.decode("utf-8").strip().endswith("eol: lf"), site


# --- CI and docs (Task 8) --------------------------------------------------------------

DOC = ROOT / "docs" / "wbs-history.md"


def test_workflow_builds_sample_history_before_pytest():
    lines = (ROOT / ".github" / "workflows" / "test.yml").read_bytes().decode("utf-8").splitlines()
    tool = next((i for i, line in enumerate(lines) if "tools/sample_history.py" in line and "run:" in line), -1)
    tests = next((i for i, line in enumerate(lines) if "python -m pytest" in line), -1)
    assert tool >= 0 and tests >= 0
    assert tool < tests


def test_doc_states_every_no_data_reason():
    doc = " ".join(DOC.read_bytes().decode("utf-8").split())
    assert len(wbs_history.REASONS) == 11
    missing = [r for r in wbs_history.REASONS if r.split("{")[0].strip() not in doc]
    assert missing == []


def test_doc_command_is_the_tools_grammar():
    doc = DOC.read_bytes().decode("utf-8")
    fenced = re.findall(r"```[^\n]*\n(.*?)```", doc, re.S)
    commands = [line for block in fenced for line in block.splitlines() if "sample_history" in line]
    assert commands and all(c.startswith("python tools/sample_history.py") for c in commands), commands
