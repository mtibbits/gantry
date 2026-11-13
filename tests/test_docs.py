"""The public docs keep their promises (#46).

Every relative link in README.md and under docs/ resolves, with its anchor
by GitHub's heading rule; every doc under docs/ is listed once in the docs
index; each view's README line links what the view's own issue adds, or
reads "(planned)" until then. Each planted test calls the same helper the
real-tree test calls (tests/helpers_docs.py), so the guard and its probe
share one predicate.
"""

import re
from pathlib import Path

import pytest

from helpers_docs import (
    doc_files,
    heading_anchors,
    index_links,
    index_problems,
    link_report,
    markdown_text,
    privacy_link_problems,
    relative_links,
    unfenced_lines,
    view_item,
    view_line_problems,
)

ROOT = Path(__file__).resolve().parents[1]

# Each target is what the view's own issue adds, never a doc an earlier issue creates.
VIEW_MAP = {
    "Worksite page": "docs/worksite-page.md",                        # #8, #9, #11
    "Strategic page": "docs/strategic-page.md",                      # #23 creates it with strategic.html
    "Daily screen": "docs/daily-screen.md#the-page",                 # #34 adds "The page"; #32 creates the file
    "Week view": "docs/week-plan.md#the-week-view-page",             # #38 adds the section; #36 creates the file
    "Requirements trace": "docs/worksite-page.md#reading-the-trace", # #20 (landed)
}


