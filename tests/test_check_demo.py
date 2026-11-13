"""tools/check_demo.py: the demo output check and the Pages source check (#45).

Every --pages-source input is hand-written bytes here, never a captured
response. The output-folder tests build the demo once from a clean clone of
HEAD (tests/helpers_demo.py) and edit a copy per row; no test writes the
real samples/out or samples/history/out.
"""

import html
import os
import re
import shutil
import tomllib
from pathlib import Path

import pytest

import check_demo
from gantry.stamp import BUILT_STAMP
from helpers_config import lands
from helpers_demo import ROOT, clean_clone, run_child, stderr_of
from helpers_history import require_git
from sample_roles import BUSY, HOSTILE_SITE, HOSTILE_TITLE, NO_WBS, OWN_UNIT, SMALL, UNESTIMATED

MUST = '; it must be "workflow" (Settings, Pages, Source: GitHub Actions)'
OK_LINE = 'check_demo: pages source: build_type is "workflow"'

PAGES_SOURCE = {
    "workflow": (b'{"url": "x", "build_type": "workflow", "source": {"branch": "main"}}', None),
    "legacy": (b'{"build_type": "legacy", "source": {"branch": "main", "path": "/"}}', 'build_type is "legacy"'),
    "null": (b'{"build_type": null}', "build_type is null"),
    "long": (b'{"build_type": "' + b"x" * 60 + b'"}', "build_type is " + ('"' + "x" * 60)[:40]),
    "not-found-body": (b'{"message": "Not Found", "status": "404"}', "the response has no build_type"),
    "empty": (b"", "the response is empty"),
    "whitespace": (b" \r\n\t\n", "the response is empty"),
    "not-json": (b"<html>Not Found</html>", "the response is not JSON"),
    "not-utf8": (b'{"build_type": "\xff"}', "the response is not JSON"),
    "json-list": (b'[{"build_type": "workflow"}]', "the response is not a JSON object"),
    "json-null": (b"null", "the response is not a JSON object"),
    "workflow-wrong-case": (b'{"build_type": "Workflow"}', 'build_type is "Workflow"'),
    "missing-file": (None, "cannot read the file"),
}


@pytest.mark.parametrize("case", list(PAGES_SOURCE))
def test_pages_source(case, tmp_path, capsys):
    data, problem = PAGES_SOURCE[case]
    file = tmp_path / "pages-source.json"
    if data is not None:
        file.write_bytes(data)
    rc = check_demo.main(["--pages-source", str(file)])
    out = capsys.readouterr()
    if problem is None:
        assert rc == 0
        assert out.out.splitlines() == [OK_LINE]
        assert out.err == ""
        assert check_demo.pages_source_problem(data) is None
    else:
        assert rc == 1
        assert out.err.splitlines() == [f"check_demo: pages source: {problem}{MUST}"]
        assert out.out == ""
        assert str(tmp_path) not in out.err
        if data is not None:
            assert check_demo.pages_source_problem(data) == problem + MUST


def test_pages_source_prints_nothing_else(tmp_path, capsys):
    file = tmp_path / "pages-source.json"
    file.write_bytes(b'{"build_type": "legacy", "html_url": "secret-looking-value"}')
    assert check_demo.main(["--pages-source", str(file)]) == 1
    assert "secret-looking-value" not in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    pytest.param([], id="neither"),
    pytest.param(["folder", "--pages-source", "file"], id="both"),
    pytest.param(["--pages-source", ""], id="empty-file"),
    pytest.param([""], id="empty-folder"),
    pytest.param(["--pages-source"], id="no-file"),
    pytest.param(["--unknown", "folder"], id="unknown-flag"),
    pytest.param(["a", "b"], id="two-folders"),
])
def test_usage(argv, capsys):
    with pytest.raises(SystemExit) as raised:
        check_demo.main(argv)
    assert raised.value.code == 2
    assert "usage:" in capsys.readouterr().err


# --- the output-folder check ---------------------------------------------------------

AS_OF = tomllib.loads((ROOT / "samples" / "gantry.sample.toml").read_bytes().decode("utf-8"))["as_of"]
STAMP = f"sample data as of {AS_OF}"
ESCAPED_TITLE = html.escape(HOSTILE_TITLE)
SHOWN_CORE = html.escape("Render <b>this</b>")
OWN = f"sites/{OWN_UNIT}.html"
QUARRY = f"sites/{UNESTIMATED}.html"
HOSTILE = f"sites/{HOSTILE_SITE}.html"
SMALL_CASED = SMALL.capitalize()
CLONE_HISTORY = []  # the demo_out clone's samples/history/out, set by the fixture


