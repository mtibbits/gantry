"""File access stays in gantry/roots.py: a static allow-list scan of gantry/ (#60).

Every .py file under gantry/ is parsed with ast (the walk shared with
tests/test_source_scan.py) and its imports and names are checked against the
closed lists below. gantry/roots.py, by that exact path, is parsed for its
imports only: other modules may not take a file-touching name through it.
The walk is the file system, not git's tracked set, so an untracked .py under
gantry/ is scanned too, on purpose.

The rules, each a tag on the report it produces (RULES):
- module: an import must be a PLAIN_MODULES key, a TOUCHING_MODULES key or a
  gantry module, by exact dotted name ("urllib.parse" admits neither
  "urllib.request" nor "urllib").
- name: from a file-touching module only ALLOWED_NAMES (or, for an
  OPEN_MODULES module, every name but those it refuses) may be imported, or
  reached as an attribute through any alias or getattr with a literal name.
  A refused from-import is reported once, at the import; a chain at each use.
- through-gantry: a name taken from another gantry module, or reached as its
  attribute, passes only if its origin there would pass here.
- star: "from M import *" from a file-touching or gantry module.
- rebind: a file-touching module bound, passed or returned as a value
  ("x = os", "f(os.path)") rather than used as the root of a chain.
- builtin: any read of the builtin open, __import__, exec or eval, called or
  not. This is stricter than #60's wording: a module that defines its own
  "open" and reads it is refused too (rename it). An attribute (leaf.open), a
  keyword argument and a dataclass field "open: bool" are not reads and pass.
- keyword: a fromfile_prefix_chars= keyword on any call.
- roots-builder: a call that builds roots (ROOT_BUILDERS): roots come from
  config, so a module cannot make a path readable by naming it a root.

Exceptions (EXCEPTIONS) are keyed by module file and exact name, each with a
reason. An unlisted module (fcntl, say) is reported at its import under its
own name, and each use under the name down to its first attribute
(fcntl.flock): an exception for the import admits only the import, and each
name used needs its own entry. An exception whose module file does not exist,
or that removes no report, is stale and fails the tree test.

What the scan does not decide, including: getattr with a computed name,
sys.modules, eval of a built string (each is deliberate evasion: review and
#43's run-time read spy own those); a Root or Roots reference that is not a
call, such as dataclasses.replace on a Root; an attribute assignment of
fromfile_prefix_chars; a gantry module that re-binds a file-touching name by
assignment rather than import; and reaches through a plain module's
attributes.
"""

import ast
import shutil
from pathlib import Path
from typing import NamedTuple

import pytest

from test_samples import ROOT
from test_source_scan import package_modules

ROOTS_FILE = "gantry/roots.py"  # exempt by exact path; parsed for imports only

# Modules that take no file path: exact dotted name -> reason. A parent does
# not admit a child, nor a child its parent.
PLAIN_MODULES = {
    "re": "regular expressions over strings",
    "json": "loads/dumps on strings; json.load takes a file only roots.py can open",
    "datetime": "dates and times",
    "dataclasses": "record types",
    "html": "escaping text",
    "string": "string constants and templates",
    "itertools": "iteration helpers",
    "functools": "function helpers",
    "typing": "type annotations",
    "enum": "enumerations",
    "math": "arithmetic",
    "hashlib": "hashing bytes",
    "graphlib": "topological sort over values",
    "heapq": "heaps over lists",
    "stat": "interprets a stat result roots.py gave; stats nothing itself",
    "sys": "interpreter state; sys.modules evasion is out of the threat model",
    "tomllib": "tomllib.loads on decoded text; tomllib.load takes a file only roots.py can open",
    "urllib.parse": "quotes and splits URL text (gantry/links.py); opens nothing",
}

# File-touching modules: importable, but only listed names may be imported
# from them or reached as an attribute. Dotted name -> reason it can touch files.
TOUCHING_MODULES = {
    "os": "opens, lists, stats, removes and runs",
    "os.path": "exists, isfile, realpath and the rest stat",
    "posixpath": "os.path under its POSIX name (intent.md, round 1)",
    "pathlib": "concrete Path reads, writes and lists",
    "shutil": "copies, moves, removes; which stats",
    "io": "io.open and FileIO open files",
    "glob": "lists folders",
    "tempfile": "creates files and folders",
    "subprocess": "starts processes",
    "codecs": "codecs.open opens files",
    "logging": "file handlers open files",
    "http.server": "serves files from a folder",
    "argparse": "FileType and fromfile_prefix_chars open files by name",
}

