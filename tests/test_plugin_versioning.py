"""The plugin's release files follow devagent's release policy (#47).

`.claude-plugin/plugin.json` carries the plugin's version, a plain `X.Y.Z`
semver string (`pyproject.toml` mirrors it; tests/test_cli.py keeps the two
equal). The marketplace entry carries no version, because Claude Code uses
plugin.json's value when both are set, so a copy there can only go stale.
`CHANGELOG.md` opens with `## [Unreleased]` and has a dated section for
plugin.json's version. Both manifests name their author or owner by a name
and a profile url only, and hold no email.

Each check takes a root folder and returns a list of problem strings. The
real-tree tests call it on the repository root; the planted tests call the
same function on a temporary copy of the release files, so no planted test
edits a tracked file. The README pins at the bottom keep the release commands
and the plugin install lines the README promises.
"""

import copy
import json
import re
import shutil
from pathlib import Path

import pytest

from helpers_docs import markdown_text, section, unfenced_lines

ROOT = Path(__file__).resolve().parents[1]

SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")  # fullmatch only; ASCII digits
RELEASE_HEADING = re.compile(r"## \[(?P<version>[^\]]+)\](?: — (?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2}))?")  # fullmatch
UNRELEASED = "## [Unreleased]"
PLUGIN = ".claude-plugin/plugin.json"
MARKETPLACE = ".claude-plugin/marketplace.json"
CHANGELOG = "CHANGELOG.md"
RELEASE_FILES = (PLUGIN, MARKETPLACE, CHANGELOG)
AT = chr(64)  # the planted mailto is built at run time, so no address sits in this source


# --- the checks ----------------------------------------------------------------

def _manifest(root, rel):
    """(document, None), or (None, the one problem that stops reading it)."""
    path = Path(root) / rel
    if not path.is_file():
        return None, f"{rel}: missing"
    try:
        doc = json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, f"{rel}: not valid JSON"
    if not isinstance(doc, dict):
        return None, f"{rel}: not a JSON object"
    return doc, None


def _semver(value):
    return isinstance(value, str) and SEMVER.fullmatch(value) is not None


def version_problems(root):
    doc, problem = _manifest(root, PLUGIN)
    if problem:
        return [problem]
    version = doc.get("version")
    if not _semver(version):
        return [f"{PLUGIN}: version {version!r} is not a semver string"]
    return []


def marketplace_problems(root):
    doc, problem = _manifest(root, MARKETPLACE)
    if problem:
        return [problem]
    plugins = doc.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        return [f"{MARKETPLACE}: plugins is empty or not a list, so no entry is checked"]
    problems = []
    for i, entry in enumerate(plugins):
        if not isinstance(entry, dict):
            problems.append(f"{MARKETPLACE}: plugins[{i}] is not an object")
        elif "version" in entry:
            name = entry.get("name") if isinstance(entry.get("name"), str) else "?"
            problems.append(f"{MARKETPLACE}: plugins[{i}] ({name}) has a version; "
                            "plugin.json carries the version")
    return problems


def _changelog_lines(root):
    """(unfenced lines, None), or (None, the one problem that stops reading it)."""
    path = Path(root) / CHANGELOG
    if not path.is_file():
        return None, f"{CHANGELOG}: missing"
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None, f"{CHANGELOG}: not UTF-8"
    return unfenced_lines(text), None


def release_headings(lines, version):
    """The unfenced lines that are a dated release heading for `version`."""
    found = []
    for line in lines:
        m = RELEASE_HEADING.fullmatch(line) if line is not None else None
        if m and m.group("version") == version and m.group("date"):
            found.append(line)
    return found


def changelog_problems(root):
    lines, problem = _changelog_lines(root)
    if problem:
        return [problem]
    problems = []
    first = next((line for line in lines if line is not None and line.startswith("## ")), None)
    if first != UNRELEASED:
        problems.append(f"{CHANGELOG}: the first section is not '{UNRELEASED}'")
    plugin, unreadable = _manifest(root, PLUGIN)
    if unreadable:
        return problems  # version_problems reports the manifest once
    version = plugin.get("version")
    if not isinstance(version, str):
        problems.append(f"{CHANGELOG}: plugin.json has no version to find a section for")
    elif not release_headings(lines, version):
        problems.append(f"{CHANGELOG}: no '## [{version}] — <date>' section for plugin.json's version")
    return problems