@pytest.fixture(scope="session")
def demo_out(tmp_path_factory):
    """The demo built from a clean clone of HEAD; never the real samples/out."""
    require_git()
    clone = clean_clone(tmp_path_factory.mktemp("check-demo"))
    ran = run_child(clone, "2031-05-06", "main")
    assert ran.returncode == 0, stderr_of(ran)
    CLONE_HISTORY.append(clone / "samples" / "history" / "out")
    return clone / "samples" / "out"


@pytest.fixture
def copy(demo_out, tmp_path):
    return Path(shutil.copytree(demo_out, tmp_path / "out"))


def text_of(folder, rel):
    return (folder / rel).read_bytes().decode("utf-8")


def swap(folder, rel, old, new, count=-1):
    text = text_of(folder, rel)
    assert old in text, (rel, old)
    (folder / rel).write_bytes(text.replace(old, new, count).encode("utf-8"))


def sub(folder, rel, pattern, new):
    text = text_of(folder, rel)
    changed, n = re.subn(pattern, new, text, count=1, flags=re.S)
    assert n == 1, (rel, pattern)
    (folder / rel).write_bytes(changed.encode("utf-8"))


def insert(folder, rel, markup):
    swap(folder, rel, "</main>", markup + "</main>", 1)


def cross_href(folder):
    """The first cross-site href on the own-unit site's page."""
    found = re.search(r'href="([a-z]+\.html#[^"]+)"', text_of(folder, OWN))
    assert found
    return found.group(1)


def write(folder, rel, data):
    (folder / rel).parent.mkdir(parents=True, exist_ok=True)
    (folder / rel).write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


def remove(folder, rel):
    (folder / rel).unlink()


def history_copy(folder):
    shutil.copytree(CLONE_HISTORY[-1], folder / "history", ignore=shutil.ignore_patterns(".git"))


def empty(folder):
    for p in list(folder.iterdir()):
        shutil.rmtree(p) if p.is_dir() else p.unlink()


def mis_cased_file(folder):
    os.rename(folder / "sites" / f"{SMALL}.html", folder / "sites" / f"{SMALL_CASED}.html")


def page_and_index(name):
    def edit(folder):
        write(folder, name, text_of(folder, "index.html"))
        insert(folder, "index.html", f'<a href="{name}">x</a>')

    return edit


PENDING = "pending on the expected list (#{}) but present; mark its entry required in the same change"