# Names from file-touching modules that open, list, stat, create or remove
# nothing and start no process: dotted name -> reason.
ALLOWED_NAMES = {
    "os.name": "the platform's name; a value",
    "os.fspath": "returns the path text of a path object; opens nothing",
    "os.path": "the module itself; its names are checked one by one",
    "os.path.join": "pure string join",
    "os.path.basename": "pure string split",
    "os.path.dirname": "pure string split",
    "os.path.splitext": "pure string split",
    "os.path.normcase": "pure string case and separator fold",
    "os.path.normpath": "pure string collapse of . and ..",
    "os.path.isabs": "pure string test",
    "os.path.sep": "a constant",
    "os.path.expanduser": "reads the home folder from process state; opens nothing (#7's ~ rule)",
    "os.path.abspath": "joins the working folder from process state; opens nothing",
    "posixpath.join": "pure string join; builds the HEAD:<path> blob name for git; opens nothing",
    "pathlib.PurePath": "a path value with no file-system methods",
    "pathlib.PurePosixPath": "a path value with no file-system methods",
    "pathlib.PureWindowsPath": "a path value with no file-system methods",
    "io.StringIO": "an in-memory text buffer",
    "io.BytesIO": "an in-memory byte buffer",
    "logging.getLogger": "returns a logger; handlers are configured elsewhere",
}

# A module whose every name is allowed but these: module -> {name: reason refused}.
OPEN_MODULES = {"argparse": {"FileType": "opens a file by name"}}

REFUSED_BUILTINS = ("open", "__import__", "exec", "eval")
REFUSED_KEYWORDS = ("fromfile_prefix_chars",)
ROOT_BUILDERS = (
    "gantry.roots.directory_roots",
    "gantry.roots.Root",
    "gantry.roots.Roots",
    "gantry.roots.add_root",
    "gantry.roots.with_file",
    "gantry.roots.bootstrap_roots",
)

RULES = ("module", "name", "through-gantry", "star", "rebind", "builtin", "keyword", "roots-builder")

# Exceptions: (module file, exact dotted name) -> reason. #35's and #64's
# entries are added by whichever of them and #60 lands second: an entry for a
# module that does not exist yet is stale.
WBS_GIT = (
    "run_git is the module's one child process, limited to the git commands _check_args "
    "admits, with no stdin and a timeout; history reads are bounded by #28's repository "
    "rule and, once it lands, #43's history check"
)
FROM_CONFIG = "this module is where roots are built from the loaded config (#59)"
EXCEPTIONS = {
    ("gantry/wbs_history.py", "subprocess.run"): f"starts git: {WBS_GIT}",
    ("gantry/wbs_history.py", "subprocess.DEVNULL"): f"git's stdin: {WBS_GIT}",
    ("gantry/wbs_history.py", "subprocess.TimeoutExpired"): f"git's timeout: {WBS_GIT}",
    ("gantry/wbs_history.py", "os.environ"): (
        "read once, in git_env, to build git's environment with every GIT_* name removed "
        f"and the ceiling set; {WBS_GIT}"
    ),
    ("gantry/config.py", "gantry.roots.bootstrap_roots"): f"the --config file's own root; {FROM_CONFIG}",
    ("gantry/sites.py", "gantry.roots.bootstrap_roots"): f"the --config file's own root; {FROM_CONFIG}",
    ("gantry/sites.py", "gantry.roots.with_file"): f"the devagent_config file the config names; {FROM_CONFIG}",
    ("gantry/sites.py", "gantry.roots.add_root"): f"each root key and site planning folder; {FROM_CONFIG}",
}

