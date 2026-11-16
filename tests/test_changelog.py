"""tools/changelog.py: entry files in changelog.d/, compiled into CHANGELOG.md (#160).

Every test builds a temporary tree with CRLF line endings, as the Windows CI
leg checks files out, runs the tool's functions or `main(argv, root=...)` on
it, and compares against hand-written LF text. Expected texts are literals
here, never built from the tool's own constants, so a check and its subject
never share a source. The real tree is read by `test_check_repository_root`
and `test_check_sees_real_entries` only, through REAL_ROOT; an autouse fixture
points the tool's ROOT at a folder that does not exist, so a call that forgets
`root=` fails instead of rewriting the real CHANGELOG.md.
"""

import importlib
import shutil
from pathlib import Path

import pytest

import changelog
from helpers_docs import markdown_text

REAL_ROOT = Path(__file__).resolve().parents[1]
F = "changelog.d/"
FOLDER = object()  # a make_tree value: plant a folder, not a file


def crlf(text):
    return text.replace("\n", "\r\n").encode("utf-8")


def make_tree(root, changelog_text, files):
    """Write CHANGELOG.md (unless None) and changelog.d/ (unless None), CRLF.

    A `files` value that is bytes is written as is; FOLDER plants a folder."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if changelog_text is not None:
        (root / "CHANGELOG.md").write_bytes(crlf(changelog_text))
        assert b"\r\n" in (root / "CHANGELOG.md").read_bytes()
    if files is not None:
        folder = root / "changelog.d"
        folder.mkdir(exist_ok=True)
        for name, content in files.items():
            if content is FOLDER:
                (folder / name).mkdir()
            elif isinstance(content, bytes):
                (folder / name).write_bytes(content)
            else:
                (folder / name).write_bytes(crlf(content))
    return root


def snapshot(root):
    """{posix relpath: bytes} for every file under `root`."""
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def run(capsys, root, *argv):
    """(exit code, stdout, stderr) of the tool's main on `root`."""
    rc = changelog.main(list(argv), root=root)
    out, err = capsys.readouterr()
    return rc, out, err


def _real_state():
    paths = [REAL_ROOT / "CHANGELOG.md"]
    folder = REAL_ROOT / "changelog.d"
    if folder.is_dir():
        paths += sorted(folder.iterdir())
    return {p.name: (p.read_bytes() if p.is_file() else None) for p in paths}


@pytest.fixture(autouse=True)
def real_tree_untouched(tmp_path, monkeypatch):
    """No test reaches the real tree by default, and none changes it."""
    monkeypatch.setattr(changelog, "ROOT", tmp_path / "no-root")
    before = _real_state()
    yield
    assert _real_state() == before


# --- the AC fixture --------------------------------------------------------------

FIXTURE_CHANGELOG = """\
# Changelog

A fixture for tools/changelog.py.

## [Unreleased]

New entries are files in changelog.d/; README's "Versioning & releases" says how.

### Added

- Carried one, which wraps
  onto a second line.

- Carried two.

## [0.1.0] — 2026-11-14

### Added

- The first release.
"""

FIXTURE_FILES = {
    "9.added.md": "- Nine, which wraps\n  onto a second line.\n",
    "10.added.md": "- Ten, first.\n\n- Ten, second.\n",
    "10.fixed.md": "- Ten fixed.\n",
    "security-policy.security.md": "- A security policy.\n",
}


# --- check -------------------------------------------------------------------------

def test_check_accepts_valid_entries(tmp_path, capsys):
    files = {name: FIXTURE_FILES[name]
             for name in ("9.added.md", "10.added.md", "security-policy.security.md")}
    files["blank-edges.changed.md"] = "\n\n- Edged.\n\n\n"
    root = make_tree(tmp_path / "t", None, files)
    assert changelog.check(root) == []
    assert run(capsys, root, "check") == (0, "changelog: check: no problems in 4 entry files\n", "")