# (edit, the finding it must produce); a finding ending "(line " is matched as a prefix.
EDITS = {
    "unlinked-page": (lambda f: swap(f, "index.html", f'<a href="sites/{BUSY}.html">{BUSY}</a>', f"<span>{BUSY}</span>"),
                      f"sites/{BUSY}.html: not linked from index.html"),
    "missing-file": (lambda f: swap(f, OWN, f'href="{cross_href(f)}"', 'href="nowhere.html#x"'),
                     f"{OWN}: link 'nowhere.html#x' names no file in the folder"),
    "missing-anchor": (lambda f: swap(f, OWN, f'href="{cross_href(f)}"', f'href="{BUSY}.html#leaf-nowhere"'),
                       f"{OWN}: link '{BUSY}.html#leaf-nowhere' names no anchor in sites/{BUSY}.html"),
    "link-leaves-folder": (lambda f: swap(f, OWN, f'href="{cross_href(f)}"', 'href="../../outside.html"'),
                           f"{OWN}: link '../../outside.html' leaves the folder"),
    "fragment-only-missing": (lambda f: insert(f, QUARRY, '<a href="#nowhere">x</a>'),
                              f"{QUARRY}: link '#nowhere' names no anchor in {QUARRY}"),
    "directory-href": (lambda f: insert(f, "index.html", '<a href="sites/">x</a>'),
                       "index.html: link 'sites/' names no file in the folder"),
    "mis-cased-href": (lambda f: swap(f, "index.html", f'href="sites/{SMALL}.html"', f'href="Sites/{SMALL_CASED}.html"'),
                       f"index.html: link 'Sites/{SMALL_CASED}.html' names no file in the folder"),
    "mis-cased-file": (mis_cased_file, f"sites/{SMALL_CASED}.html: no sample site is named {SMALL_CASED}"),
    "required-view-missing": (lambda f: remove(f, "index.html"),
                              "index.html: required by the expected list (#11) and missing"),
    "required-overlay-missing": (lambda f: swap(f, HOSTILE, 'id="trace"', 'id="not-trace"'),
                                 f"sites/{SMALL}.html: element trace required by the expected list (#20) is missing"),
    "pending-view-present": (page_and_index("strategic.html"), "strategic.html: " + PENDING.format(23)),
    "pending-data-present": (lambda f: write(f, "week-plan.json", "{}\n"), "week-plan.json: " + PENDING.format(61)),
    "unlisted-top-page": (page_and_index("other.html"), "other.html: on no entry of the expected list"),
    "unlisted-nested-page": (page_and_index("extra/view.html"), "extra/view.html: on no entry of the expected list"),
    "unlisted-data-file": (lambda f: write(f, "sites/notes.txt", "notes\n"),
                           "sites/notes.txt: on no entry of the expected list"),
    "history-copy": (history_copy, f"history/{BUSY}/WBS.md: on no entry of the expected list"),
    "site-page-missing": (lambda f: remove(f, QUARRY), f"{QUARRY}: no page for the sample site {UNESTIMATED}"),
    "not-built-site-has-page": (lambda f: write(f, f"sites/{NO_WBS}.html", text_of(f, QUARRY)),
                                f"sites/{NO_WBS}.html: {NO_WBS} is a NOT_BUILT site and has a page"),
    "unknown-site-page": (lambda f: write(f, "sites/nowhere.html", text_of(f, QUARRY)),
                          "sites/nowhere.html: no sample site is named nowhere"),
    "not-built-site-linked": (lambda f: swap(f, "index.html", f'<span class="name">{NO_WBS}</span>',
                                             f'<a href="sites/{NO_WBS}.html">{NO_WBS}</a>'),
                              f"index.html: links a page for the NOT_BUILT site {NO_WBS}"),
    "not-built-site-unnamed": (lambda f: sub(f, "index.html", f'<li><span class="name">{NO_WBS}</span>.*?</li>', ""),
                               f"index.html: does not name the NOT_BUILT site {NO_WBS}"),
    "stamp-missing": (lambda f: swap(f, QUARRY, f'<p class="label stamp">{STAMP}</p>', ""),
                      f'{QUARRY}: no stamp "{STAMP}"'),
    "stamp-wrong-date": (lambda f: swap(f, QUARRY, f'stamp">{STAMP}<', 'stamp">sample data as of 2001-01-01<'),
                         f'{QUARRY}: no stamp "{STAMP}"'),
    "stamp-twice": (lambda f: insert(f, QUARRY, f'<p class="stamp">{STAMP}</p>'), f'{QUARRY}: no stamp "{STAMP}"'),
    "built-stamp": (lambda f: insert(f, QUARRY, "<p>" + BUILT_STAMP.format(when="2031-05-06 12:00 UTC", date=AS_OF) + "</p>"),
                    f"{QUARRY}: carries a build time (built stamp)"),
    "data-built-at": (lambda f: swap(f, "index.html", '<main class="wrap">', '<main class="wrap" data-built-at="x">'),
                      "index.html: carries a build time (data-built-at)"),
    "external-resource": (lambda f: insert(f, QUARRY, '<img src="https://example.invalid/x.png">'),
                          f"{QUARRY}: may load an external resource: src on <img> is not empty or a #fragment (line "),
    "title-as-markup": (lambda f: swap(f, HOSTILE, ESCAPED_TITLE, HOSTILE_TITLE, 1),
                        f"{HOSTILE}: shows the sample hostile title as markup"),
    "title-as-markup-truncated": (lambda f: swap(f, OWN, f'">{SHOWN_CORE}', '">Render <b>this</b>', 1),
                                  f"{OWN}: shows the sample hostile title as markup"),
    "title-not-shown": (lambda f: swap(f, HOSTILE, SHOWN_CORE, "Plain words"),
                        f"{HOSTILE}: does not show the sample hostile title"),
    "page-rules-with-title": (lambda f: insert(f, HOSTILE, '<p onclick="x()">x</p>'),
                              f"{HOSTILE}: breaks the page rules with the sample hostile title planted: "
                              "attribute not allowed: onclick on <p> (line "),
    "not-utf8": (lambda f: write(f, QUARRY, b"<p>\xff</p>"), f"{QUARRY}: is not UTF-8"),
    "empty-folder": (empty, ".: the folder holds no page"),
}


def findings_hold(findings, expected):
    if expected.endswith("(line "):
        return any(f.startswith(expected) for f in findings)
    return expected in findings


