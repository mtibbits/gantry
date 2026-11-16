"""tools/next_requirement_id.py: the next free requirement ID for a layer (#161).

IDs are built at run time from a number `k` the test picks, so no test pins
the live file's highest number. Every subprocess the tool would run goes
through a fake runner that raises on an argv it was not given, so no test
reaches the network; the read-only tests run real git against a local bare
remote they build, with a fake gh.
"""

import json
import re
import string
import subprocess
import sys
import time

import pytest

import next_requirement_id as nri
from gantry.requirements import DEFAULT_BR_NAMESPACE, ID_RE, PROJECT_LAYERS, parse_id, read_requirements
from helpers_config import roots_around
from test_samples import ROOT, git, git_env, needs_git

K = 731
KS = (1, K)
NOT_COUNTED = ("CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "MANNEQUIN", "NONE")


def main_src(text):
    return nri.Source("main", None, None, text)


def tree_src(text):
    return nri.Source("tree", None, None, text)


def issue(number, association, text):
    return nri.Source("issue", number, association, text)


def pull(number, association, text):
    return nri.Source("pull", number, association, text)


def next_id(code, sources):
    n, source = nri.choose(code, sources)
    return nri.format_id(code, n + 1), source


# --- counting (AC Counting) ---------------------------------------------------------


@pytest.mark.parametrize("k", KS)
def test_counting_ac(k):
    pr = 12
    sources = [
        main_src(f"- FR-{k}: main's highest\n"),
        issue(3, "OWNER", f"Claims FR-{k + 1}.\n"),
        issue(5, "CONTRIBUTOR", f"Claims FR-{k + 50}.\n"),
        pull(pr, "MEMBER", f"- gantry:FR-{k + 2}: the pull request's\n"),
        issue(6, "OWNER", f"harbor:FR-{k + 9} FR-01 NFR-{k + 5} FR-{10**9 + k}\n"),
    ]
    got, source = next_id("FR", sources)
    assert got == f"FR-{k + 3}"
    assert source.kind == "pull"
    assert source.label == f"open pull request #{pr}"


NON_COUNTING = [
    pytest.param(issue(4, "OWNER", f"harbor:FR-{K + 1}"), id="other-namespace"),
    pytest.param(issue(4, "OWNER", "FR-01"), id="leading-zero"),
    pytest.param(issue(4, "OWNER", f"NFR-{K + 5}"), id="inside-NFR"),
    pytest.param(issue(4, "OWNER", f"FR-{10**9 + K}"), id="ten-digit-run"),
] + [pytest.param(issue(4, assoc, f"FR-{K + 1}"), id=assoc) for assoc in NOT_COUNTED]


@pytest.mark.parametrize("noise", NON_COUNTING)
def test_non_counting_mention_alone(noise):
    got, source = next_id("FR", [main_src(f"- FR-{K}: x\n"), noise])
    assert got == f"FR-{K + 1}"
    assert source.label == "origin/main"


@pytest.mark.parametrize("assoc", ["OWNER", "MEMBER", "COLLABORATOR"])
def test_counted_associations(assoc):
    got, source = next_id("FR", [main_src(f"- FR-{K}: x\n"), issue(4, assoc, f"FR-{K + 1}")])
    assert got == f"FR-{K + 2}"
    assert source.label == "open issue #4"


N = 731
SHAPES = [
    (f"- FR-{N}: x", [N]),
    (f"(FR-{N})", [N]),
    (f"[FR-{N},", [N]),
    (f"**FR-{N}**", [N]),
    (f"`FR-{N}`", [N]),
    (f"FR-{N}.", [N]),
    (f"FR-{N}:", [N]),
    (f"gantry:FR-{N}", [N]),
    (f"FR-{N}\r\n", [N]),
    (f"x-FR-{N}", [N]),
    (f"_FR-{N}_", [N]),
    (f"__FR-{N}__", [N]),
    (f"e.g.FR-{N}", [N]),
    (f"_gantry:FR-{N}_", [N]),
    ("FR-3-FR-5", [3, 5]),
    (f"FR-{N}a", []),
    (f"xgantry:FR-{N}", []),
    (f"x-gantry:FR-{N}", []),
    ("{refines:FR-2}", []),
]


@pytest.mark.parametrize("text, counted", SHAPES)
def test_counting_shapes(text, counted):
    assert nri.mentions(text, "FR") == counted


def test_left_boundary_counts_punctuation():
    for c in map(chr, range(32, 127)):
        assert bool(nri.mentions(c + "FR-5", "FR")) == (c != ":" and not c.isalnum()), repr(c)


# B1's one-regex form (imPlan Improvements): the tool's scan must agree with it
# on every shape, while staying linear where this form is not.
GRAMMAR_FORM = re.compile(rf"(?<![A-Za-z0-9:])(?:{ID_RE.pattern})(?![A-Za-z0-9])")


@pytest.mark.parametrize("text", [text for text, _ in SHAPES] + [
    "FR-1-gantry:FR-5", "a.b:FR-2 gantry:FR-3", "-gantry:FR-4", ".gantry:FR-6", "gantry:gantry:FR-7",
])
def test_scan_agrees_with_the_grammar_form(text):
    want = [int(m["number"]) for m in GRAMMAR_FORM.finditer(text)
            if m["code"] == "FR" and m["namespace"] in (None, "gantry")]
    assert nri.mentions(text, "FR") == want


TIES = [
    pytest.param(main_src, tree_src, "origin/main", id="main-over-tree"),
    pytest.param(tree_src, lambda t: issue(1, "OWNER", t), "the working tree", id="tree-over-issue"),
    pytest.param(lambda t: issue(9, "OWNER", t), lambda t: pull(1, "OWNER", t), "open issue #9", id="issue-over-pull"),
    pytest.param(lambda t: issue(4, "OWNER", t), lambda t: issue(9, "OWNER", t), "open issue #4", id="issue-4-over-9"),
    pytest.param(lambda t: pull(3, "MEMBER", t), lambda t: pull(8, "MEMBER", t), "open pull request #3",
                 id="pull-3-over-8"),
]


@pytest.mark.parametrize("winner, loser, label", TIES)
def test_tie_order(winner, loser, label):
    text = f"FR-{K}"
    got, source = next_id("FR", [loser(text), winner(text)])  # given in reverse
    assert got == f"FR-{K + 1}"
    assert source.label == label


def test_no_number_gives_one():
    sources = [main_src("- UC-3: x\n"), tree_src("")]
    assert nri.choose("FR", sources) == (0, None)
    assert nri.format_id("FR", 1) == "FR-1"


def test_nine_digit_limit():
    assert nri.format_id("FR", 999999999) == "FR-999999999"
    with pytest.raises(nri.TooLong):
        nri.format_id("FR", 10**9)


@pytest.mark.parametrize("code", nri.CODES)
def test_result_is_an_id(code):
    for n in (1, 999999999):
        assert parse_id(nri.format_id(code, n), nri.NAMESPACE) is not None


def test_scanner_finds_every_defined_id():
    doc = ROOT / "docs" / "requirements.md"
    defined = read_requirements(roots_around(doc), doc, nri.NAMESPACE, layers=PROJECT_LAYERS,
                                br_namespace=DEFAULT_BR_NAMESPACE).requirements.values()
    assert len(defined) >= 20
    text = doc.read_bytes().decode("utf-8")
    found = {(code, n) for code in nri.CODES for n in nri.mentions(text, code)}
    assert {(r.id.code, r.id.number) for r in defined} <= found


def test_scan_is_linear_on_hostile_text():
    size = 1_000_000
    hostile = [
        string.ascii_letters * (size // 52),
        "a:" * (size // 2),
        "FR-" * (size // 3),
        "FR-" + "1" * size,
        "a-" * (size // 2),
        "_a." * (size // 3),
    ]
    start = time.perf_counter()
    for text in hostile:
        nri.mentions(text, "FR")
    assert time.perf_counter() - start < 2.0


# --- the CLI and the git sources (AC Codes, Sources read, Fails closed) -------------

SHA = "a1" * 20
OTHER = "b2" * 20
TOP = ("git", "rev-parse", "--show-toplevel")
REV = ("git", "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main^{commit}")
LS = ("git", "ls-remote", "origin", "refs/heads/main")
CAT = ("git", "cat-file", "blob", f"{SHA}:docs/requirements.md")
TOOL = ROOT / "tools" / "next_requirement_id.py"
P = "next_requirement_id: "


def ok(out=b""):
    return nri.Result(0, out.encode("utf-8") if isinstance(out, str) else out, b"")


def fail(rc, err=b""):
    return nri.Result(rc, b"", err)


class FakeRunner:
    """Scripted results (or exceptions) keyed by argv; any other argv fails the test."""

    def __init__(self, script):
        self.script = dict(script)
        self.calls = []

    def __call__(self, argv, cwd):
        self.calls.append(list(argv))
        try:
            result = self.script[tuple(argv)]
        except KeyError:
            raise AssertionError(f"unscripted argv: {argv}") from None
        if isinstance(result, BaseException):
            raise result
        return result


def git_script(main_text="", remote=SHA):
    return {REV: ok(SHA + "\n"), LS: ok(f"{remote}\trefs/heads/main\n"), CAT: ok(main_text)}


def tree(tmp_path, data="# Requirements\n"):
    path = tmp_path / "docs" / "requirements.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return tmp_path


def run_main(capsys, argv, runner, root):
    code = nri.main(argv, runner=runner, root=root)
    out, err = capsys.readouterr()
    return code, out, err


def refused(capsys, argv, runner, root, line):
    assert run_main(capsys, argv, runner, root) == (1, "", P + line + "\n")


def offline_line(sha=SHA):
    return (f"--offline: read origin/main at {sha[:12]}, not compared with the remote, and the working tree; "
            "open issues and pull requests were not read")


def test_codes_literal():
    assert nri.CODES == ("BRule", "UC", "SR", "FR", "QA", "EI", "CON")
    assert set(nri.CODES) == PROJECT_LAYERS


@pytest.mark.parametrize("code", nri.CODES)
def test_code_accepted(capsys, tmp_path, code):
    runner = FakeRunner(git_script("- FR-3: x\n"))
    rc, out, err = run_main(capsys, ["--offline", code], runner, tree(tmp_path))
    if code == "FR":
        assert (rc, out) == (0, "FR-4\n")
    else:
        assert (rc, out) == (0, f"{code}-1\n")
        assert err.splitlines()[-1] == f"{P}{code}-1: no {code} number was found"


@pytest.mark.parametrize("bad", ["BR", "fr", "XX", ""])
def test_code_refused(capsys, tmp_path, bad):
    with pytest.raises(SystemExit) as exit_:
        nri.main([bad], runner=FakeRunner({}), root=tree(tmp_path))
    out, err = capsys.readouterr()
    assert exit_.value.code == 2
    assert out == ""
    assert f"{bad}: not a code a project file uses; use one of BRule, UC, SR, FR, QA, EI, CON" in err


@pytest.mark.parametrize("argv", [["--offlin", "FR"], ["FR", "UC"], []])
def test_usage_errors(capsys, tmp_path, argv):
    with pytest.raises(SystemExit) as exit_:
        nri.main(argv, runner=FakeRunner({}), root=tree(tmp_path))
    assert exit_.value.code == 2
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("k", KS)
def test_stdout_is_the_bare_id(capsys, tmp_path, k):
    runner = FakeRunner(git_script(f"- FR-{k}: x\n"))
    rc, out, err = run_main(capsys, ["--offline", "FR"], runner, tree(tmp_path, f"- FR-{k}: x\n"))
    assert (rc, out) == (0, f"FR-{k + 1}\n")
    assert err == f"{P}{offline_line()}\n{P}FR-{k + 1}: the highest FR number, {k}, is in origin/main\n"


def test_offline_reads_only_main_and_tree(capsys, tmp_path):
    runner = FakeRunner(git_script())
    rc, _, err = run_main(capsys, ["--offline", "FR"], runner, tree(tmp_path))
    assert rc == 0
    assert runner.calls == [list(REV), list(CAT)]
    assert err.splitlines()[0] == P + offline_line()


def test_tree_counts_offline_with_bom_and_crlf(capsys, tmp_path):
    runner = FakeRunner(git_script(f"- FR-{K}: x\n"))
    root = tree(tmp_path, f"\N{BYTE ORDER MARK}- FR-{K}: x\r\n- FR-{K + 4}: y\r\n")
    rc, out, err = run_main(capsys, ["--offline", "FR"], runner, root)
    assert (rc, out) == (0, f"FR-{K + 5}\n")
    assert err.splitlines()[-1] == f"{P}FR-{K + 5}: the highest FR number, {K + 4}, is in the working tree"


def test_origin_main_missing(capsys, tmp_path):
    runner = FakeRunner({**git_script(), REV: fail(1)})
    refused(capsys, ["FR"], runner, tree(tmp_path), "origin/main: missing; run git fetch")


def test_origin_main_behind(capsys, tmp_path):
    runner = FakeRunner(git_script(remote=OTHER))
    refused(capsys, ["FR"], runner, tree(tmp_path),
            f"origin/main: at {SHA[:12]}, but origin's main is at {OTHER[:12]}; run git fetch")
    assert list(CAT) not in runner.calls


def test_ls_remote_tail_ref_is_not_main(capsys, tmp_path):
    runner = FakeRunner({**git_script(), LS: ok(f"{SHA}\trefs/heads/x/refs/heads/main\n")})
    refused(capsys, ["FR"], runner, tree(tmp_path), "origin/main: could not read: origin has no branch main")


@pytest.mark.parametrize("err, reason", [(b"fatal: x\n", "git exited 128: fatal: x"), (b"", "git exited 128"),
                                         (b"\n  \nfatal: y\nmore\n", "git exited 128: fatal: y")])
@pytest.mark.parametrize("step", [1, 2, 3, 4])
def test_git_fails(capsys, tmp_path, monkeypatch, step, err, reason):
    root = tree(tmp_path)
    script = {**git_script(), TOP: ok(str(root) + "\n")}
    script[(TOP, REV, LS, CAT)[step - 1]] = fail(128, err)
    argv = ["FR"] if step == 3 else ["--offline", "FR"]
    if step == 1:
        monkeypatch.chdir(root)
    where = "the checkout" if step == 1 else "origin/main"
    refused(capsys, argv, FakeRunner(script), None if step == 1 else root, f"{where}: could not read: {reason}")


def test_checkout_is_the_toplevel(capsys, tmp_path, monkeypatch):
    root = tree(tmp_path, f"- FR-{K}: x\n")
    (root / "sub").mkdir()
    monkeypatch.chdir(root / "sub")
    runner = FakeRunner({**git_script(), TOP: ok(str(root) + "\n")})
    rc, out, _ = run_main(capsys, ["--offline", "FR"], runner, None)
    assert (rc, out) == (0, f"FR-{K + 1}\n")
    assert runner.calls[0] == list(TOP)


def test_rev_parse_garbage_refuses(capsys, tmp_path):
    runner = FakeRunner({**git_script(), REV: ok("not-a-sha\n")})
    refused(capsys, ["--offline", "FR"], runner, tree(tmp_path), "origin/main: could not read: unexpected output")


def test_git_missing(capsys, tmp_path):
    runner = FakeRunner({REV: FileNotFoundError(2, "No such file or directory", "git")})
    refused(capsys, ["--offline", "FR"], runner, tree(tmp_path), "origin/main: could not read: git is not installed")


def test_working_tree_file_missing(capsys, tmp_path):
    rc, out, err = run_main(capsys, ["--offline", "FR"], FakeRunner(git_script()), tmp_path)
    assert (rc, out) == (1, "")
    assert err.startswith(P + "the working tree's docs/requirements.md: could not read: ")
    assert err.count("\n") == 1 and len(err) > len(P + "the working tree's docs/requirements.md: could not read: \n")


@pytest.mark.parametrize("where", ["main", "tree"])
def test_not_utf8_refuses(capsys, tmp_path, where):
    runner = FakeRunner({**git_script(), **({CAT: ok(b"\xff")} if where == "main" else {})})
    root = tree(tmp_path, b"\xff" if where == "tree" else "x\n")
    label = "origin/main" if where == "main" else "the working tree's docs/requirements.md"
    refused(capsys, ["--offline", "FR"], runner, root, f"{label}: could not read: not UTF-8")


def test_too_long_refuses(capsys, tmp_path):
    runner = FakeRunner(git_script("- FR-999999999: x\n"))
    refused(capsys, ["--offline", "FR"], runner, tree(tmp_path),
            "FR-1000000000 would have more than 9 digits, so it is not an ID; "
            "the highest FR number, 999999999, is in origin/main")


def test_script_entry_point(tmp_path):
    def run(*argv):
        return subprocess.run([sys.executable, str(TOOL), *argv], cwd=tmp_path, capture_output=True,
                              stdin=subprocess.DEVNULL)

    bad = run("BR")
    assert bad.returncode == 2
    assert bad.stdout == b""
    assert b"BR: not a code a project file uses" in bad.stderr
    helped = run("--help")
    assert helped.returncode == 0
    assert b"--offline" in helped.stdout
    assert b"Exit 0" in helped.stdout


# --- the gh sources and the pagination contract -------------------------------------

NWO = "owner/repo"
H = "c3" * 20
REMEDY = "or run --offline (which reads no open issues or pull requests)"


def page_argv(query, after=None):
    argv = ["gh", "api", "graphql", "-F", "owner={owner}", "-F", "name={repo}", "-f", "query=" + query]
    return tuple(argv + (["-f", "after=" + after] if after is not None else []))


ISSUES = page_argv(nri.ISSUES_QUERY)
PULLS = page_argv(nri.PULLS_QUERY)


def contents_argv(oid):
    return ("gh", "api", "-H", "Accept: application/vnd.github.raw+json",
            f"repos/{{owner}}/{{repo}}/contents/docs/requirements.md?ref={oid}")


def page(field, nodes, total=None, after=None):
    """A listing page in the live probe's shape; `after` is the next page's cursor."""
    total = len(nodes) if total is None else total
    end = after if after is not None else (f"c-{nodes[-1]['number']}" if nodes else None)
    listing = {"totalCount": total, "pageInfo": {"hasNextPage": after is not None, "endCursor": end},
               "nodes": nodes}
    return ok(json.dumps({"data": {"repository": {"nameWithOwner": NWO, field: listing}}}))


def inode(number, association, body):
    return {"number": number, "authorAssociation": association, "body": body}


def pnode(number, association, oid=H):
    return {"number": number, "authorAssociation": association, "headRefOid": oid}


def online(main_text="", issues=(), pulls=(), files=None):
    script = {**git_script(main_text), ISSUES: page("issues", list(issues)), PULLS: page("pullRequests", list(pulls))}
    for oid, text in (files or {}).items():
        script[contents_argv(oid)] = ok(text)
    return script


def test_queries_are_single_line_and_read_only():
    for query in (nri.ISSUES_QUERY, nri.PULLS_QUERY):
        assert "\n" not in query
        assert not re.search(r"\b(mutation|subscription)\b", query)
        assert query.startswith("query(")


def test_pages_until_total(capsys, tmp_path):
    script = online(f"- FR-{K}: x\n")
    script[ISSUES] = page("issues", [inode(1, "OWNER", ""), inode(2, "OWNER", "")], total=3, after="c1")
    script[page_argv(nri.ISSUES_QUERY, "c1")] = page("issues", [inode(3, "OWNER", f"FR-{K + 1}")], total=3)
    runner = FakeRunner(script)
    rc, out, _ = run_main(capsys, ["FR"], runner, tree(tmp_path))
    assert (rc, out) == (0, f"FR-{K + 2}\n")
    pages = [argv for argv in runner.calls if "query=" + nri.ISSUES_QUERY in argv]
    assert pages == [list(ISSUES), list(ISSUES) + ["-f", "after=c1"]]


@pytest.mark.parametrize("field, argv, where", [("issues", ISSUES, "open issues"),
                                                ("pullRequests", PULLS, "open pull requests")])
def test_listing_short_refuses(capsys, tmp_path, field, argv, where):
    nodes = ([inode(1, "NONE", ""), inode(2, "NONE", "")] if field == "issues"
             else [pnode(1, "NONE"), pnode(2, "NONE")])
    script = {**online(), argv: page(field, nodes, total=3)}
    refused(capsys, ["FR"], FakeRunner(script), tree(tmp_path), f"{where}: could not read: read 2 of 3; run again")


def test_repeated_cursor_refuses(capsys, tmp_path):
    script = online()
    script[ISSUES] = page("issues", [inode(1, "OWNER", "")], total=3, after="c1")
    script[page_argv(nri.ISSUES_QUERY, "c1")] = page("issues", [inode(2, "OWNER", "")], total=3, after="c1")
    refused(capsys, ["FR"], FakeRunner(script), tree(tmp_path),
            "open issues: could not read: the same page came back twice; run again")


def test_duplicate_number_refuses(capsys, tmp_path):
    script = online()
    script[ISSUES] = page("issues", [inode(1, "OWNER", "")], total=2, after="c1")
    script[page_argv(nri.ISSUES_QUERY, "c1")] = page("issues", [inode(1, "OWNER", "")], total=2)
    refused(capsys, ["FR"], FakeRunner(script), tree(tmp_path), "open issues: could not read: read 1 of 2; run again")


def test_page_cap_refuses(capsys, tmp_path):
    script = online()
    script[ISSUES] = page("issues", [], total=0, after="c1")
    script[page_argv(nri.ISSUES_QUERY, "c1")] = page("issues", [], total=0, after="c2")
    runner = FakeRunner(script)  # a third page is unscripted: the cap must stop first
    refused(capsys, ["FR"], runner, tree(tmp_path),
            "open issues: could not read: more pages than 0 items need; run again")


def listing(field="issues", **changes):
    node = inode(1, "OWNER", "") if field == "issues" else pnode(1, "OWNER")
    doc = {"data": {"repository": {"nameWithOwner": NWO, field: {
        "totalCount": 1, "pageInfo": {"hasNextPage": False, "endCursor": "c"}, "nodes": [node]}}}}
    inner = doc["data"]["repository"][field]
    for key, value in changes.items():
        if key == "node":
            inner["nodes"] = [value]
        elif key in ("hasNextPage", "endCursor"):
            inner["pageInfo"][key] = value
        elif value is DROP:
            inner["pageInfo"].pop("hasNextPage")
        else:
            inner[key] = value
    return json.dumps(doc)


DROP = object()
BAD = [
    pytest.param("not json", id="not-json"),
    pytest.param("null", id="null"),
    pytest.param('{"data": null}', id="data-null"),
    pytest.param(listing(node=None), id="null-node"),
    pytest.param(listing(totalCount="1"), id="string-total"),
    pytest.param(listing(totalCount=True), id="bool-total"),
    pytest.param(listing(missing=DROP), id="no-hasNextPage"),
    pytest.param(listing(hasNextPage=True, endCursor=None), id="next-without-cursor"),
    pytest.param(listing(hasNextPage=True, endCursor=""), id="next-empty-cursor"),
    pytest.param(listing(node={"number": True, "authorAssociation": "OWNER", "body": ""}), id="bool-number"),
    pytest.param(listing(node={"number": 1, "authorAssociation": "OWNER"}), id="no-body"),
    pytest.param("[" * 100_000 + "]" * 100_000, id="deep"),
]


@pytest.mark.parametrize("payload", BAD)
def test_bad_response_refuses(capsys, tmp_path, payload):
    script = {**online(), ISSUES: ok(payload)}
    refused(capsys, ["FR"], FakeRunner(script), tree(tmp_path), "open issues: could not read: unexpected response")


def test_bad_pull_node_refuses(capsys, tmp_path):
    script = {**online(), PULLS: ok(listing("pullRequests", node={"number": 1, "authorAssociation": "OWNER"}))}
    refused(capsys, ["FR"], FakeRunner(script), tree(tmp_path),
            "open pull requests: could not read: unexpected response")


def test_gh_missing(capsys, tmp_path):
    script = {**online(), ISSUES: FileNotFoundError(2, "No such file or directory", "gh")}
    refused(capsys, ["FR"], FakeRunner(script), tree(tmp_path), "open issues: could not read: gh is not installed")


def test_gh_unauthenticated(capsys, tmp_path):
    err = (b"To get started with GitHub CLI, please run:  gh auth login\n"
           b"Alternatively, populate the GH_TOKEN environment variable with a GitHub API authentication token.\n")
    script = {**online(), ISSUES: fail(4, err)}
    refused(capsys, ["FR"], FakeRunner(script), tree(tmp_path),
            "open issues: could not read: gh exited 4: To get started with GitHub CLI, please run:  gh auth login")


def test_gh_fails_without_stderr(capsys, tmp_path):
    script = {**online(), PULLS: fail(1)}
    refused(capsys, ["FR"], FakeRunner(script), tree(tmp_path), "open pull requests: could not read: gh exited 1")


@pytest.mark.parametrize("k", KS)
def test_number_only_in_working_tree_counts(capsys, tmp_path, k):
    runner = FakeRunner(online(f"- FR-{k}: x\n"))
    rc, out, err = run_main(capsys, ["FR"], runner, tree(tmp_path, f"- FR-{k}: x\n- FR-{k + 4}: y\n"))
    assert (rc, out) == (0, f"FR-{k + 5}\n")
    assert err.splitlines()[-1] == f"{P}FR-{k + 5}: the highest FR number, {k + 4}, is in the working tree"


def test_pull_file_read_at_head(capsys, tmp_path):
    runner = FakeRunner(online(f"- FR-{K}: x\n", pulls=[pnode(7, "MEMBER", H)],
                               files={H: f"- gantry:FR-{K + 2}: y\n"}))
    rc, out, err = run_main(capsys, ["FR"], runner, tree(tmp_path))
    assert (rc, out) == (0, f"FR-{K + 3}\n")
    assert list(contents_argv(H)) in runner.calls
    assert contents_argv(H)[-1] == f"repos/{{owner}}/{{repo}}/contents/docs/requirements.md?ref={H}"
    assert "headRefOid" in nri.PULLS_QUERY and "baseRef" not in nri.PULLS_QUERY
    assert err.splitlines()[-1].endswith("is in open pull request #7")


def test_author_filter_before_scan(capsys, tmp_path):
    runner = FakeRunner(online(f"- FR-{K}: x\n", issues=[inode(3, "NONE", f"FR-{K + 50}")],
                               pulls=[pnode(5, "CONTRIBUTOR", H)]))  # no file scripted for #5
    rc, out, _ = run_main(capsys, ["FR"], runner, tree(tmp_path))
    assert (rc, out) == (0, f"FR-{K + 1}\n")
    assert not [argv for argv in runner.calls if argv[:3] == ["gh", "api", "-H"]]


def test_unknown_association_refuses(capsys, tmp_path):
    runner = FakeRunner(online(issues=[inode(3, "SPONSOR", "")]))
    refused(capsys, ["FR"], runner, tree(tmp_path),
            "open issues: could not read: unknown author association SPONSOR on #3; "
            f"this tool needs updating for it, {REMEDY}")


@pytest.mark.parametrize("oid", ["HEAD", "c3" * 19, ("C3" * 20), "c3" * 20 + "\n", "-x"])
def test_bad_head_oid_refuses(capsys, tmp_path, oid):
    runner = FakeRunner(online(pulls=[pnode(7, "OWNER", oid)]))
    refused(capsys, ["FR"], runner, tree(tmp_path), "open pull request #7: could not read: unexpected head commit")
    assert not [argv for argv in runner.calls if argv[:3] == ["gh", "api", "-H"]]


def test_pull_file_fails_refuses(capsys, tmp_path):
    script = {**online(pulls=[pnode(7, "OWNER", H)]), contents_argv(H): fail(1, b"gh: Not Found (HTTP 404)\n")}
    rc, out, err = run_main(capsys, ["FR"], FakeRunner(script), tree(tmp_path))
    assert (rc, out) == (1, "")
    assert err == (f"{P}open pull request #7: could not read: gh exited 1: gh: Not Found (HTTP 404)\n"
                   f"{P}open pull request #7 has no docs/requirements.md at its head {H}; "
                   f"ask its author to rebase, or close it, {REMEDY}\n")


def test_pull_file_other_failure_has_no_404_line(capsys, tmp_path):
    script = {**online(pulls=[pnode(7, "OWNER", H)]), contents_argv(H): fail(1, b"gh: Server Error (HTTP 502)\n")}
    refused(capsys, ["FR"], FakeRunner(script), tree(tmp_path),
            "open pull request #7: could not read: gh exited 1: gh: Server Error (HTTP 502)")


def test_pull_file_not_utf8_refuses(capsys, tmp_path):
    script = {**online(pulls=[pnode(7, "OWNER", H)]), contents_argv(H): ok(b"\xff")}
    refused(capsys, ["FR"], FakeRunner(script), tree(tmp_path), "open pull request #7: could not read: not UTF-8")


def test_pull_files_read_ascending(capsys, tmp_path):
    h2 = "d4" * 20
    runner = FakeRunner(online(pulls=[pnode(9, "OWNER", h2), pnode(4, "COLLABORATOR", H)],
                               files={H: "", h2: ""}))
    assert run_main(capsys, ["FR"], runner, tree(tmp_path))[0] == 0
    reads = [argv[-1] for argv in runner.calls if argv[:3] == ["gh", "api", "-H"]]
    assert reads == [contents_argv(H)[-1], contents_argv(h2)[-1]]


def test_tie_stderr_names_earlier(capsys, tmp_path):
    runner = FakeRunner(online(f"- FR-{K}: x\n", issues=[inode(4, "OWNER", f"Claims FR-{K + 1}.")],
                               pulls=[pnode(2, "OWNER", H)], files={H: f"- FR-{K + 1}: y\n"}))
    rc, out, err = run_main(capsys, ["FR"], runner, tree(tmp_path))
    assert (rc, out) == (0, f"FR-{K + 2}\n")
    assert err.splitlines()[-1] == f"{P}FR-{K + 2}: the highest FR number, {K + 1}, is in open issue #4"


def test_online_stderr_summary(capsys, tmp_path):
    h2 = "d4" * 20
    runner = FakeRunner(online(issues=[inode(1, "OWNER", ""), inode(2, "NONE", ""), inode(5, "MEMBER", "")],
                               pulls=[pnode(3, "CONTRIBUTOR", h2), pnode(6, "OWNER", H)], files={H: ""}))
    rc, out, err = run_main(capsys, ["FR"], runner, tree(tmp_path))
    assert (rc, out) == (0, "FR-1\n")
    assert err == (f"{P}read origin/main at {SHA[:12]}, the working tree, and {NWO}'s 3 open issues and "
                   "2 open pull requests (2 and 1 by the owner, a member or a collaborator)\n"
                   f"{P}FR-1: no FR number was found\n")


# --- read only, with real git and a local bare remote (AC Read only) -----------------
# The AC's "as test_commit_check.py builds one": that file builds a hermetic temp
# repo but no bare remote, so this builder makes its own in the same style.

IDENTITY = ("-c", "user.name=Test", "-c", "user.email=test@example.invalid")
REAL_GIT = {"rev-parse", "ls-remote", "cat-file"}
PR = 21


def run_git(cwd, *args):
    result = git(cwd, *IDENTITY, *args, hermetic=True)
    assert result.returncode == 0, (args, result.stderr.decode("utf-8", "replace"))
    return result.stdout.decode("utf-8")


def commit_file(repo, text, message):
    path = repo / "docs" / "requirements.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    run_git(repo, "add", "--", "docs/requirements.md")
    run_git(repo, "commit", "-q", "-m", message)


def bare_remote_repo(tmp_path, k):
    """(repo, remote URI): repo's main pushed to a local bare remote, so origin/main exists."""
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    run_git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    repo.mkdir()
    run_git(repo, "init", "-q", "-b", "main")
    commit_file(repo, f"# Requirements\n\n- FR-{k}: on main\n", "main")
    uri = remote.as_uri()  # one form on every OS (imPlan U2)
    run_git(repo, "remote", "add", "origin", uri)
    run_git(repo, "push", "-q", "origin", "main")
    assert run_git(repo, "rev-parse", "--verify", "refs/remotes/origin/main").strip()
    assert not (repo / ".git" / "FETCH_HEAD").exists()
    return repo, uri


def snapshot(repo):
    files = tuple(sorted((p.relative_to(repo).as_posix(), p.read_bytes()) for p in repo.rglob("*")
                         if p.is_file() and ".git" not in p.relative_to(repo).parts))
    counts = dict(line.split(": ", 1) for line in run_git(repo, "count-objects", "-v").splitlines())
    return (
        files,
        run_git(repo, "--no-optional-locks", "status", "--porcelain", "--untracked-files=all"),
        run_git(repo, "for-each-ref"),
        tuple(counts[key] for key in ("count", "in-pack", "packs")),
        (repo / ".git" / "FETCH_HEAD").exists(),
    )


class HybridRunner:
    """Real git for the reads the tool may run, a fake gh, and nothing else."""

    def __init__(self, k):
        self.calls = []
        self.gh = {ISSUES: page("issues", []), PULLS: page("pullRequests", [pnode(PR, "OWNER", H)]),
                   contents_argv(H): ok(f"- FR-{k + 5}: the pull request's\n")}

    def __call__(self, argv, cwd):
        self.calls.append(list(argv))
        if argv[0] == "git" and argv[1] in REAL_GIT:
            done = subprocess.run(argv, cwd=cwd, capture_output=True, stdin=subprocess.DEVNULL,
                                  env=git_env(hermetic=True))
            return nri.Result(done.returncode, done.stdout, done.stderr)
        if tuple(argv) in self.gh:
            return self.gh[tuple(argv)]
        raise AssertionError(f"unexpected argv: {argv}")

    def ran(self, verb):
        return [argv for argv in self.calls if argv[:2] == ["git", verb]]


@needs_git
@pytest.mark.parametrize("k", KS)
def test_online_path_writes_nothing(capsys, tmp_path, monkeypatch, k):
    repo, _ = bare_remote_repo(tmp_path, k)
    monkeypatch.chdir(repo)
    before = snapshot(repo)
    runner = HybridRunner(k)
    rc, out, err = run_main(capsys, ["FR"], runner, None)
    assert (rc, out) == (0, f"FR-{k + 6}\n"), err
    assert err.splitlines()[-1].endswith(f"is in open pull request #{PR}")
    assert len(runner.ran("ls-remote")) == 1
    assert snapshot(repo) == before


@needs_git
def test_stale_origin_main_refuses_then_fetch_recovers(capsys, tmp_path, monkeypatch):
    repo, uri = bare_remote_repo(tmp_path, K)
    other = tmp_path / "other"
    run_git(tmp_path, "clone", "-q", uri, str(other))
    commit_file(other, f"# Requirements\n\n- FR-{K}: on main\n- FR-{K + 7}: merged elsewhere\n", "elsewhere")
    run_git(other, "push", "-q", "origin", "main")
    remote_tip = run_git(repo, "ls-remote", "origin", "refs/heads/main").split("\t")[0]
    assert remote_tip != run_git(repo, "rev-parse", "refs/remotes/origin/main").strip()
    monkeypatch.chdir(repo)
    before = snapshot(repo)

    rc, out, err = run_main(capsys, ["FR"], HybridRunner(K), None)
    assert (rc, out) == (1, "")
    assert err.endswith("; run git fetch\n")
    assert snapshot(repo) == before

    run_git(repo, "fetch", "-q", "origin")  # the remedy the refusal names
    assert snapshot(repo) != before  # the instrument sees a fetch
    rc, out, err = run_main(capsys, ["FR"], HybridRunner(K), None)
    assert (rc, out) == (0, f"FR-{K + 8}\n"), err
    assert err.splitlines()[-1].endswith("is in origin/main")