@pytest.mark.parametrize("folder", ["absent", "empty"])
def test_check_missing_or_empty_folder(tmp_path, capsys, folder):
    root = make_tree(tmp_path / "t", None, None if folder == "absent" else {})
    assert changelog.check(root) == []
    assert run(capsys, root, "check") == (0, "changelog: check: no problems in 0 entry files\n", "")


def test_check_one_file(tmp_path, capsys):
    root = make_tree(tmp_path / "t", None, {"9.added.md": "- Nine.\n"})
    assert run(capsys, root, "check") == (0, "changelog: check: no problems in 1 entry file\n", "")


def test_check_accepts_bom(tmp_path):
    root = make_tree(tmp_path / "t", None, {"9.added.md": b"\xef\xbb\xbf- Nine.\r\n"})
    assert changelog.check(root) == []


NAME_RULE = "it must be <key>.<kind>.md"
KIND_LIST = "added, changed, deprecated, removed, fixed, security"
SLUG_RULE = "must start with a letter and hold only lowercase letters, digits and single hyphens"
PROBLEM_CASES = {
    # case: (name, content, the problem)
    "no-kind": ("79.md", "- x\n", f"{F}79.md: the name has no kind; {NAME_RULE}"),
    "bare-md": (".md", "- x\n", f"{F}.md: the name has no kind; {NAME_RULE}"),
    "unknown-kind": ("79.feature.md", "- x\n",
                     f"{F}79.feature.md: unknown kind 'feature'; it must be one of {KIND_LIST}"),
    "capital-kind": ("79.Added.md", "- x\n",
                     f"{F}79.Added.md: unknown kind 'Added'; it must be one of {KIND_LIST}"),
    "double-md": ("79.added.md.md", "- x\n",
                  f"{F}79.added.md.md: unknown kind 'md'; it must be one of {KIND_LIST}"),
    "txt": ("79.added.txt", "- x\n", f"{F}79.added.txt: the name does not end in .md; {NAME_RULE}"),
    "gitkeep": (".gitkeep", "", f"{F}.gitkeep: the name does not end in .md; {NAME_RULE}"),
    "backup": ("160.changed.md~", "- x\n",
               f"{F}160.changed.md~: the name does not end in .md; {NAME_RULE}"),
    "leading-zero": ("079.added.md", "- x\n",
                     f"{F}079.added.md: the issue number '079' must be positive with no leading zero"),
    "zero": ("0.added.md", "- x\n",
             f"{F}0.added.md: the issue number '0' must be positive with no leading zero"),
    "digit-slug": ("9-lives.added.md", "- x\n", f"{F}9-lives.added.md: the slug '9-lives' {SLUG_RULE}"),
    "capital-slug": ("Security.security.md", "- x\n",
                     f"{F}Security.security.md: the slug 'Security' {SLUG_RULE}"),
    "double-hyphen": ("a--b.added.md", "- x\n", f"{F}a--b.added.md: the slug 'a--b' {SLUG_RULE}"),
    "trailing-hyphen": ("a-.added.md", "- x\n", f"{F}a-.added.md: the slug 'a-' {SLUG_RULE}"),
    "empty-slug": (".added.md", "- x\n", f"{F}.added.md: the slug '' {SLUG_RULE}"),
    "dotted-slug": ("a.b.added.md", "- x\n", f"{F}a.b.added.md: the slug 'a.b' {SLUG_RULE}"),
    "empty": ("79.added.md", "", f"{F}79.added.md: empty; the body must be one or more list items"),
    "blank-lines": ("79.added.md", "\n\n",
                    f"{F}79.added.md: empty; the body must be one or more list items"),
    "prose-first": ("79.added.md", "Intro.\n- x\n",
                    f"{F}79.added.md: line 1 is not part of a list item"),
    "star-item": ("79.added.md", "* x\n", f"{F}79.added.md: line 1 is not part of a list item"),
    "continuation-after-blank": ("79.added.md", "- x\n\n  more\n",
                                 f"{F}79.added.md: line 3 is not part of a list item"),
    "tab-continuation": ("79.added.md", "- x\n\tmore\n",
                         f"{F}79.added.md: line 2 is not part of a list item"),
    "not-utf8": ("79.added.md", b"- \xff\n", f"{F}79.added.md: not UTF-8"),
    "folder": ("sub", FOLDER, f"{F}sub: not a file; changelog.d/ holds only entry files"),
}