# Every module the scan must parse: the 27 under gantry/ at 39f4afa. A later
# issue may extend this list but not shrink it.
KEPT = frozenset({
    "gantry/__init__.py",
    "gantry/__main__.py",
    "gantry/attention.py",
    "gantry/build.py",
    "gantry/cli.py",
    "gantry/config.py",
    "gantry/deps.py",
    "gantry/est_rules.py",
    "gantry/estimates_file.py",
    "gantry/inputs.py",
    "gantry/links.py",
    "gantry/model.py",
    "gantry/network.py",
    "gantry/portfolio_file.py",
    "gantry/priority.py",
    "gantry/render.py",
    "gantry/requirements.py",
    "gantry/roots.py",
    "gantry/schedule.py",
    "gantry/sites.py",
    "gantry/stamp.py",
    "gantry/trace.py",
    "gantry/wbs_history.py",
    "gantry/wbs_stopgap.py",
    "gantry/views/__init__.py",
    "gantry/views/index.py",
    "gantry/views/worksite.py",
})

# Per rule, what to do about a report; every text names roots.py and the allow-list.
REMEDY = {
    "module": "it is not on the allow-list in tests/test_confine_scan.py: a module that takes "
    "no file path goes in PLAIN_MODULES with a reason; file access goes through gantry/roots.py",
    "name": "file access goes through gantry/roots.py; a name that touches no file goes in "
    "ALLOWED_NAMES, or a reviewed use in EXCEPTIONS, on the allow-list in "
    "tests/test_confine_scan.py with a reason",
    "through-gantry": "another gantry module took it from a file-touching module: import what "
    "you need from where it is allowed, and file access goes through gantry/roots.py "
    "(allow-list: tests/test_confine_scan.py)",
    "star": "a star import hides which names it takes: import each name, checked against the "
    "allow-list in tests/test_confine_scan.py; file access goes through gantry/roots.py",
    "rebind": "a file-touching module used as a value escapes the name check: reach only its "
    "names on the allow-list in tests/test_confine_scan.py; file access goes through "
    "gantry/roots.py",
    "builtin": "this builtin reaches files or loads code: file access goes through "
    "gantry/roots.py; a reviewed use goes in EXCEPTIONS on the allow-list in "
    "tests/test_confine_scan.py with a reason",
    "keyword": "it makes argparse open files named on the command line: drop it; file access "
    "goes through gantry/roots.py (allow-list: tests/test_confine_scan.py)",
    "roots-builder": "roots come from config: only gantry/roots.py builds roots, and a "
    "reviewed caller goes in EXCEPTIONS on the allow-list in tests/test_confine_scan.py "
    "with a reason",
}


class Hit(NamedTuple):
    file: str  # posix path from the package's parent: gantry/planted_open_call.py
    line: int
    name: str
    rule: str


class Report(NamedTuple):
    refused: list  # Hit, exceptions already removed
    parsed: set  # every module file the scan parsed
    stale: list  # exceptions that removed nothing: (file, name, why)


def message(hit):
    return f"{hit.file}:{hit.line}: {hit.name} is refused: {REMEDY[hit.rule]}"


def not_parsed_message(file):
    return (
        f"{file}: not parsed: every module on KEPT in tests/test_confine_scan.py must be "
        "parsed, so a walk that finds nothing cannot pass"
    )


def stale_message(file, name, why):
    return f"{file}: exception for {name} is stale ({why}): remove it from EXCEPTIONS in tests/test_confine_scan.py"


class Bound(NamedTuple):
    origin: str  # dotted: a module (os.path, gantry.roots) or a name in one (os.remove)
    module: bool


def is_gantry(name):
    return name == "gantry" or name.startswith("gantry.")


def module_of(file):
    """gantry/views/index.py -> (gantry.views.index, gantry.views)."""
    parts = file[: -len(".py")].split("/")
    package = ".".join(parts[:-1])
    return (package if parts[-1] == "__init__" else ".".join(parts)), package


def import_from_module(node, package):
    """The absolute module a from-import names, relative levels resolved."""
    if not node.level:
        return node.module
    base = package.split(".")
    if node.level > 1:
        base = base[: -(node.level - 1)]
    return ".".join([*base, *([node.module] if node.module else [])])


def allowed_name(module, name):
    if f"{module}.{name}" in ALLOWED_NAMES:
        return True
    return module in OPEN_MODULES and name not in OPEN_MODULES[module]


def allowed_module(name):
    return name in PLAIN_MODULES or name in TOUCHING_MODULES or is_gantry(name)


