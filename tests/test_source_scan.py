"""No hard-coded paths in the code, and the package imports only the stdlib (#7).

The scan reads every file under the code roots, comments and docstrings
included, and fails on an absolute path of any operating system or a user
home folder. docs/, tests/ and samples/ are not scanned: docs, fictional
fixtures and refusal tests may hold such forms. Every test source is held to
the same forms by test_every_test_source_holds_no_path_form, so fixtures
build them at run time.
"""

import ast
import shutil
import sys
from pathlib import Path

import pytest

# One predicate with #42's sample check and the commit check (#44).
from commit_check import (
    EXEMPT_PATH,
    PATH_FORMS,
    entry_is_live,
    is_exempt,
    line_hits,
    parse_exemptions,
    path_form_hits,
)
from test_samples import (
    PATH_FORM_CONTROLS,
    ROOT,
    decode_bytes,
    git,
    git_work_tree,
    needs_git,
)

CODE_ROOTS = ("gantry", "commands", "tools", ".claude-plugin")  # a new code root is added here
# The issue names these four; a root dropped from CODE_ROOTS keeps its planted
# controls (below), which then go red.
ISSUE_ROOTS = ("gantry", "commands", "tools", ".claude-plugin")
PLANT_ROOTS = tuple(dict.fromkeys(ISSUE_ROOTS + CODE_ROOTS))
# The allow-list is the commit check's exemption list (#44), read from the work tree.
ALLOW_PARSED = parse_exemptions((ROOT / EXEMPT_PATH).read_bytes().decode("utf-8"))
ALLOW = ALLOW_PARSED[0]


def test_issue_roots_are_code_roots():
    # Deduplicating ISSUE_ROOTS into CODE_ROOTS would disarm the planted controls.
    assert set(ISSUE_ROOTS) <= set(CODE_ROOTS)

FLOOR = {
    "gantry/__init__.py",
    "gantry/__main__.py",
    "gantry/cli.py",
    "gantry/config.py",
    "gantry/sites.py",
    "commands/build.md",
    "tools/gantry-build.sh",
    "tools/commit_check.py",
    "tools/commit_check_exempt.txt",
    ".claude-plugin/plugin.json",
}


def code_files(root, hermetic=False):
    """POSIX relative paths of every file under the code roots."""
    root = Path(root)
    roots = [r for r in CODE_ROOTS if (root / r).is_dir()]
    if not roots:
        return []
    if git_work_tree(root):
        result = git(
            root, "ls-files", "-z", "--cached", "--others", "--exclude-standard",
            "--", *[r + "/" for r in roots], hermetic=hermetic,
        )
        assert result.returncode == 0, decode_bytes(result.stderr)
        return sorted(p for p in decode_bytes(result.stdout).split("\0") if p)
    return sorted(
        p.relative_to(root).as_posix()
        for r in roots
        for p in (root / r).rglob("*")
        if p.is_file() and "__pycache__" not in p.relative_to(root).parts
    )


def _line_scan(root, hermetic):
    """(relpath, form, line, start, end) per hit; the exemption list is read, not scanned."""
    for rel in code_files(root, hermetic=hermetic):
        if rel == EXEMPT_PATH:
            continue
        text = (Path(root) / rel).read_bytes().decode("latin-1")
        lines = text.split("\n")
        for lineno, form, start, end in line_hits(text):
            yield rel, form, lines[lineno - 1], start, end


def raw_scan(root, hermetic=False):
    return [(rel, form, line[start:end]) for rel, form, line, start, end in _line_scan(root, hermetic)]


def scan(root, hermetic=False):
    return [
        (rel, form, line[start:end])
        for rel, form, line, start, end in _line_scan(root, hermetic)
        if not is_exempt(rel, line, start, end, ALLOW)
    ]


# --- the tree ------------------------------------------------------------------


def test_tree_has_no_hard_coded_paths():
    assert scan(ROOT) == []
    files = set(code_files(ROOT))
    assert FLOOR <= files, sorted(FLOOR - files)


# --- positive and negative controls ---------------------------------------------

CONTROLS = PATH_FORM_CONTROLS + [
    ("posix_home", "/" + "home" + "/" + "someone" + "/"),
    ("posix_home", "/" + "Users" + "/" + "someone" + "/"),
]


def test_controls_cover_every_form():
    assert {form for form, _ in CONTROLS} == set(PATH_FORMS)


def bare_tree(tmp_path):
    tree = tmp_path / "tree"
    for r in PLANT_ROOTS:
        (tree / r).mkdir(parents=True)
    assert not git_work_tree(tree)
    return tree


# One tree for all the plant cases: git_work_tree is cached per root, so a
# fresh tree per case would spawn git each time.
@pytest.fixture(scope="module")
def shared_tree(tmp_path_factory):
    return bare_tree(tmp_path_factory.mktemp("plants"))


