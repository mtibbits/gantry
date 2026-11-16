"""tools/next_requirement_id.py: the next free requirement ID for a layer (#161).

IDs are built at run time from a number `k` the test picks, so no test pins
the live file's highest number. Every subprocess the tool would run goes
through a fake runner that raises on an argv it was not given, so no test
reaches the network; the read-only tests run real git against a local bare
remote they build, with a fake gh.
"""

import re
import string
import subprocess
import sys
import time

import pytest

import next_requirement_id as nri
from gantry.requirements import DEFAULT_BR_NAMESPACE, ID_RE, PROJECT_LAYERS, parse_id, read_requirements
from helpers_config import roots_around
from test_samples import ROOT

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