class Package:
    """Every module's import table, and the rules that resolve a dotted name."""

    def __init__(self, modules):
        self.modules = {name for name, _, _ in modules}
        self.tables = {name: self.table(tree, package) for name, package, tree in modules}

    def known(self, name):
        return name in PLAIN_MODULES or name in TOUCHING_MODULES or name in self.modules

    def table(self, tree, package):
        table = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        table[alias.asname] = Bound(alias.name, True)
                    else:
                        top = alias.name.split(".")[0]
                        table[top] = Bound(top, True)
            elif isinstance(node, ast.ImportFrom):
                source = import_from_module(node, package)
                for alias in node.names:
                    if alias.name != "*":
                        full = f"{source}.{alias.name}"
                        table[alias.asname or alias.name] = Bound(full, self.known(full))
        return table

    def split(self, dotted):
        """(longest known module prefix or None, the names after it)."""
        parts = dotted.split(".")
        for i in range(len(parts), 0, -1):
            prefix = ".".join(parts[:i])
            if self.known(prefix):
                return prefix, parts[i:]
        return None, parts

    def refusal(self, dotted, seen=frozenset()):
        """(name, rule) if a reach of dotted is refused, else None."""
        prefix, rest = self.split(dotted)
        if prefix is None:
            return ".".join(rest[:2]), "module"
        if prefix in PLAIN_MODULES:
            return None
        if prefix in TOUCHING_MODULES:
            if not rest:
                return prefix, "rebind"
            return None if allowed_name(prefix, rest[0]) else (f"{prefix}.{rest[0]}", "name")
        if not rest:
            return None
        name, tail = rest[0], rest[1:]
        if (hop := self.follow(prefix, name, seen)) is None:
            return None
        origin, seen = hop
        if self.refusal(origin, seen) or (tail and self.refusal(".".join([origin, *tail]), seen)):
            return f"{prefix}.{name}", "through-gantry"
        return None

    def canonical(self, dotted, seen=frozenset()):
        """dotted with every re-export through a gantry module followed to its origin."""
        prefix, rest = self.split(dotted)
        if prefix in self.modules and rest and (hop := self.follow(prefix, rest[0], seen)):
            origin, seen = hop
            return self.canonical(".".join([origin, *rest[1:]]), seen)
        return dotted

    def follow(self, module, name, seen):
        """(origin, seen plus this hop) for a name gantry module binds; None if unbound or a cycle."""
        bound = self.tables[module].get(name)
        if bound is None or (module, name) in seen:
            return None
        return bound.origin, seen | {(module, name)}


def chain_of(node, table):
    """(Bound, attribute names) for a Name/Attribute chain rooted at an imported name."""
    attrs = []
    while isinstance(node, ast.Attribute):
        attrs.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name) and node.id in table:
        return table[node.id], attrs[::-1]
    return None


def dotted_of(chain):
    bound, attrs = chain
    return ".".join([bound.origin, *attrs])


def check_imports(node, package, rules, refuse):
    if isinstance(node, ast.Import):
        for alias in node.names:
            if not allowed_module(alias.name):
                refuse(alias, alias.name, "module")
        return
    source = import_from_module(node, package)
    for alias in node.names:
        if alias.name == "*":
            if source in TOUCHING_MODULES or is_gantry(source):
                refuse(alias, f"{source}.*", "star")
            elif source not in PLAIN_MODULES:
                refuse(alias, source, "module")
            continue
        full = f"{source}.{alias.name}"
        if full in PLAIN_MODULES or full in TOUCHING_MODULES or source in PLAIN_MODULES:
            continue
        if source in TOUCHING_MODULES:
            if not allowed_name(source, alias.name):
                refuse(alias, full, "name")
        elif is_gantry(source):
            if found := rules.refusal(full):
                refuse(alias, *found)
        else:
            refuse(alias, source, "module")
            refuse(alias, full, "module")