@pytest.fixture
def plant(shared_tree):
    """Write planted.txt under one code root of the shared tree; remove it after."""
    planted = []

    def write(code_root, text):
        path = shared_tree / code_root / "planted.txt"
        path.write_bytes(text.encode("utf-8") + b"\n")
        planted.append(path)
        return shared_tree

    yield write
    for path in planted:
        path.unlink()


@pytest.mark.parametrize("shape", ["alone", "quoted"])
@pytest.mark.parametrize("code_root", PLANT_ROOTS)
@pytest.mark.parametrize("form,sample", CONTROLS)
def test_planted_form_flagged(plant, form, sample, code_root, shape):
    text = sample if shape == "alone" else 'path = "' + sample + '"'
    tree = plant(code_root, text)
    hits = scan(tree)
    assert (code_root + "/planted.txt", form) in {(rel, f) for rel, f, _ in hits}, hits


@pytest.mark.parametrize("code_root", PLANT_ROOTS)
@pytest.mark.parametrize("text", ["https://x", "a:b", "/gantry:build"])
def test_negative_controls_pass(plant, text, code_root):
    tree = plant(code_root, text)
    assert code_root + "/planted.txt" in code_files(tree)
    assert scan(tree) == []


PLANT = ("~" + "/x\n").encode("ascii")


def test_non_git_pycache_rule_is_whole_part(tmp_path):
    tree = bare_tree(tmp_path)
    (tree / "gantry" / "__pycache__").mkdir()
    (tree / "gantry" / "__pycache__" / "x.pyc").write_bytes(PLANT)
    (tree / "gantry" / "__pycache__x.py").write_bytes(PLANT)
    files = code_files(tree)
    assert "gantry/__pycache__/x.pyc" not in files
    assert "gantry/__pycache__x.py" in files
    assert [rel for rel, _, _ in scan(tree)] == ["gantry/__pycache__x.py"]


@needs_git
def test_git_enumeration_scans_untracked_skips_ignored(tmp_path):
    tree = tmp_path / "gittree"
    (tree / "gantry" / "__pycache__").mkdir(parents=True)
    shutil.copyfile(ROOT / ".gitignore", tree / ".gitignore")
    result = git(tree, "init", "-q", hermetic=True)
    assert result.returncode == 0, decode_bytes(result.stderr)
    assert git_work_tree(tree)
    (tree / "gantry" / "new.py").write_bytes(PLANT)
    (tree / "gantry" / "__pycache__" / "x.pyc").write_bytes(PLANT)
    files = code_files(tree, hermetic=True)
    assert files == ["gantry/new.py"]
    assert [rel for rel, _, _ in scan(tree, hermetic=True)] == ["gantry/new.py"]


def test_allow_list_entries_are_live():
    entries, errors = ALLOW_PARSED
    assert errors == []
    for entry in entries:
        if entry.path.split("/")[0] not in CODE_ROOTS:
            continue
        text = (ROOT / entry.path).read_bytes().decode("latin-1")
        assert entry_is_live(entry, text), f"exemption entry matches nothing: {entry}"


def test_exempt_file_not_scanned(shared_tree):
    # The tool reads the exemption list and does not scan it (rule 3), and so does this scan.
    path = shared_tree / EXEMPT_PATH
    path.write_bytes(("x | absolute-path | " + "~" + "/x | why\n").encode("utf-8"))
    try:
        assert EXEMPT_PATH in code_files(shared_tree)
        assert raw_scan(shared_tree) == []
    finally:
        path.unlink()


# --- stdlib-only imports ----------------------------------------------------------


def non_stdlib_imports(pkg_dir):
    """(relpath, module) for each absolute import outside the stdlib and gantry."""
    pkg_dir = Path(pkg_dir)
    found = []
    for path in sorted(pkg_dir.rglob("*.py")):
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top not in sys.stdlib_module_names and top != "gantry":
                    found.append((path.relative_to(pkg_dir).as_posix(), name))
    return found


def test_gantry_imports_stdlib_only(tmp_path):
    pkg = ROOT / "gantry"
    assert len(list(pkg.rglob("*.py"))) >= 5
    assert non_stdlib_imports(pkg) == []

    planted = tmp_path / "gantry"
    planted.mkdir()
    (planted / "x.py").write_bytes(
        b"import yaml\n"
        b"from requests import get\n"
        b"import tomllib\n"
        b"from . import config\n"
        b"from gantry.config import Key\n"
    )
    assert non_stdlib_imports(planted) == [("x.py", "yaml"), ("x.py", "requests")]


def test_tools_import_stdlib_only():
    # tools/commit_check.py runs in CI and in hooks with no install step (#44).
    assert (ROOT / "tools" / "commit_check.py").is_file()
    assert non_stdlib_imports(ROOT / "tools") == []


# --- test-source hygiene ------------------------------------------------------------


def test_every_test_source_holds_no_path_form():
    sources = sorted((ROOT / "tests").glob("*.py"))
    assert len(sources) >= 6
    offenders = {
        p.name: hits
        for p in sources
        if (hits := path_form_hits(p.read_bytes().decode("utf-8")))
    }
    assert offenders == {}