def tree_bytes(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


@pytest.mark.parametrize("case", list(EDITS))
def test_check_fails(case, copy, tmp_path, capsys):
    edit, expected = EDITS[case]
    before = tree_bytes(copy)
    edit(copy)
    assert tree_bytes(copy) != before, "the edit changed nothing"
    findings = check_demo.check(copy)
    assert findings_hold(findings, expected), findings
    assert findings == sorted(findings)
    # test_every_finding_is_relative, row by row: no finding holds the folder's path.
    assert [f for f in findings if str(tmp_path) in f] == []
    assert check_demo.main([str(copy)]) == 1
    out = capsys.readouterr()
    assert out.err.splitlines() == [f"check_demo: {f}" for f in findings] + [f"check_demo: {len(findings)} problem(s)"]
    assert out.out == ""


def test_check_missing_folder(tmp_path, capsys):
    assert check_demo.check(tmp_path / "absent") == [".: the folder holds no page"]
    assert check_demo.main([str(tmp_path / "absent")]) == 1


def test_demo_output_passes(demo_out, capsys):
    assert check_demo.check(demo_out) == []
    assert check_demo.main([str(demo_out)]) == 0
    out = capsys.readouterr()
    assert out.out.splitlines() == ["check_demo: ok: 5 pages, 4 site pages, 7 expected entries"]
    assert out.err == ""


@pytest.mark.parametrize("case", ["required-absent", "pending-overlay-present"])
def test_expected_arg(case, copy):
    if case == "required-absent":
        expected = tuple(e._replace(state="required") if e.path == "strategic.html" else e for e in check_demo.EXPECTED)
        problem = "strategic.html: required by the expected list (#23) and missing"
    else:
        expected = tuple(e._replace(state="pending") if e.kind == "overlay" else e for e in check_demo.EXPECTED)
        problem = f"{HOSTILE}: element trace is " + PENDING.format(20)
    assert expected != check_demo.EXPECTED
    assert check_demo.check(copy) == []
    assert check_demo.check(copy, expected=expected) == [problem]


def test_expected_list_is_well_formed():
    entries = check_demo.EXPECTED
    assert {e.kind for e in entries} <= {"view", "sites", "overlay", "data"}
    assert {e.state for e in entries} <= {"required", "pending"}
    assert all(isinstance(e.issue, int) and e.issue > 0 for e in entries)
    keys = [(e.path, e.element) for e in entries]
    assert len(keys) == len(set(keys))
    assert all((e.element is not None) == (e.kind == "overlay") for e in entries)
    # The states at HEAD; a later view's change edits this table with its entry.
    assert [tuple(e) for e in entries] == [
        ("view", "index.html", None, 11, "required"),
        ("sites", "sites/*.html", None, 11, "required"),
        ("overlay", f"sites/{SMALL}.html", "trace", 20, "required"),
        ("view", "strategic.html", None, 23, "pending"),
        ("view", "daily.html", None, 34, "pending"),
        ("view", "week.html", None, 38, "pending"),
        ("data", "week-plan.json", None, 61, "pending"),
    ]


def test_links_agree_with_helpers(demo_out):
    links = check_demo.links(demo_out)
    compared = 0
    for page, href, problem in links:
        if href.startswith(("#", "/")) or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", href):
            continue
        assert (problem is None) == lands(demo_out, page, href), (page, href, problem)
        compared += 1
    assert compared >= 9


# --- docs/demo.md ----------------------------------------------------------------------

import contextlib
import importlib
import io

DOC = ROOT / "docs" / "demo.md"
TOOL_RE = re.compile(r"tools/(\w+)\.py")
FLAG_RE = re.compile(r"(?<![\w-])--[a-z][a-z-]*")


def doc_text():
    return DOC.read_bytes().decode("utf-8").replace("\r\n", "\n")


def test_doc_lists_every_expected_entry():
    rows = [line for line in doc_text().split("\n") if line.startswith("|")]
    for e in check_demo.EXPECTED:
        named = [r for r in rows if f"`{e.path}`" in r and f"#{e.issue}" in r and f"`{e.state}`" in r
                 and (e.element is None or f"`{e.element}`" in r)]
        assert len(named) == 1, e


def tool_help(name):
    module = importlib.import_module(name)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        module.main(["--help"])
    return out.getvalue()


def test_doc_commands_name_real_tools():
    flags = {}
    for line in doc_text().split("\n"):
        tool = None
        for token in re.finditer(r"tools/(\w+)\.py|(?<![\w-])--[a-z][a-z-]*", line):
            if token.group(1):
                tool = token.group(1)
                assert (ROOT / "tools" / f"{tool}.py").is_file(), tool
                flags.setdefault(tool, set())
            elif tool is not None:
                flags[tool].add(token.group(0))
    assert {"demo_build", "check_demo", "pages_artifact"} <= set(flags)
    assert flags["check_demo"] >= {"--pages-source"} and flags["pages_artifact"] >= {"--repo-url"}
    for tool, given in flags.items():
        if given:
            text = tool_help(tool)
            assert [f for f in sorted(given) if f not in text] == [], tool