def check_module(file, tree, rules, hits):
    module, package = module_of(file)
    table = rules.tables[module]

    def refuse(node, name, rule):
        hits.append(Hit(file, node.lineno, name, rule))

    nodes = list(ast.walk(tree))
    inner, subjects = set(), set()
    for node in nodes:
        if isinstance(node, ast.Attribute):
            inner.add(id(node.value))
        elif is_literal_getattr(node) and (chain := chain_of(node.args[0], table)) and chain[0].module:
            subjects.add(id(node.args[0]))

    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            check_imports(node, package, rules, refuse)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in REFUSED_BUILTINS:
            refuse(node, node.id, "builtin")
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg in REFUSED_KEYWORDS:
                    refuse(keyword, keyword.arg, "keyword")
            if (chain := chain_of(node.func, table)) is not None:
                if (builder := rules.canonical(dotted_of(chain))) in ROOT_BUILDERS:
                    refuse(node, builder, "roots-builder")
            if node.args and id(node.args[0]) in subjects:
                subject = dotted_of(chain_of(node.args[0], table))
                if found := rules.refusal(f"{subject}.{node.args[1].value}"):
                    refuse(node, *found)
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            continue
        if isinstance(node, (ast.Name, ast.Attribute)) and id(node) not in inner and id(node) not in subjects:
            chain = chain_of(node, table)
            if chain is not None and chain[0].module and (found := rules.refusal(dotted_of(chain))):
                refuse(node, *found)


def is_literal_getattr(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    )


def scan(pkg_dir, exceptions=EXCEPTIONS):
    """Report every reach outside the allow-list in the package at pkg_dir."""
    prefix = Path(pkg_dir).name
    files = [(f"{prefix}/{rel}", tree) for rel, tree in package_modules(pkg_dir)]
    rules = Package([(*module_of(file), tree) for file, tree in files])
    hits = []
    for file, tree in files:
        if file != ROOTS_FILE:
            check_module(file, tree, rules, hits)
    parsed = {file for file, _ in files}
    used = {(hit.file, hit.name) for hit in hits}
    stale = [
        (file, name, "its module file does not exist" if file not in parsed else "the module no longer uses this name")
        for file, name in sorted(exceptions)
        if file not in parsed or (file, name) not in used
    ]
    refused = sorted(hit for hit in hits if (hit.file, hit.name) not in exceptions)
    return Report(refused, parsed, stale)


def tree_failures(pkg_dir, exceptions=EXCEPTIONS):
    return failures_of(scan(pkg_dir, exceptions))


def failures_of(report):
    """The tree verdict: every refused name, unparsed kept module and stale exception."""
    return (
        [message(hit) for hit in report.refused]
        + [not_parsed_message(file) for file in sorted(KEPT - report.parsed)]
        + [stale_message(*entry) for entry in report.stale]
    )


# --- planted modules -------------------------------------------------------------


def src(*lines):
    return "\n".join(lines) + "\n"


class Fail(NamedTuple):
    id: str
    source: str
    line: int
    name: str
    rule: str
    file: str = ""  # default gantry/planted_<id>.py


class Pass(NamedTuple):
    id: str
    source: str
    file: str = ""


def plant_path(plant):
    return plant.file or f"gantry/planted_{plant.id}.py"


# Support modules other plants import from. The carriers pin the
# through-gantry rule without depending on what live modules import.
SUPPORT = (
    Pass("carrier", src("import os", "from pathlib import Path", "from pathlib import PurePosixPath")),
    Pass("zcarrier", src("import zipfile")),
)