def _person_ok(value):
    return (isinstance(value, dict) and set(value) == {"name", "url"}
            and all(isinstance(value[k], str) and value[k] for k in ("name", "url"))
            and value["url"].startswith("https://"))


def authorship_problems(root):
    problems = []
    for rel, field in ((PLUGIN, "author"), (MARKETPLACE, "owner")):
        doc, unreadable = _manifest(root, rel)
        if unreadable:
            continue  # version_problems or marketplace_problems reports it once
        if not _person_ok(doc.get(field)):
            problems.append(f"{rel}: {field} must hold exactly a name and a url")
        plugins = doc.get("plugins") if rel == MARKETPLACE else None
        for i, entry in enumerate(plugins if isinstance(plugins, list) else []):
            if isinstance(entry, dict) and "author" in entry and not _person_ok(entry["author"]):
                problems.append(f"{rel}: plugins[{i}].author must hold exactly a name and a url")
        if AT in (Path(root) / rel).read_bytes().decode("utf-8"):
            problems.append(f"{rel}: holds '{AT}'; owner and author carry no email")
    return problems


def release_problems(root):
    return (version_problems(root) + marketplace_problems(root)
            + changelog_problems(root) + authorship_problems(root))


# --- the real tree -----------------------------------------------------------------

def test_release_files_have_no_problems():
    assert release_problems(ROOT) == []
    plugin = json.loads((ROOT / PLUGIN).read_text(encoding="utf-8"))
    market = json.loads((ROOT / MARKETPLACE).read_text(encoding="utf-8"))
    assert len(market["plugins"]) == 1
    assert market["plugins"][0]["name"] == plugin["name"] == "gantry"
    lines = unfenced_lines(markdown_text(ROOT / CHANGELOG))
    assert len(release_headings(lines, plugin["version"])) == 1


# --- planted cases on a copy ---------------------------------------------------------

@pytest.fixture
def release_tree(tmp_path):
    root = tmp_path / "r"
    for rel in RELEASE_FILES:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        if (ROOT / rel).is_file():  # a missing one is reported by the assert below
            shutil.copyfile(ROOT / rel, root / rel)
    assert release_problems(root) == []
    return root


def plant_json(root, rel, change):
    """Parse the copy's `rel`, apply `change` to it, write it back; assert it changed."""
    path = root / rel
    before = json.loads(path.read_text(encoding="utf-8"))
    doc = copy.deepcopy(before)
    change(doc)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    assert json.loads(path.read_text(encoding="utf-8")) != before


def plant_changelog(root, change):
    """Rewrite the copy's CHANGELOG from its LF text through `change`; assert it changed."""
    path = root / CHANGELOG
    before = markdown_text(path)
    after = change(before)
    assert after != before
    path.write_text(after, encoding="utf-8", newline="\n")


def plugin_version(root):
    return json.loads((root / PLUGIN).read_text(encoding="utf-8"))["version"]


def _set(key, value):
    return lambda doc: doc.__setitem__(key, value)


def test_planted_copy_is_clean(release_tree):
    assert release_problems(release_tree) == []


ABSENT = object()


@pytest.mark.parametrize("value", ["0.1", "v0.1.0", "01.0.0", "0.1.0.0", "0.1.0\n", "0.1.0-rc.1",
                                   "1\u0662.0.0", 1, ABSENT],  # U+0662 ARABIC-INDIC DIGIT TWO
                         ids=["two-part", "v-prefix", "leading-zero", "four-part", "trailing-newline",
                              "prerelease", "non-ascii-digit", "not-a-string", "absent"])
def test_planted_bad_version(release_tree, value):
    if value is ABSENT:
        plant_json(release_tree, PLUGIN, lambda doc: doc.pop("version"))
    else:
        plant_json(release_tree, PLUGIN, _set("version", value))
    shown = None if value is ABSENT else value
    assert version_problems(release_tree) == [f"{PLUGIN}: version {shown!r} is not a semver string"]


def test_planted_marketplace_entry_version(release_tree):
    plant_json(release_tree, MARKETPLACE, lambda doc: doc["plugins"][0].__setitem__("version", "0.1.0"))
    assert marketplace_problems(release_tree) == [
        f"{MARKETPLACE}: plugins[0] (gantry) has a version; plugin.json carries the version"]


@pytest.mark.parametrize("plugins", [[], ABSENT, {}], ids=["empty-list", "absent", "object"])
def test_planted_empty_plugins(release_tree, plugins):
    if plugins is ABSENT:
        plant_json(release_tree, MARKETPLACE, lambda doc: doc.pop("plugins"))
    else:
        plant_json(release_tree, MARKETPLACE, _set("plugins", plugins))
    assert marketplace_problems(release_tree) == [
        f"{MARKETPLACE}: plugins is empty or not a list, so no entry is checked"]


