"""The site list and a run's roots: read only from devagent's config.toml (#7, #59).

The sites are the keys of the parsed `project` table, in the order each first
appears in the file; a nested table such as `[project.<name>.permissions]` is a
field of its site, never a site. A site that cannot be read is listed with its
reason and does not stop the others.

`config_roots` lists every root of a run. It shares the planning-directory
parse with `load_sites`, so the two cannot disagree about which sites have a
planning directory, and it reads the devagent config itself, through roots
that hold only the two config files.
"""

import os
import stat
from dataclasses import dataclass, replace
from pathlib import PurePath

from gantry.config import (
    ID_TOKEN_RE,
    KEYS,
    REFRESH_KEYS,
    SITE_KEYS,
    ConfigError,
    PathError,
    error_reason,
    read_toml,
    resolve_path,
)
from gantry.roots import (
    CONFIG_KEY,
    OutsideRoots,
    add_root,
    bootstrap_roots,
    relation,
    stat_confined,
    with_file,
    with_package,
)

# The keys whose roots are writable, by a run's purpose; serve writes nothing.
WRITABLE = {"build": ("output_dir",), "check": (), "refresh": ("refresh.dir",), "serve": ()}

# The problems that prove a site has no leaves (#21): such a site is not
# counted among the sites the trace could not read.
NO_DEVDOC_DIR = "no devdoc_dir"
NO_PLANNING_DIR = "planning directory does not exist"
NO_WBS = "no WBS.md in the planning directory"
NO_LEAVES = frozenset({NO_DEVDOC_DIR, NO_PLANNING_DIR, NO_WBS})
# The other problems gantry.build compares with the portfolio JSON's statuses (#12): constants,
# so the comparison never keys on a spelling.
EMPTY_DEVDOC_DIR = "devdoc_dir is empty"
PLANNING_NOT_A_DIRECTORY = "planning directory is not a directory"
WBS_NOT_A_FILE = "WBS.md is not a file"


@dataclass(frozen=True)
class SiteEntry:
    name: str
    planning_dir: PurePath | None
    problem: str | None
    # Set by gantry.build.match_sites (#12) for a site devagent's portfolio JSON did not read and
    # whose status agrees with `problem`: the document's status words and reason, which the index
    # and every not-built line show. `problem` stays Gantry's own, which the fires and the trace read.
    document_reason: str | None = None


@dataclass(frozen=True)
class SiteWarning:
    key: str
    message: str


def shown(name: str) -> str:
    """A site name as printed: an invalid name may hold a tab, newline or ESC,
    so it is escaped, and a line stays one line."""
    return name if ID_TOKEN_RE.fullmatch(name) else ascii(name)[1:-1]


def one_line(text: str) -> str:
    """`text` with every character that is not printable (a control character, a line or
    paragraph separator) escaped as ascii() writes it, so a message from a file Gantry
    reads (devagent's portfolio JSON, #12) stays one terminal line and moves no cursor."""
    return "".join(ch if ch.isprintable() else ascii(ch)[1:-1] for ch in text)


def _read_devagent(roots, dfile):
    data = read_toml(roots, dfile, "the devagent config", "the devagent config ", key="devagent_config")
    projects = data.get("project")
    if not isinstance(projects, dict):
        raise ConfigError(f"{dfile}: no [project] table; is devagent_config the right file?")
    if not projects:
        raise ConfigError(f"{dfile}: the [project] table is empty; is devagent_config the right file?")
    return projects


def _config_files(config):
    """The roots that hold only the two config files."""
    return with_file(bootstrap_roots(config.path), config.devagent_config, key="devagent_config")


def _stat(roots, path, missing, label, *, key, site):
    """(stat result, None), or (None, `missing`, a refusal or "cannot check `label`: ...")."""
    try:
        return stat_confined(roots, path, key=key, site=site), None
    except FileNotFoundError:
        return None, missing
    except OutsideRoots as e:
        return None, str(e)
    except (OSError, ValueError) as e:
        # ValueError: an embedded NUL, or a path too long for Windows.
        return None, f"cannot check {label}: {error_reason(e)}"


def _stat_problem(roots, planning_dir, site):
    """The first problem with a planning directory and its WBS.md, or None."""
    st, problem = _stat(roots, planning_dir, NO_PLANNING_DIR, "the planning directory",
                        key="devdoc_dir", site=site)
    if problem:
        return problem
    if not stat.S_ISDIR(st.st_mode):
        return PLANNING_NOT_A_DIRECTORY
    wbs, problem = _stat(roots, planning_dir / "WBS.md", NO_WBS, "WBS.md",
                         key="WBS.md", site=site)
    if problem:
        return problem
    if not stat.S_ISREG(wbs.st_mode):
        return WBS_NOT_A_FILE
    return None