# The via_roots_*, build_*, directory_roots, add_root, with_file and
# roots_annotation plants depend on gantry/roots.py's contents, and
# pure_via_config on gantry/config.py's; the carrier plants are their twins.
FAILS = (
    Fail("open_call", src('open("x")'), 1, "open", "builtin"),
    Fail(
        "path_import",
        src("from pathlib import Path", 'p = Path("x")', 'p.replace("y")', 'Path("x").read_text()'),
        1,
        "pathlib.Path",
        "name",
    ),
    Fail("os_remove", src("import os", 'os.remove("x")'), 2, "os.remove", "name"),
    Fail("from_alias", src("from os import remove as rm"), 1, "os.remove", "name"),
    Fail("module_alias", src("import os as o", 'o.listdir("x")'), 2, "os.listdir", "name"),
    Fail("subprocess_run", src("import subprocess", 'subprocess.run(["x"])'), 2, "subprocess.run", "name"),
    Fail("zipfile", src("import zipfile"), 1, "zipfile", "module"),
    Fail("os_stat", src("import os", 'os.stat("x")'), 2, "os.stat", "name"),
    Fail("isfile", src("import os", 'os.path.isfile("x")'), 2, "os.path.isfile", "name"),
    Fail("isfile_from", src("from os import path as p", 'p.isfile("x")'), 2, "os.path.isfile", "name"),
    Fail("realpath", src("import os.path as p", 'p.realpath("x")'), 2, "os.path.realpath", "name"),
    Fail("which", src("import shutil", 'shutil.which("x")'), 2, "shutil.which", "name"),
    Fail("map_open", src("names = []", "list(map(open, names))"), 2, "open", "builtin"),
    Fail("dunder_import", src('__import__("os")'), 1, "__import__", "builtin"),
    Fail("exec_call", src('exec("x = 1")'), 1, "exec", "builtin"),
    Fail("eval_call", src('eval("1")'), 1, "eval", "builtin"),
    Fail(
        "open_field_and_call",
        src("class Leaf:", "    open: bool", "def f(p):", "    return open(p)"),
        4,
        "open",
        "builtin",
    ),
    Fail("roots_named_file", src('open("x")'), 1, "open", "builtin", "gantry/views/roots.py"),
    Fail("getattr_literal", src("import os", 'getattr(os, "remove")'), 2, "os.remove", "name"),
    Fail("getattr_chain", src("import os", 'getattr(os.path, "isfile")'), 2, "os.path.isfile", "name"),
    Fail("importlib", src("import importlib"), 1, "importlib", "module"),
    Fail("importlib_from", src("from importlib import resources"), 1, "importlib", "module"),
    Fail("importlib_dotted", src("import importlib.resources"), 1, "importlib.resources", "module"),
    Fail("logging_handlers", src("import logging.handlers"), 1, "logging.handlers", "module"),
    Fail("posixpath_exists", src("import posixpath", 'posixpath.exists("x")'), 2, "posixpath.exists", "name"),
    Fail("ntpath", src("import ntpath"), 1, "ntpath", "module"),
    Fail("urllib_request", src("import urllib.request"), 1, "urllib.request", "module"),
    Fail("urllib_chain", src("import urllib.parse", 'urllib.request.urlopen("x")'), 2, "urllib.request", "module"),
    Fail("filetype", src("import argparse", 'argparse.FileType("r")'), 2, "argparse.FileType", "name"),
    Fail("via_roots_from", src("from gantry.roots import os"), 1, "gantry.roots.os", "through-gantry"),
    Fail(
        "via_roots_attr",
        src("import gantry.roots", 'gantry.roots.os.remove("x")'),
        2,
        "gantry.roots.os",
        "through-gantry",
    ),
    Fail("via_roots_relative", src("from .roots import os"), 1, "gantry.roots.os", "through-gantry"),
    Fail(
        "via_roots_views",
        src("from ..roots import os"),
        1,
        "gantry.roots.os",
        "through-gantry",
        "gantry/views/planted_via_roots_views.py",
    ),
    Fail(
        "via_carrier",
        src("from gantry.planted_carrier import Path"),
        1,
        "gantry.planted_carrier.Path",
        "through-gantry",
    ),
    Fail(
        "via_carrier_os",
        src("from gantry.planted_carrier import os"),
        1,
        "gantry.planted_carrier.os",
        "through-gantry",
    ),
    Fail(
        "via_carrier_attr",
        src("import gantry.planted_carrier", 'gantry.planted_carrier.os.remove("x")'),
        2,
        "gantry.planted_carrier.os",
        "through-gantry",
    ),
    Fail(
        "via_zcarrier",
        src("from gantry.planted_zcarrier import zipfile"),
        1,
        "gantry.planted_zcarrier.zipfile",
        "through-gantry",
    ),
    Fail("star_touching", src("from os import *"), 1, "os.*", "star"),
    Fail("star_gantry", src("from gantry.config import *"), 1, "gantry.config.*", "star"),
    Fail("rebind", src("import os", "x = os"), 2, "os", "rebind"),
    Fail("rebind_chain", src("import os", "osp = os.path"), 2, "os.path", "rebind"),
    Fail("pass_module", src("import os", "print(os.path)"), 2, "os.path", "rebind"),
    Fail(
        "fromfile_add_parser",
        src(
            "import argparse",
            "s = argparse.ArgumentParser().add_subparsers()",
            's.add_parser("b", fromfile_prefix_chars="@")',
        ),
        3,
        "fromfile_prefix_chars",
        "keyword",
    ),
    Fail(
        "fromfile_parser",
        src("import argparse", 'argparse.ArgumentParser(fromfile_prefix_chars="@")'),
        2,
        "fromfile_prefix_chars",
        "keyword",
    ),
    Fail(
        "directory_roots",
        src("from gantry.roots import directory_roots", 'directory_roots(["x"])'),
        2,
        "gantry.roots.directory_roots",
        "roots-builder",
    ),
    Fail("build_roots", src("from gantry.roots import Roots", "Roots(())"), 2, "gantry.roots.Roots", "roots-builder"),
    Fail(
        "build_root_alias",
        src("from gantry import roots as r", 'r.Root(None, None, "k", "file", False)'),
        2,
        "gantry.roots.Root",
        "roots-builder",
    ),
    Fail(
        "build_root_relative",
        src("from . import roots as r", 'r.Root(None, None, "k", "file", False)'),
        2,
        "gantry.roots.Root",
        "roots-builder",
    ),
    Fail(
        "add_root",
        src("from gantry.roots import add_root", 'add_root(None, "x", key="k", kind="file")'),
        2,
        "gantry.roots.add_root",
        "roots-builder",
    ),
    Fail(
        "with_file",
        src("import gantry.roots", 'gantry.roots.with_file(None, "x", key="k")'),
        2,
        "gantry.roots.with_file",
        "roots-builder",
    ),
)