def test_planted_non_object_entry(release_tree):
    plant_json(release_tree, MARKETPLACE, _set("plugins", ["gantry"]))
    assert marketplace_problems(release_tree) == [f"{MARKETPLACE}: plugins[0] is not an object"]
    assert authorship_problems(release_tree) == []


@pytest.mark.parametrize("version", ["0.0.0", "99.0.0"])
def test_planted_version_without_changelog_section(release_tree, version):
    plant_json(release_tree, PLUGIN, _set("version", version))
    assert changelog_problems(release_tree) == [
        f"{CHANGELOG}: no '## [{version}] — <date>' section for plugin.json's version"]


def test_planted_non_string_version_has_no_section(release_tree):
    plant_json(release_tree, PLUGIN, lambda doc: doc.pop("version"))
    assert changelog_problems(release_tree) == [
        f"{CHANGELOG}: plugin.json has no version to find a section for"]


def _fenced(line):
    return f"```\n{line}\n```"


def _undated(line):
    return line.split(" — ")[0]


def _h3(line):
    return "#" + line


@pytest.mark.parametrize("disguise", [_fenced, _undated, _h3], ids=["fenced", "undated", "h3"])
def test_planted_changelog_heading_not_counted(release_tree, disguise):
    version = plugin_version(release_tree)
    lines = unfenced_lines(markdown_text(release_tree / CHANGELOG))
    [real] = release_headings(lines, version)
    plant_changelog(release_tree, lambda text: text.replace(real, disguise(real)))
    assert changelog_problems(release_tree) == [
        f"{CHANGELOG}: no '## [{version}] — <date>' section for plugin.json's version"]


def test_planted_unreleased_not_first(release_tree):
    def move(text):
        text = text.replace(UNRELEASED + "\n\n", "", 1)
        return text.rstrip("\n") + "\n\n" + UNRELEASED + "\n"
    plant_changelog(release_tree, move)
    assert changelog_problems(release_tree) == [f"{CHANGELOG}: the first section is not '{UNRELEASED}'"]


def test_planted_changelog_missing(release_tree):
    (release_tree / CHANGELOG).unlink()
    assert changelog_problems(release_tree) == [f"{CHANGELOG}: missing"]


def test_planted_changelog_not_utf8(release_tree):
    (release_tree / CHANGELOG).write_bytes(b"# Changelog\n\n\xff\xfe\n")
    assert changelog_problems(release_tree) == [f"{CHANGELOG}: not UTF-8"]


def _mailto_owner(doc):
    doc["owner"]["url"] = "mailto:" + "someone" + AT + "example.org"


AUTHORSHIP_CASES = {
    "author-email": (PLUGIN, lambda doc: doc["author"].__setitem__("email", "x"),
                     [f"{PLUGIN}: author must hold exactly a name and a url"]),
    "owner-mailto": (MARKETPLACE, _mailto_owner,
                     [f"{MARKETPLACE}: owner must hold exactly a name and a url",
                      f"{MARKETPLACE}: holds '{AT}'; owner and author carry no email"]),
    "author-absent": (PLUGIN, lambda doc: doc.pop("author"),
                      [f"{PLUGIN}: author must hold exactly a name and a url"]),
    "entry-author-string": (MARKETPLACE, lambda doc: doc["plugins"][0].__setitem__("author", "x"),
                            [f"{MARKETPLACE}: plugins[0].author must hold exactly a name and a url"]),
}


@pytest.mark.parametrize("case", list(AUTHORSHIP_CASES))
def test_planted_authorship(release_tree, case):
    rel, change, expected = AUTHORSHIP_CASES[case]
    plant_json(release_tree, rel, change)
    assert authorship_problems(release_tree) == expected


@pytest.mark.parametrize("rel, content, expected", [
    (MARKETPLACE, None, f"{MARKETPLACE}: missing"),
    (PLUGIN, "[]", f"{PLUGIN}: not a JSON object"),
    (PLUGIN, '{"name": "gantry", "vers', f"{PLUGIN}: not valid JSON"),
], ids=["marketplace-missing", "plugin-array", "plugin-truncated"])
def test_planted_unreadable_manifest(release_tree, rel, content, expected):
    path = release_tree / rel
    if content is None:
        path.unlink()
    else:
        path.write_text(content, encoding="utf-8", newline="\n")
    check = version_problems if rel == PLUGIN else marketplace_problems
    assert check(release_tree) == [expected]
    # The manifest's own check reports it once; the others say nothing about it.
    assert authorship_problems(release_tree) == []
    if rel == PLUGIN:
        assert changelog_problems(release_tree) == []