def _site(name, table, dfile):
    """The parse alone: a SiteEntry with a planning directory and no problem
    yet, or with no planning directory and its problem."""
    # The name is checked first: only a valid name is quoted in a problem.
    if not ID_TOKEN_RE.fullmatch(name):
        return SiteEntry(name, None, f"name does not match {ID_TOKEN_RE.pattern}")
    if not isinstance(table, dict):
        return SiteEntry(name, None, f"[project.{name}] is not a table")
    if "devdoc_dir" not in table:
        return SiteEntry(name, None, NO_DEVDOC_DIR)
    raw = table["devdoc_dir"]
    if not isinstance(raw, str):
        return SiteEntry(name, None, "devdoc_dir is not a string")
    if raw == "":
        return SiteEntry(name, None, EMPTY_DEVDOC_DIR)
    try:
        planning_dir = resolve_path(raw, dfile.parent, flavour=os.name)
    except PathError as e:
        # Never joined to the config's folder: the site just cannot be read.
        return SiteEntry(name, None, f"devdoc_dir {e}")
    return SiteEntry(name, planning_dir, None)


def _parse(config, roots):
    dfile = config.devagent_config
    projects = _read_devagent(roots, dfile)
    return projects, [_site(name, table, dfile) for name, table in projects.items()]


def _inner(root):
    """How the containment error names an input root."""
    if root.key == CONFIG_KEY:
        return "the config file itself"
    if root.key == "devdoc_dir":
        return f"devdoc_dir of site {shown(root.site)}"
    if root.kind == "package":
        return "the gantry package"
    return f"key '{root.key}'"


def config_roots(config, *, purpose="build"):
    """Every root of a run: the two config files, every set path key's value
    (its kind from the registry), `[refresh] dir`, every planning directory,
    and the package directory. Writable: the keys WRITABLE names for
    `purpose` ("check", for a command that writes nothing, has none). A
    writable root that equals or contains an input root is a ConfigError.
    `gantry build --output-dir` replaces `config.output_dir` before this is
    called (#35). The "serve" purpose is provisional for #64."""
    if purpose not in WRITABLE:
        raise ValueError(f"unknown purpose {purpose!r}")
    roots = _config_files(config)
    _, parsed = _parse(config, roots)
    writable = WRITABLE[purpose]

    def add(roots, value, dotted, key):
        try:
            return add_root(roots, value, key=dotted, kind=key.root, writable=dotted in writable)
        except (OSError, ValueError) as e:
            raise ConfigError(f"{config.path}: key '{dotted}' cannot be resolved: {error_reason(e)}") from None

    for key in KEYS:
        if key.root is None or key.name == "devagent_config":
            continue
        value = getattr(config, key.name)
        if value is not None:
            roots = add(roots, value, key.name, key)
    for name, override in config.sites.items():
        for key in SITE_KEYS:
            value = getattr(override, key.name)
            if key.root is not None and isinstance(value, PurePath):
                roots = add(roots, value, f"site.{name}.{key.name}", key)
    for key in REFRESH_KEYS:
        value = getattr(config.refresh, key.name)
        if key.root is not None and value is not None:
            roots = add(roots, value, f"refresh.{key.name}", key)
    for site in parsed:
        if site.planning_dir is None:
            continue
        try:
            roots = add_root(roots, site.planning_dir, key="devdoc_dir", kind="directory", site=site.name)
        except (OSError, ValueError):
            pass  # no root: load_sites gives the site its problem
    roots = with_package(roots)
    _check_containment(config, roots)
    return roots


def _check_containment(config, roots):
    # A `history` directory (#28) is an input: git reads its repository.
    inputs = [r for r in roots.roots
              if r.key in (CONFIG_KEY, "devagent_config", "devdoc_dir") or r.kind == "package"
              or r.key.endswith(".history")]
    for w in roots.roots:
        if not w.writable:
            continue
        found = []
        for i in inputs:
            verb = relation(w, i)
            if verb is not None:
                found.append(f"{verb} {_inner(i)}")
        if found:
            raise ConfigError(f"{config.path}: key '{w.key}' {', '.join(found)}; "
                              "a writable root may not equal or contain an input root")


def load_sites(config, roots=None):
    """Return (sites, warnings) for a loaded installation config.

    The devagent config is read through roots holding only the two config
    files; each planning directory and its WBS.md are then stat'd through
    the run's full roots: `roots` when the caller has built them, else
    config_roots builds them (reading the devagent config again).
    """
    dfile = config.devagent_config
    projects, parsed = _parse(config, _config_files(config))
    full = config_roots(config) if roots is None else roots
    sites = [replace(s, problem=_stat_problem(full, s.planning_dir, s.name)) if s.planning_dir is not None else s
             for s in parsed]
    warnings = [
        SiteWarning(f"site.{name}", f"{config.path}: [site.{name}] names no project in {dfile}")
        for name in config.sites
        if name not in projects
    ]
    return sites, warnings