PASSES = (
    Pass(
        "str_replace",
        src(
            "import os",
            "from pathlib import PurePath",
            '"a".replace("a", "b")',
            'PurePath("a")',
            'os.path.join("a", "b")',
        ),
    ),
    Pass("posixpath_join", src("import posixpath", 'posixpath.join("a", "b")')),
    Pass(
        "open_field",
        src(
            "from dataclasses import dataclass",
            "@dataclass",
            "class Leaf:",
            "    open: bool",
            "leaf = Leaf(open=True)",
            "leaf.open",
        ),
    ),
    Pass("pure_via_config", src("from gantry.config import PurePosixPath")),
    Pass("pure_via_carrier", src("from gantry.planted_carrier import PurePosixPath")),
    Pass("roots_annotation", src("from gantry.roots import Roots", "def f(roots: Roots):", "    return roots")),
    Pass("getattr_value", src("e = ValueError()", 'getattr(e, "strerror", None)')),
    Pass("getattr_allowed", src("import os", 'getattr(os.path, "join")')),
    Pass("urllib_parse", src("import urllib.parse", 'urllib.parse.quote("a")')),
)


class Planted(NamedTuple):
    base: object
    copy: object
    report: Report
    failures: list


def copy_package(base):
    copy = base / "gantry"
    shutil.copytree(ROOT / "gantry", copy, ignore=shutil.ignore_patterns("__pycache__"))
    return copy


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    base = tmp_path_factory.mktemp("confine")
    copy = copy_package(base)
    for plant in (*SUPPORT, *FAILS, *PASSES):
        (base / plant_path(plant)).write_bytes(plant.source.encode("utf-8"))
    report = scan(copy)
    return Planted(base, copy, report, failures_of(report))


def by_file(report, file):
    return [hit for hit in report.refused if hit.file == file]


# --- the tree --------------------------------------------------------------------


def test_tree_is_confined():
    assert tree_failures(ROOT / "gantry") == []


# --- planted controls ------------------------------------------------------------


@pytest.mark.parametrize("row", FAILS, ids=[row.id for row in FAILS])
def test_planted_form_refused(planted, row):
    path = plant_path(row)
    assert Hit(path, row.line, row.name, row.rule) in planted.report.refused, by_file(planted.report, path)


def test_path_plant_fails_at_its_import_only(planted):
    hits = by_file(planted.report, "gantry/planted_path_import.py")
    assert [(hit.line, hit.name) for hit in hits] == [(1, "pathlib.Path")]


@pytest.mark.parametrize("row", PASSES, ids=[row.id for row in PASSES])
def test_planted_form_passes(planted, row):
    assert plant_path(row) in planted.report.parsed
    assert by_file(planted.report, plant_path(row)) == []


def test_message_names_file_line_and_name(planted):
    for row in FAILS:
        text = message(Hit(plant_path(row), row.line, row.name, row.rule))
        assert text in planted.failures, row.id
        assert text.startswith(f"{plant_path(row)}:{row.line}: {row.name} ")
        assert text.endswith(REMEDY[row.rule])
    for remedy in REMEDY.values():
        assert "gantry/roots.py" in remedy and "allow-list" in remedy
    assert len(set(REMEDY.values())) == len(RULES)