def test_release_keeps_versioning_checks(release_tree):
    """tools/changelog.py's release writes a section changelog_problems accepts (#160).

    It runs on a copy of the real CHANGELOG.md, so it also fails, naming the
    line, when the real ## [Unreleased] gains a line release cannot place."""
    import changelog  # here, not at the top: this module still collects when the tool is broken

    plant_json(release_tree, PLUGIN, _set("version", "9.9.9"))
    (release_tree / "changelog.d").mkdir()
    (release_tree / "changelog.d" / "1.added.md").write_bytes(b"- A planted entry.\n")
    assert changelog_problems(release_tree) == [
        f"{CHANGELOG}: no '## [9.9.9] — <date>' section for plugin.json's version"]
    changelog.release(release_tree, "9.9.9", "2026-12-01")
    assert release_problems(release_tree) == []
    lines = markdown_text(release_tree / CHANGELOG).split("\n")
    at = lines.index(UNRELEASED)
    assert lines[at + 1:at + 5] == ["", changelog.POINTER, "", "## [9.9.9] — 2026-12-01"]


# --- the README's release section and plugin install block ------------------------------

RELEASE_SECTION = "## Versioning & releases"
INSTALL_SECTION = "## Install"
PLUGIN_BEGIN = "<!-- plugin-install:begin -->"
PLUGIN_END = "<!-- plugin-install:end -->"
OLD_PROMISE = "comes with the first release"

INSTALL_LINES = (
    "claude plugin marketplace add mtibbits/gantry",
    "claude plugin install gantry@gantry",
    "/gantry:build <installation config>",
    "claude --plugin-dir <clone>",
    "Python 3.11 or later",
)
RELEASE_LINES = (
    "claude plugin validate --strict .",
    "claude plugin tag --push",
    'gh release create gantry--v<version> --verify-tag --title "gantry <version>" --notes-file "<section file>"',
    "gh workflow run quickstart.yml --ref gantry--v<version>",
    "claude plugin marketplace update gantry",
    "claude plugin update gantry@gantry",
    "claude plugin list",
    'git clone --branch gantry--v<version> https://github.com/mtibbits/gantry.git "<clone>"',
    "python3 tools/demo_build.py",
    'git -C "<clone>/samples" rev-parse --show-toplevel',
    "/gantry:build <clone>/samples/gantry.sample.toml",
    'python3 tools/commit_check.py names --text "<file>"',
    "python3 tools/changelog.py release <version> <date>",
    "python3 tools/changelog.py preview",
    # Only the history-less sample site loses its history with <clone>/.git.
    "the history-less sample site",
)


def plugin_block(text):
    """The `## Install` lines between the plugin-install markers, or None."""
    install = section(text, INSTALL_SECTION)
    lines = install.split("\n") if install is not None else []
    if lines.count(PLUGIN_BEGIN) != 1 or lines.count(PLUGIN_END) != 1:
        return None
    begin, end = lines.index(PLUGIN_BEGIN), lines.index(PLUGIN_END)
    return "\n".join(lines[begin + 1:end]) if begin < end else None


def readme_problems(text):
    problems = []
    count = text.split("\n").count(RELEASE_SECTION)
    if count != 1:
        problems.append(f"README.md: '{RELEASE_SECTION}' is a whole line {count} times, not once")
    release = section(text, RELEASE_SECTION)
    for literal in RELEASE_LINES if release is not None else ():
        if literal not in release:
            problems.append(f"README.md: the release section lacks {literal!r}")
    block = plugin_block(text)
    if block is None:
        problems.append(f"README.md: no plugin install block between '{PLUGIN_BEGIN}' and "
                        f"'{PLUGIN_END}' under '{INSTALL_SECTION}'")
    for literal in INSTALL_LINES if block is not None else ():
        if literal not in block:
            problems.append(f"README.md: the plugin install block lacks {literal!r}")
    if OLD_PROMISE in text:
        problems.append(f"README.md: still says a marketplace install {OLD_PROMISE}")
    return problems