@pytest.mark.parametrize("case", PROBLEM_CASES)
def test_check_problem(tmp_path, case):
    name, content, problem = PROBLEM_CASES[case]
    root = make_tree(tmp_path / "t", None, {name: content})
    assert changelog.check(root) == [problem]


def test_check_reports_every_bad_file(tmp_path, capsys):
    root = make_tree(tmp_path / "t", None, {
        "9.feature.md": "- x\n", "10.added.md": "- Ten.\n", "079.added.md": "- x\n"})
    expected = [
        f"{F}079.added.md: the issue number '079' must be positive with no leading zero",
        f"{F}9.feature.md: unknown kind 'feature'; it must be one of {KIND_LIST}",
    ]
    assert changelog.check(root) == expected
    assert run(capsys, root, "check") == (1, "", "".join(f"changelog: {p}\n" for p in expected))


def test_check_repository_root():
    # The AC's "holds this PR's entry" is checked by listing the folder at
    # preship: the next release deletes 160.changed.md, so no pin here.
    assert changelog.check(REAL_ROOT) == []


def test_check_sees_real_entries(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    if (REAL_ROOT / "changelog.d").is_dir():
        shutil.copytree(REAL_ROOT / "changelog.d", root / "changelog.d")
    make_tree(root, None, {"079.added.md": "- x\n"})
    assert changelog.check(root) == [
        f"{F}079.added.md: the issue number '079' must be positive with no leading zero"]


# --- the ## [Unreleased] reader, the renderer and preview ----------------------------

EXPECTED_PREVIEW = """\
## [Unreleased]

### Added

- Carried one, which wraps
  onto a second line.
- Carried two.
- Nine, which wraps
  onto a second line.
- Ten, first.
- Ten, second.

### Fixed

- Ten fixed.

### Security

- A security policy.
"""

BARE_CHANGELOG = """\
# Changelog

A fixture for tools/changelog.py.

## [Unreleased]

New entries are files in changelog.d/; README's "Versioning & releases" says how.

## [0.1.0] — 2026-11-14

### Added

- The first release.
"""


def planted(text, old, new):
    """`text` with its one `old` replaced by `new`."""
    assert text.count(old) == 1
    return text.replace(old, new)


def test_preview_fixture(tmp_path, capsys):
    root = make_tree(tmp_path / "t", FIXTURE_CHANGELOG, FIXTURE_FILES)
    before = snapshot(root)
    assert run(capsys, root, "preview") == (0, EXPECTED_PREVIEW, "")
    assert snapshot(root) == before


@pytest.mark.parametrize("case", ["absent", "empty-folder", "headings-only"])
def test_preview_nothing_waiting(tmp_path, capsys, case):
    text = BARE_CHANGELOG
    if case == "headings-only":
        text = planted(text, "says how.\n\n", "says how.\n\n### Added\n\n### Fixed\n\n")
    root = make_tree(tmp_path / "t", text, None if case == "absent" else {})
    assert run(capsys, root, "preview") == (0, "## [Unreleased]\n", "")


def test_preview_key_order(tmp_path, capsys):
    text = planted(BARE_CHANGELOG, "says how.\n\n", "says how.\n\n### Added\n\n- Carried.\n\n")
    root = make_tree(tmp_path / "t", text, {
        "10.added.md": "- Ten.\n", "2.added.md": "- Two.\n",
        "beta.added.md": "- Beta.\n", "alpha.added.md": "- Alpha.\n"})
    expected = ("## [Unreleased]\n\n### Added\n\n"
                "- Carried.\n- Two.\n- Ten.\n- Alpha.\n- Beta.\n")
    assert run(capsys, root, "preview") == (0, expected, "")


UNRELEASED_TAIL = "says how.\n\n### Added\n"
PREVIEW_REFUSALS = {
    # case: (CHANGELOG text, entry files, stderr after each `changelog: `)
    "bad-file": (FIXTURE_CHANGELOG, {**FIXTURE_FILES, "079.added.md": "- x\n"}, [
        f"{F}079.added.md: the issue number '079' must be positive with no leading zero",
        "preview refused: fix the changelog.d/ problems above"]),
    "prose": (planted(FIXTURE_CHANGELOG, UNRELEASED_TAIL, "says how.\n\nSome prose.\n\n### Added\n"),
              FIXTURE_FILES, [
        "CHANGELOG.md line 9: ## [Unreleased] holds prose other than the pointer line; "
        "move it into an entry file or delete it"]),
    "pointer-after-heading": (
        planted(FIXTURE_CHANGELOG, "- Carried two.\n",
                "- Carried two.\n\nNew entries are files in changelog.d/; "
                "README's \"Versioning & releases\" says how.\n"),
        FIXTURE_FILES, [
        "CHANGELOG.md line 16: ## [Unreleased] holds prose other than the pointer line; "
        "move it into an entry file or delete it"]),
    "unknown-heading": (planted(FIXTURE_CHANGELOG, UNRELEASED_TAIL,
                                "says how.\n\n### Notes\n\n### Added\n"), FIXTURE_FILES, [
        "CHANGELOG.md line 9: ## [Unreleased] holds a heading other than the six kinds: '### Notes'"]),
    "item-no-heading": (planted(FIXTURE_CHANGELOG, UNRELEASED_TAIL,
                                "says how.\n\n- Stray.\n\n### Added\n"), FIXTURE_FILES, [
        "CHANGELOG.md line 9: ## [Unreleased] holds an item under no kind heading"]),
    "no-unreleased": (planted(FIXTURE_CHANGELOG, "## [Unreleased]\n\n", ""), FIXTURE_FILES, [
        "CHANGELOG.md has no '## [Unreleased]' line"]),
}


@pytest.mark.parametrize("case", PREVIEW_REFUSALS)
def test_preview_refuses(tmp_path, capsys, case):
    text, files, lines = PREVIEW_REFUSALS[case]
    root = make_tree(tmp_path / "t", text, files)
    before = snapshot(root)
    assert run(capsys, root, "preview") == (1, "", "".join(f"changelog: {x}\n" for x in lines))
    assert snapshot(root) == before


# --- release <version> <date> --------------------------------------------------------

EXPECTED_RELEASED = """\
# Changelog

A fixture for tools/changelog.py.

## [Unreleased]

New entries are files in changelog.d/; README's "Versioning & releases" says how.

## [9.9.9] — 2026-12-01

### Added

- Carried one, which wraps
  onto a second line.
- Carried two.
- Nine, which wraps
  onto a second line.
- Ten, first.
- Ten, second.

### Fixed

- Ten fixed.

### Security

- A security policy.

## [0.1.0] — 2026-11-14

### Added

- The first release.
"""

POINTER_TEXT = 'New entries are files in changelog.d/; README\'s "Versioning & releases" says how.'
R1_999 = ("CHANGELOG.md already has a section for 9.9.9; add to it with --fold, "
          "or release a new version")


def released_tree(tmp_path):
    root = make_tree(tmp_path / "t", FIXTURE_CHANGELOG, FIXTURE_FILES)
    assert changelog.main(["release", "9.9.9", "2026-12-01"], root=root) == 0
    return root


def test_release_fixture(tmp_path, capsys):
    root = make_tree(tmp_path / "t", FIXTURE_CHANGELOG, FIXTURE_FILES)
    assert run(capsys, root, "release", "9.9.9", "2026-12-01") == (
        0, "changelog: wrote ## [9.9.9] — 2026-12-01: 7 items, 4 entry files deleted\n", "")
    assert (root / "CHANGELOG.md").read_bytes() == EXPECTED_RELEASED.encode("utf-8")
    assert list((root / "changelog.d").iterdir()) == []


def test_preview_matches_release(tmp_path, capsys):
    root = make_tree(tmp_path / "t", FIXTURE_CHANGELOG, FIXTURE_FILES)
    rc, shown, _ = run(capsys, root, "preview")
    assert rc == 0
    assert run(capsys, root, "release", "9.9.9", "2026-12-01")[0] == 0
    lines = (root / "CHANGELOG.md").read_bytes().decode("utf-8").split("\n")
    start = lines.index("## [9.9.9] — 2026-12-01")
    end = lines.index("## [0.1.0] — 2026-11-14")
    section = lines[start:end]
    while section and section[-1] == "":
        section.pop()
    assert "\n".join(["## [Unreleased]"] + section[1:]) + "\n" == shown


def test_release_first_section(tmp_path):
    text = ("# Changelog\n\nA fixture for tools/changelog.py.\n\n## [Unreleased]\n\n"
            f"{POINTER_TEXT}\n\n### Added\n\n- Carried.\n")
    root = make_tree(tmp_path / "t", text, None)
    changelog.release(root, "1.0.0", "2026-12-01")
    assert (root / "CHANGELOG.md").read_bytes().decode("utf-8") == (
        "# Changelog\n\nA fixture for tools/changelog.py.\n\n## [Unreleased]\n\n"
        f"{POINTER_TEXT}\n\n## [1.0.0] — 2026-12-01\n\n### Added\n\n- Carried.\n")


def test_release_restores_pointer(tmp_path):
    text = planted(FIXTURE_CHANGELOG, f"{POINTER_TEXT}\n\n", "")
    root = make_tree(tmp_path / "t", text, FIXTURE_FILES)
    changelog.release(root, "9.9.9", "2026-12-01")
    assert (root / "CHANGELOG.md").read_bytes() == EXPECTED_RELEASED.encode("utf-8")


def test_release_refusal_order(tmp_path, capsys):
    text = planted(FIXTURE_CHANGELOG, "## [0.1.0]",
                   "## [9.9.9] — 2026-11-20\n\n### Added\n\n- Older.\n\n## [0.1.0]")
    root = make_tree(tmp_path / "t", text, {**FIXTURE_FILES, "079.added.md": "- x\n"})
    before = snapshot(root)
    assert run(capsys, root, "release", "9.9.9", "2026-12-01") == (1, "", f"changelog: {R1_999}\n")
    assert snapshot(root) == before


def test_release_write_fails(tmp_path, capsys, monkeypatch):
    root = make_tree(tmp_path / "t", FIXTURE_CHANGELOG, FIXTURE_FILES)
    before = snapshot(root)

    def refuse(self, data):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "write_bytes", refuse)
    assert run(capsys, root, "release", "9.9.9", "2026-12-01") == (
        1, "", "changelog: CHANGELOG.md: could not write (Permission denied); nothing changed\n")
    assert snapshot(root) == before