def test_plants_cover_every_rule(planted):
    assert set(REMEDY) == set(RULES)
    assert {row.rule for row in FAILS} == set(RULES)
    assert {hit.rule for hit in planted.report.refused} <= set(RULES)


def test_planted_copy_is_outside_the_work_tree(planted):
    assert not planted.base.resolve().is_relative_to(ROOT.resolve())
    assert list((ROOT / "gantry").rglob("planted_*.py")) == []
    for plant in (*SUPPORT, *FAILS, *PASSES):
        assert (planted.base / plant_path(plant)).is_file()


# --- the allow-list --------------------------------------------------------------


def test_every_entry_has_a_reason():
    reasons = [
        *PLAIN_MODULES.values(),
        *TOUCHING_MODULES.values(),
        *ALLOWED_NAMES.values(),
        *(why for inner in OPEN_MODULES.values() for why in inner.values()),
        *EXCEPTIONS.values(),
    ]
    assert all(isinstance(why, str) and why.strip() for why in reasons)
    assert EXCEPTIONS


def test_exception_removes_only_its_own_name(planted):
    report = scan(planted.copy, {("gantry/planted_subprocess_run.py", "subprocess.run"): "x"})
    assert Hit("gantry/planted_subprocess_run.py", 2, "subprocess.run", "name") not in report.refused
    assert Hit("gantry/planted_os_remove.py", 2, "os.remove", "name") in report.refused
    assert report.stale == []


def test_exception_for_missing_module_is_stale(planted):
    failures = tree_failures(planted.copy, {("gantry/absent.py", "os.remove"): "x"})
    stale = [text for text in failures if "is stale" in text]
    assert len(stale) == 1
    assert stale[0].startswith("gantry/absent.py: exception for os.remove is stale")
    assert "does not exist" in stale[0]


def test_exception_no_longer_used_is_stale(planted):
    failures = tree_failures(planted.copy, {("gantry/planted_str_replace.py", "subprocess.run"): "x"})
    stale = [text for text in failures if "is stale" in text]
    assert len(stale) == 1
    assert stale[0].startswith("gantry/planted_str_replace.py: exception for subprocess.run is stale")
    assert "no longer uses" in stale[0]


# --- the kept list ---------------------------------------------------------------


def unparsed(failures):
    return [text for text in failures if ": not parsed:" in text]


def test_empty_copy_fails_the_kept_list(planted, tmp_path):
    assert unparsed(planted.failures) == []
    empty = tmp_path / "gantry"
    empty.mkdir()
    report = scan(empty)
    assert report.parsed == set()
    assert unparsed(failures_of(report)) == [not_parsed_message(file) for file in sorted(KEPT)]


def test_one_missing_module_fails_the_kept_list(tmp_path):
    copy = copy_package(tmp_path)
    (copy / "views" / "worksite.py").unlink()
    assert unparsed(tree_failures(copy)) == [not_parsed_message("gantry/views/worksite.py")]


# The kept list at 39f4afa, spelled out a second time: KEPT may grow, never shrink.
BASELINE = frozenset({
    "gantry/__init__.py", "gantry/__main__.py", "gantry/attention.py", "gantry/build.py",
    "gantry/cli.py", "gantry/config.py", "gantry/deps.py", "gantry/est_rules.py",
    "gantry/estimates_file.py", "gantry/inputs.py", "gantry/links.py", "gantry/model.py",
    "gantry/network.py", "gantry/portfolio_file.py", "gantry/priority.py", "gantry/render.py",
    "gantry/requirements.py", "gantry/roots.py", "gantry/schedule.py", "gantry/sites.py",
    "gantry/stamp.py", "gantry/trace.py", "gantry/wbs_history.py", "gantry/wbs_stopgap.py",
    "gantry/views/__init__.py", "gantry/views/index.py", "gantry/views/worksite.py",
})


def test_kept_list_holds_the_39f4afa_modules():
    assert len(BASELINE) == 27
    assert KEPT >= BASELINE
    for file in KEPT:
        assert (ROOT / file).is_file(), file
