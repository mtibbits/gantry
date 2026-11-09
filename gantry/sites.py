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

# The keys whose roots are writable, by a run's purpose. #35 adds refresh's
# `[refresh] dir`; serve writes nothing.
WRITABLE = {"build": ("output_dir",), "check": (), "refresh": (), "serve": ()}


@dataclass(frozen=True)
class SiteEntry:
    name: str
    planning_dir: PurePath | None
    problem: str | None


@dataclass(frozen=True)
class SiteWarning:
    key: str
    message: str


def shown(name: str) -> str:
    """A site name as printed: an invalid name may hold a tab, newline or ESC,
    so it is escaped, and a line stays one line."""
    return name if ID_TOKEN_RE.fullmatch(name) else ascii(name)[1:-1]


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
    st, problem = _stat(roots, planning_dir, "planning directory does not exist", "the planning directory",
                        key="devdoc_dir", site=site)
    if problem:
        return problem
    if not stat.S_ISDIR(st.st_mode):
        return "planning directory is not a directory"
    wbs, problem = _stat(roots, planning_dir / "WBS.md", "no WBS.md in the planning directory", "WBS.md",
                         key="WBS.md", site=site)
    if problem:
        return problem
    if not stat.S_ISREG(wbs.st_mode):
        return "WBS.md is not a file"
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
        return SiteEntry(name, None, "no devdoc_dir")
    raw = table["devdoc_dir"]
    if not isinstance(raw, str):
        return SiteEntry(name, None, "devdoc_dir is not a string")
    if raw == "":
        return SiteEntry(name, None, "devdoc_dir is empty")
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


def config_roots(config, *, output_dir=None, purpose="build"):
    """Every root of a run: the two config files, every set path key's value
    (its kind from the registry), every planning directory, and the package
    directory. Writable: the keys WRITABLE names for `purpose` ("check", for
    a command that writes nothing, has none). A writable root that equals or
    contains an input root is a ConfigError. `output_dir=` and the "refresh"
    and "serve" purposes are provisional for #35 and #64."""
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
        if key.name == "output_dir" and output_dir is not None:
            value = PurePath(os.path.abspath(output_dir))
        if value is not None:
            roots = add(roots, value, key.name, key)
    for name, override in config.sites.items():
        for key in SITE_KEYS:
            value = getattr(override, key.name)
            if key.root is not None and isinstance(value, PurePath):
                roots = add(roots, value, f"site.{name}.{key.name}", key)
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
    inputs = [r for r in roots.roots
              if r.key in (CONFIG_KEY, "devagent_config", "devdoc_dir") or r.kind == "package"]
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