def plant(root, files):
    """Write each {relative path: text} under root; a path ending in / is a folder."""
    for rel, text in files.items():
        path = root / rel
        if rel.endswith("/"):
            path.mkdir(parents=True, exist_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return root


# --- heading anchors -----------------------------------------------------------


@pytest.mark.parametrize("text, anchors", [
    ("## Reading `WBS.md`", ["reading-wbsmd"]),
    ("### `h:`", ["h"]),
    ("### `depends_on`", ["depends_on"]),
    ("### P0 and devagent's `wbs order`", ["p0-and-devagents-wbs-order"]),
    ("### `[site.<project>]` here and `[site.<name>]` in the installation config",
     ["siteproject-here-and-sitename-in-the-installation-config"]),
    ("## Two limits, the summary and the marks", ["two-limits-the-summary-and-the-marks"]),
    ("## X ##", ["x"]),
    ("## A\n## A\n## A", ["a", "a-1", "a-2"]),
    ("## See [the doc](x.md) now", ["see-the-doc-now"]),
    ("## In\n```\n## Fenced\n```\n~~~\n## Tilde\n~~~\n## Out", ["in", "out"]),
    ("    # Indented\n## Real", ["real"]),
    # An unclosed fence makes the rest of the file code (CommonMark).
    ("## A\n```\n## B\n", ["a"]),
    # A backtick opener whose info string holds a backtick is no fence.
    ("``` a`b\n## C\n", ["c"]),
    # A closing fence followed by CR LF still closes (Windows working tree).
    ("```\r\ncode\r\n```\r\n## After\r\n", ["after"]),
])
def test_heading_anchors_follow_githubs_rule(text, anchors):
    assert heading_anchors(text) == anchors


# --- relative links ------------------------------------------------------------

LINKS_TEXT = """\
[a](x.md) ![i](img.png) [h](#here) [u](../up/)
`[t](code.md)` and `span
[w](wrapped.md)` end

```
[f](fenced.md)
```

[s](https://e.example/) [m](mailto:a@e.example) [n](//host)
[b](<sp ace.md>) [t](t.md "title")

- `open
- [y](y.md)
- close`

[ref]: ref.md
"""


def test_relative_links_skip_code_and_absolute():
    assert relative_links(LINKS_TEXT) == [
        "x.md", "img.png", "#here", "../up/", "sp ace.md", "t.md", "y.md", "ref.md"]
    # An unclosed fence hides every later link.
    assert relative_links("[a](a.md)\n```\n[b](b.md)\n") == ["a.md"]
    # A CRLF text reads as its LF twin.
    assert relative_links(LINKS_TEXT.replace("\n", "\r\n")) == relative_links(LINKS_TEXT)


def test_every_relative_link_resolves():
    checked, problems = link_report(ROOT)
    assert problems == []
    assert checked >= 50
    docs = doc_files(ROOT)
    assert len(docs) >= 10
    assert set(docs) <= {p.relative_to(ROOT).as_posix() for p in (ROOT / "docs").rglob("*.md")}


def test_planted_broken_link_fails(tmp_path):
    root = plant(tmp_path / "r", {"README.md": "[x](docs/nope.md)\n", "docs/a.md": "# A\n"})
    assert link_report(root)[1] == ["README.md: docs/nope.md: no such file or folder"]
    plant(root, {"docs/nope.md": "# Nope\n"})
    assert link_report(root) == (1, [])


def test_planted_bad_anchor_fails(tmp_path):
    root = plant(tmp_path / "r", {"README.md": "", "docs/a.md": "## Real\n",
                                  "docs/b.md": "[r](a.md#real)\n"})
    assert link_report(root)[1] == []
    for fragment in ("unreal", "real-1"):
        plant(root, {"docs/b.md": f"[r](a.md#{fragment})\n"})
        assert link_report(root)[1] == [f"docs/b.md: a.md#{fragment}: no heading with that anchor"]
    plant(root, {"docs/a.md": "## Real\n\n## Real\n", "docs/b.md": "[r](a.md#real-1)\n"})
    assert link_report(root)[1] == []
    plant(root, {"docs/b.md": "[r](a.md#real-2)\n"})
    assert link_report(root)[1] == ["docs/b.md: a.md#real-2: no heading with that anchor"]
    plant(root, {"docs/pic.png": "x", "docs/sub/c.md": "# C\n",
                 "docs/b.md": "[p](pic.png#x) [d](sub/#x)\n"})
    assert link_report(root)[1] == [
        "docs/b.md: pic.png#x: a fragment needs a Markdown target",
        "docs/b.md: sub/#x: a fragment needs a Markdown target",
    ]


def test_planted_link_case_and_root(tmp_path):
    root = plant(tmp_path / "r", {"README.md": "", "docs/config.md": "# C\n",
                                  "samples/x.txt": "x", "docs/b.md": "[c](Config.md)\n"})
    assert link_report(root)[1] == ["docs/b.md: Config.md: no such file or folder (exact case)"]
    plant(root, {"docs/b.md": "[c](config.md) [s](../samples/)\n"})
    assert link_report(root) == (2, [])
    plant(root, {"docs/b.md": "[o](../../out.md) [r](/abs.md)\n"})
    assert link_report(root)[1] == [
        "docs/b.md: ../../out.md: leaves the tree",
        "docs/b.md: /abs.md: leaves the tree",
    ]


# --- the docs index ------------------------------------------------------------


def test_every_doc_is_listed_in_the_index():
    assert index_problems(ROOT) == []
    assert len([d for d in doc_files(ROOT) if d != "docs/README.md"]) >= 11


def test_index_lists_config_first():
    assert index_links(markdown_text(ROOT / "docs" / "README.md"))[0] == "config.md"


def test_planted_unlisted_doc_fails(tmp_path):
    root = plant(tmp_path / "r", {"docs/README.md": "# Index\n\n- [a.md](a.md): A\n",
                                  "docs/a.md": "# A\n", "docs/sub/b.md": "# B\n"})
    assert index_problems(root) == ["docs/sub/b.md: not listed in docs/README.md"]
    plant(root, {"docs/README.md": "# Index\n\nSee [a](a.md#a).\n\n- [a.md](a.md): A\n- [b](sub/b.md): B\n"})
    assert index_problems(root) == []
    plant(root, {"docs/README.md": "- [a.md](a.md): A\n- [again](a.md#a)\n- [b](sub/b.md)\n"})
    assert index_problems(root) == ["docs/a.md: listed 2 times in docs/README.md"]
    (root / "docs" / "README.md").unlink()
    assert index_problems(root) == ["docs/README.md: missing"]


# --- the README's view lines ---------------------------------------------------


def test_view_lines_match_their_targets():
    assert view_line_problems(ROOT, VIEW_MAP) == []
    readme = markdown_text(ROOT / "README.md")
    assert [len(view_item(readme, label)) for label in VIEW_MAP] == [1] * len(VIEW_MAP)


STRATEGIC = {"Strategic page": "docs/strategic-page.md"}
PLANNED = "- **Strategic page**: across all projects (planned)\n"
LINKED = "- **Strategic page**: across all projects, see\n  [the doc](docs/strategic-page.md)\n"


def test_planted_view_planned_but_target_exists(tmp_path):
    root = plant(tmp_path / "r", {"README.md": PLANNED, "docs/strategic-page.md": "# S\n"})
    assert view_line_problems(root, STRATEGIC) == [
        "Strategic page: docs/strategic-page.md exists, so the line must link it"]
    plant(root, {"README.md": LINKED})
    assert view_line_problems(root, STRATEGIC) == []


def test_planted_view_links_missing_target(tmp_path):
    root = plant(tmp_path / "r", {"README.md": LINKED})
    assert view_line_problems(root, STRATEGIC) == [
        "Strategic page: docs/strategic-page.md does not exist, so the line must read (planned) with no link"]
    plant(root, {"README.md": PLANNED})
    assert view_line_problems(root, STRATEGIC) == []


def test_daily_screen_doc_without_the_page_stays_planned(tmp_path):
    daily = {"Daily screen": "docs/daily-screen.md#the-page"}
    linked = "- **Daily screen**: [the page](docs/daily-screen.md#the-page)\n"
    planned = "- **Daily screen**: on a phone (planned)\n"
    root = plant(tmp_path / "r", {"README.md": linked, "docs/daily-screen.md": "# Daily\n\n## Fires\n"})
    assert view_line_problems(root, daily) == [
        "Daily screen: docs/daily-screen.md#the-page does not exist, so the line must read (planned) with no link"]
    plant(root, {"README.md": planned})
    assert view_line_problems(root, daily) == []
    plant(root, {"docs/daily-screen.md": "# Daily\n\n## Fires\n\n## The page\n"})
    assert view_line_problems(root, daily) == [
        "Daily screen: docs/daily-screen.md#the-page exists, so the line must link it"]
    plant(root, {"README.md": linked})
    assert view_line_problems(root, daily) == []


@pytest.mark.parametrize("count", [0, 2])
def test_view_label_found_once(tmp_path, count):
    week = {"Week view": "docs/week-plan.md#the-week-view-page"}
    root = plant(tmp_path / "r", {"README.md": "- **Week view**: the week (planned)\n" * count})
    assert view_line_problems(root, week) == [
        f"Week view: README.md needs exactly one '- **Week view**' item, found {count}"]


# --- the privacy section -------------------------------------------------------

PRIVACY = "# G\n\n## Your data stays private\n\n{body}\n\n## Next\n\n{after}\n"
REFRESH_LINK = "[serving](docs/refresh-and-serve.md)"


@pytest.mark.parametrize("present, body, after, problems", [
    (False, "Text.", "", []),
    (True, "Text.", REFRESH_LINK,
     ["Your data stays private: docs/refresh-and-serve.md exists, so the section must link it"]),
    (True, f"See {REFRESH_LINK}.", "", []),
    (False, f"See {REFRESH_LINK}.", "",
     ["Your data stays private: docs/refresh-and-serve.md does not exist, so the section must not link it"]),
])
def test_planted_privacy_link(tmp_path, present, body, after, problems):
    files = {"README.md": PRIVACY.format(body=body, after=after)}
    if present:
        files["docs/refresh-and-serve.md"] = "# Refresh\n"
    assert privacy_link_problems(plant(tmp_path / "r", files)) == problems


def test_planted_privacy_section_missing(tmp_path):
    root = plant(tmp_path / "r", {"README.md": "# G\n"})
    assert privacy_link_problems(root) == ["README.md: no '## Your data stays private' section"]


def test_privacy_section_links_refresh_and_serve_when_it_exists():
    assert privacy_link_problems(ROOT) == []


# --- the README quickstart (tools/quickstart.py) --------------------------------

QS_BEGIN = "<!-- quickstart:begin -->"
QS_END = "<!-- quickstart:end -->"
GET_CODE_BLOCK = "```sh\ngit clone https://github.com/mtibbits/gantry.git\ncd gantry\n```\n"
BUILD_BLOCK = "```sh\npython3 tools/demo_build.py\n```\n"
GOOD_README = f"# R\n\n{QS_BEGIN}\n\n{GET_CODE_BLOCK}\nThen:\n\n{BUILD_BLOCK}\n{QS_END}\n"
MARKERS = 'quickstart: the README needs one "<!-- quickstart:begin -->" line and one "<!-- quickstart:end -->" line after it'
NOT_GET_CODE = 'quickstart: the "get the code" block is not exactly: git clone https://github.com/mtibbits/gantry.git / cd gantry'


@pytest.fixture
def qs():
    import quickstart

    return quickstart


@pytest.mark.parametrize("text, message", [
    (GOOD_README.replace(QS_BEGIN + "\n", ""), MARKERS),
    (GOOD_README.replace(QS_END + "\n", ""), MARKERS),
    (GOOD_README.replace(QS_BEGIN, "@@").replace(QS_END, QS_BEGIN).replace("@@", QS_END), MARKERS),
    (QS_BEGIN + "\n" + GOOD_README, MARKERS),
    (GOOD_README.replace(BUILD_BLOCK, ""), "quickstart: found 1 fenced blocks between the markers, not 2"),
    (GOOD_README.replace(BUILD_BLOCK, BUILD_BLOCK + "\n" + BUILD_BLOCK),
     "quickstart: found 3 fenced blocks between the markers, not 2"),
    (GOOD_README.replace(BUILD_BLOCK, "```sh\npython3 tools/demo_build.py\n"),
     "quickstart: a fenced block between the markers is not closed"),
    (GOOD_README.replace("cd gantry\n", "cd gantry\nls\n"), NOT_GET_CODE),
    (GOOD_README.replace("cd gantry\n", "cd gantry2\n"), NOT_GET_CODE),
    (GOOD_README.replace("cd gantry\n", "cd gantry  \n"), NOT_GET_CODE),
    (GOOD_README.replace("cd gantry\n", ""), NOT_GET_CODE),
    (GOOD_README.replace("python3 tools/demo_build.py\n", "\n"), 'quickstart: the "build the demo" block is empty'),
])
def test_quickstart_refuses(qs, tmp_path, capsys, text, message):
    readme = tmp_path / "README.md"
    readme.write_text(text, encoding="utf-8", newline="\n")
    assert text != GOOD_README
    out = tmp_path / "b.sh"
    with pytest.raises(qs.Refused, match="^" + re.escape(message.removeprefix("quickstart: ")) + "$"):
        qs.blocks(text)
    assert qs.main(["blocks", str(readme), str(out)]) == 1
    assert capsys.readouterr().err.strip() == message
    assert not out.exists()


def test_quickstart_writes_the_block_verbatim(qs, tmp_path):
    text = GOOD_README.replace("python3 tools/demo_build.py\n", "line one\nline two\n").replace("\n", "\r\n")
    readme = tmp_path / "README.md"
    readme.write_bytes(text.encode("utf-8"))
    out = tmp_path / "b.sh"
    assert qs.main(["blocks", str(readme), str(out)]) == 0
    assert out.read_bytes() == b"line one\nline two\n"
    assert qs.blocks(GOOD_README) == (qs.GET_CODE, ["python3 tools/demo_build.py"])


def test_quickstart_output_check(qs, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    plant(tmp_path, {"out/index.html": "<p>", "out/sites/a.html": "<p>"})
    assert qs.main(["output", "out"]) == 0
    assert capsys.readouterr().out.strip() == "quickstart: ok: out holds index.html and 1 site pages"
    plant(tmp_path, {"bare/sites/a.txt": "x", "bare/index.html": "<p>", "noindex/sites/a.html": "<p>"})
    for folder, message in [("noindex", "quickstart: noindex has no index.html"),
                            ("bare", "quickstart: bare has no sites/<name>.html"),
                            ("nope", "quickstart: nope is not a folder"),
                            ("", "quickstart: no output folder was named")]:
        assert qs.main(["output", folder]) == 1
        assert capsys.readouterr().err.strip() == message


@pytest.mark.parametrize("argv", [[], ["frob"], ["blocks", "README.md"], ["output"], ["output", "a", "b"]])
def test_quickstart_usage(qs, argv):
    with pytest.raises(SystemExit) as exc:
        qs.main(argv)
    assert exc.value.code == 2


WINDOWS_LINE = r"`py -3 tools\demo_build.py`"


def test_readme_quickstart(qs):
    import demo_build

    readme = markdown_text(ROOT / "README.md")
    first, second = qs.blocks(readme)
    assert first == qs.GET_CODE == ["git clone https://github.com/mtibbits/gantry.git", "cd gantry"]
    assert second == ["python3 tools/demo_build.py"]
    lines = readme.split("\n")
    begin = lines.index(QS_BEGIN)
    note = lines[begin - 1]
    assert note.startswith("<!--") and ".github/workflows/quickstart.yml" in note and "tests/test_docs.py" in note
    prerequisites = [line for line in lines[:begin] if line.startswith("Prerequisites:")]
    assert len(prerequisites) == 1
    for needed in ("Python 3.11 or later", "`python3`", "git"):
        assert needed in prerequisites[0]
    assert any(WINDOWS_LINE in line for line in unfenced_lines(readme) if line)
    assert demo_build.output_folder(ROOT).relative_to(ROOT).as_posix() in readme