def test_release_unlink_fails(tmp_path, capsys, monkeypatch):
    root = make_tree(tmp_path / "t", FIXTURE_CHANGELOG, FIXTURE_FILES)
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == "10.added.md":
            raise PermissionError(13, "Permission denied")
        real_unlink(self, missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    assert run(capsys, root, "release", "9.9.9", "2026-12-01") == (
        1, "", "changelog: CHANGELOG.md now holds these items; delete these files by hand, "
               "do not fold them: changelog.d/10.added.md\n")
    assert (root / "CHANGELOG.md").read_bytes() == EXPECTED_RELEASED.encode("utf-8")
    assert sorted(p.name for p in (root / "changelog.d").iterdir()) == ["10.added.md"]


@pytest.mark.parametrize("text", [
    "9.9.9", "09.9.9", "1\u0662.0.0",
    "## [9.9.9] — 2026-12-01", "## [9.9.9]", "## [9.9.9] - 2026-12-01"])
def test_predicates_match_versioning_test(text):
    versioning = importlib.import_module("test_plugin_versioning")
    for ours, theirs in ((changelog.SEMVER, versioning.SEMVER),
                         (changelog.RELEASE_HEADING, versioning.RELEASE_HEADING)):
        a, b = ours.fullmatch(text), theirs.fullmatch(text)
        assert (a and a.groups()) == (b and b.groups())


# --- refusals change nothing --------------------------------------------------------

RELEASE_REFUSALS = {
    # case: (CHANGELOG text, entry files, argv after `release`, stderr after each `changelog: `)
    "existing-section": (FIXTURE_CHANGELOG, FIXTURE_FILES, ["0.1.0", "2026-12-01"], [
        "CHANGELOG.md already has a section for 0.1.0; add to it with --fold, "
        "or release a new version"]),
    "undated-section": (planted(FIXTURE_CHANGELOG, "## [0.1.0]", "## [9.9.9]\n\n## [0.1.0]"),
                        FIXTURE_FILES, ["9.9.9", "2026-12-01"], [R1_999]),
    **{f"version-{v}": (FIXTURE_CHANGELOG, FIXTURE_FILES, [v, "2026-12-01"], [shown])
       for v, shown in [
           ("9.9", "'9.9' is not a plain X.Y.Z version"),
           ("v9.9.9", "'v9.9.9' is not a plain X.Y.Z version"),
           ("09.9.9", "'09.9.9' is not a plain X.Y.Z version"),
           ("9.9.9-rc.1", "'9.9.9-rc.1' is not a plain X.Y.Z version"),
           ("9.9.9.0", "'9.9.9.0' is not a plain X.Y.Z version"),
           ("1٢.0.0", "'1٢.0.0' is not a plain X.Y.Z version")]},
    **{f"date-{d}": (FIXTURE_CHANGELOG, FIXTURE_FILES, ["9.9.9", d], [shown])
       for d, shown in [
           ("2026-12-1", "'2026-12-1' is not a YYYY-MM-DD date"),
           ("20261201", "'20261201' is not a YYYY-MM-DD date"),
           ("2026-13-01", "'2026-13-01' is not a YYYY-MM-DD date"),
           ("2026-02-30", "'2026-02-30' is not a YYYY-MM-DD date"),
           ("2026-W48-2", "'2026-W48-2' is not a YYYY-MM-DD date")]},
    "nothing-waiting": (BARE_CHANGELOG, None, ["9.9.9", "2026-12-01"], [
        "nothing is waiting: no entry files in changelog.d/ and no items under ## [Unreleased]"]),
    "bad-file": (FIXTURE_CHANGELOG, {**FIXTURE_FILES, "079.added.md": "- x\n"},
                 ["9.9.9", "2026-12-01"], [
        f"{F}079.added.md: the issue number '079' must be positive with no leading zero",
        "release refused: fix the changelog.d/ problems above"]),
    **{case: (PREVIEW_REFUSALS[case][0], FIXTURE_FILES, ["9.9.9", "2026-12-01"],
              PREVIEW_REFUSALS[case][2])
       for case in ("prose", "unknown-heading", "item-no-heading", "no-unreleased")},
}


@pytest.mark.parametrize("case", RELEASE_REFUSALS)
def test_release_refuses(tmp_path, capsys, case):
    text, files, argv, lines = RELEASE_REFUSALS[case]
    root = make_tree(tmp_path / "t", text, files)
    before = snapshot(root)
    assert run(capsys, root, "release", *argv) == (1, "", "".join(f"changelog: {x}\n" for x in lines))
    assert snapshot(root) == before


@pytest.mark.parametrize("argv", [
    ["release", "9.9.9"], ["release", "9.9.9", "2026-12-01", "--fold"], ["release"],
    ["release", "9.9.9", "--fol"], ["bogus"]], ids=" ".join)
def test_release_usage_error(tmp_path, capsys, argv):
    root = make_tree(tmp_path / "t", FIXTURE_CHANGELOG, FIXTURE_FILES)
    before = snapshot(root)
    with pytest.raises(SystemExit) as e:
        changelog.main(argv, root=root)
    assert e.value.code == 2
    assert snapshot(root) == before


# --- release <version> --fold ---------------------------------------------------------

EXPECTED_FOLDED = """\
# Changelog

A fixture for tools/changelog.py.

## [Unreleased]

New entries are files in changelog.d/; README's "Versioning & releases" says how.

## [9.9.9] — 2026-12-01

### Added

- Carried one, which wraps
  onto a second line.
- Carried two.
- Nine, which wraps
  onto a second line.
- Ten, first.
- Ten, second.

### Removed

- Twelve removed.

### Fixed

- Ten fixed.
- Eleven fixed.

### Security

- A security policy.

## [0.1.0] — 2026-11-14

### Added

- The first release.
"""


def replant(root, old, new):
    """Rewrite the tree's CHANGELOG.md, CRLF, with its one `old` replaced by `new`."""
    path = root / "CHANGELOG.md"
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    path.write_bytes(crlf(planted(text, old, new)))


def test_fold_fixture(tmp_path, capsys):
    root = released_tree(tmp_path)
    make_tree(root, None, {"11.fixed.md": "- Eleven fixed.\n", "12.removed.md": "- Twelve removed.\n"})
    capsys.readouterr()
    assert run(capsys, root, "release", "9.9.9", "--fold") == (
        0, "changelog: folded 2 items into ## [9.9.9] — 2026-12-01: 2 entry files deleted\n", "")
    assert (root / "CHANGELOG.md").read_bytes() == EXPECTED_FOLDED.encode("utf-8")
    assert list((root / "changelog.d").iterdir()) == []


def test_fold_carries_unreleased_lines(tmp_path, capsys):
    root = released_tree(tmp_path)
    replant(root, f"{POINTER_TEXT}\n\n", f"{POINTER_TEXT}\n\n### Fixed\n\n- Carried late.\n\n")
    capsys.readouterr()
    assert run(capsys, root, "release", "9.9.9", "--fold") == (
        0, "changelog: folded 1 item into ## [9.9.9] — 2026-12-01: 0 entry files deleted\n", "")
    assert (root / "CHANGELOG.md").read_bytes() == planted(
        EXPECTED_RELEASED, "- Ten fixed.\n", "- Ten fixed.\n- Carried late.\n").encode("utf-8")


def r8(newest, version):
    return (f"--fold adds only to the newest dated section, which is {newest}, not {version}; "
            "release a new version instead")


ELEVEN = {"11.fixed.md": "- Eleven fixed.\n"}
FOLD_REFUSALS = {
    # case: (starting tree, (old, new) planted in CHANGELOG.md or None, entry files,
    #        version, stderr after each `changelog: `)
    "older-section": ("released", None, ELEVEN, "0.1.0", [r8("9.9.9", "0.1.0")]),
    "no-section": ("released", None, ELEVEN, "9.9.8", [r8("9.9.9", "9.9.8")]),
    "nothing-waiting": ("released", None, {}, "9.9.9", [
        "nothing is waiting: no entry files in changelog.d/ and no items under ## [Unreleased]"]),
    "bad-file": ("released", None, {**ELEVEN, "079.added.md": "- x\n"}, "9.9.9", [
        f"{F}079.added.md: the issue number '079' must be positive with no leading zero",
        "release refused: fix the changelog.d/ problems above"]),
    "undated-newest": ("released", ("## [9.9.9] — 2026-12-01", "## [9.9.9]"), ELEVEN, "9.9.9",
                       [r8("9.9.9", "9.9.9")]),
    "bad-version": ("released", None, ELEVEN, "9.9", ["'9.9' is not a plain X.Y.Z version"]),
    "unreleased-fixture": ("fixture", None, FIXTURE_FILES, "9.9.9", [r8("0.1.0", "9.9.9")]),
    "no-dated-section": ("fixture", ("\n## [0.1.0] — 2026-11-14\n\n### Added\n\n- The first release.\n", ""),
                         FIXTURE_FILES, "9.9.9", [r8("none", "9.9.9")]),
}


def fold_tree(tmp_path, start, plant, files):
    if start == "released":
        root = released_tree(tmp_path)
    else:
        root = make_tree(tmp_path / "t", FIXTURE_CHANGELOG, {})
    if plant:
        replant(root, *plant)
    return make_tree(root, None, files)


@pytest.mark.parametrize("case", FOLD_REFUSALS)
def test_fold_refuses(tmp_path, capsys, case):
    start, plant, files, version, lines = FOLD_REFUSALS[case]
    root = fold_tree(tmp_path, start, plant, files)
    before = snapshot(root)
    capsys.readouterr()
    assert run(capsys, root, "release", version, "--fold") == (
        1, "", "".join(f"changelog: {x}\n" for x in lines))
    assert snapshot(root) == before


HAND_EDITS = {
    # case: (old, new, the planted line's number)
    "prose": ("## [9.9.9] — 2026-12-01\n\n",
              "## [9.9.9] — 2026-12-01\n\nUpgrade note: edit your config.\n\n", 11),
    "unknown-heading": ("- Ten, second.\n\n### Fixed", "- Ten, second.\n\n### Notes\n\n### Fixed", 21),
    # A kind heading out of KINDS order differs from render() at the first
    # kind heading line, where ### Security now stands instead of ### Added.
    "out-of-order": ("2026-12-01\n\n### Added\n",
                     "2026-12-01\n\n### Security\n\n- A security policy.\n\n### Added\n", 11),
    "blank-between-items": ("- Ten, first.\n- Ten, second.\n", "- Ten, first.\n\n- Ten, second.\n", 19),
    "empty-kind": ("- Ten, second.\n\n### Fixed", "- Ten, second.\n\n### Deprecated\n\n### Fixed", 21),
}


@pytest.mark.parametrize("case", HAND_EDITS)
def test_fold_refuses_hand_edited_section(tmp_path, capsys, case):
    old, new, line = HAND_EDITS[case]
    root = released_tree(tmp_path)
    replant(root, old, new)
    if case == "out-of-order":
        replant(root, "- Ten fixed.\n\n### Security\n\n- A security policy.\n", "- Ten fixed.\n")
    make_tree(root, None, ELEVEN)
    before = snapshot(root)
    capsys.readouterr()
    assert run(capsys, root, "release", "9.9.9", "--fold") == (1, "", (
        f"changelog: CHANGELOG.md line {line}: ## [9.9.9] — 2026-12-01 is not in the form "
        "release writes, so --fold cannot add to it; release a new version instead\n"))
    assert snapshot(root) == before


# --- the real CHANGELOG.md's pointer line and header ---------------------------------

def test_real_changelog_pointer_line():
    lines = markdown_text(REAL_ROOT / "CHANGELOG.md").split("\n")
    assert lines.count("## [Unreleased]") == 1
    at = lines.index("## [Unreleased]")
    assert lines[at + 1:at + 4] == ["", changelog.POINTER, ""]
    assert lines[at + 4].startswith(("### ", "## "))


def test_real_changelog_header_says_where_entries_go():
    text = markdown_text(REAL_ROOT / "CHANGELOG.md")
    header = " ".join(text[:text.index("\n## [Unreleased]\n")].split())
    assert "`changelog.d/`" in header
    assert "line under `## [Unreleased]`" not in header