@pytest.fixture
def readme():
    text = markdown_text(ROOT / "README.md")
    assert readme_problems(text) == []
    return text


def test_readme_release_section():
    assert readme_problems(markdown_text(ROOT / "README.md")) == []


CUTTING = "### Cutting a release"


def release_step(text, n):
    """"Cutting a release" step n's list item, from `n. ` to `n+1. `, whitespace
    collapsed; "" when the step cannot be found once."""
    lines = (section(text, RELEASE_SECTION) or "").split("\n")
    if lines.count(CUTTING) != 1:
        return ""
    lines = lines[lines.index(CUTTING) + 1:]
    lines = lines[:next((i for i, line in enumerate(lines) if line.startswith("### ")), len(lines))]
    starts = [i for i, line in enumerate(lines) if line.startswith(f"{n}. ")]
    ends = [i for i, line in enumerate(lines) if line.startswith(f"{n + 1}. ")]
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        return ""
    return " ".join(" ".join(lines[starts[0]:ends[0]]).split())


def test_readme_release_steps_name_the_tool(readme):
    # Anchored on the steps, so the convention line repeating a command cannot
    # keep these green after a step loses it (#160).
    assert "python3 tools/changelog.py release <version> <date>" in release_step(readme, 1)
    step2 = release_step(readme, 2)
    assert "python3 tools/changelog.py preview" in step2
    assert "python3 tools/changelog.py release <version> --fold" in step2


def test_release_tag_literals_use_plugin_name():
    name = json.loads((ROOT / PLUGIN).read_text(encoding="utf-8"))["name"]
    prefixes = [p for line in RELEASE_LINES for p in re.findall(r"(?<![\w-])([\w-]+?)--v<version>", line)]
    assert len(prefixes) >= 3
    assert set(prefixes) == {name}


def test_install_literals_use_marketplace_name():
    """`<plugin>@<marketplace>` and `marketplace update <marketplace>` name the real marketplace."""
    market = json.loads((ROOT / MARKETPLACE).read_text(encoding="utf-8"))
    lines = INSTALL_LINES + RELEASE_LINES
    named = [m for line in lines for m in re.findall(r"\bgantry" + AT + r"([\w-]+)", line)]
    named += [m for line in lines for m in re.findall(r"marketplace update ([\w-]+)", line)]
    assert len(named) >= 3
    assert set(named) == {market["name"]}


def test_planted_readme_release_literal_dropped(readme):
    literal = RELEASE_LINES[0]
    assert readme_problems(readme.replace(literal, "")) == [f"README.md: the release section lacks {literal!r}"]


def test_planted_readme_install_literal_dropped(readme):
    literal = INSTALL_LINES[0]
    assert readme_problems(readme.replace(literal, "")) == [
        f"README.md: the plugin install block lacks {literal!r}"]


def test_planted_readme_python_rule_only_outside_block(readme):
    """The plain-CLI sentence above the block cannot stand in for the plugin's Python rule."""
    literal = "Python 3.11 or later"
    head, tail = readme.split(PLUGIN_BEGIN)
    block, rest = tail.split(PLUGIN_END)
    planted = head + PLUGIN_BEGIN + block.replace(literal, "Python") + PLUGIN_END + rest
    assert literal in section(planted, INSTALL_SECTION)
    assert readme_problems(planted) == [f"README.md: the plugin install block lacks {literal!r}"]


def test_planted_readme_old_promise_restored(readme):
    planted = readme.replace(PLUGIN_END, f"A marketplace install {OLD_PROMISE}.\n{PLUGIN_END}")
    assert readme_problems(planted) == [f"README.md: still says a marketplace install {OLD_PROMISE}"]


def test_planted_readme_release_heading_duplicated(readme):
    planted = readme.rstrip("\n") + f"\n\n{RELEASE_SECTION}\n"
    assert readme_problems(planted) == [f"README.md: '{RELEASE_SECTION}' is a whole line 2 times, not once"]


def test_planted_readme_release_heading_missing(readme):
    planted = readme.replace(f"{RELEASE_SECTION}\n", "## Versioning and releases\n")
    assert readme_problems(planted) == [f"README.md: '{RELEASE_SECTION}' is a whole line 0 times, not once"]


def test_planted_readme_markers_missing(readme):
    planted = readme.replace(PLUGIN_END + "\n", "")
    assert readme_problems(planted) == [
        f"README.md: no plugin install block between '{PLUGIN_BEGIN}' and '{PLUGIN_END}' under '{INSTALL_SECTION}'"]
