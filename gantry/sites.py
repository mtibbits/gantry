"""The site list: read only from devagent's config.toml (#7).

The sites are the keys of the parsed `project` table, in the order each first
appears in the file; a nested table such as `[project.<name>.permissions]` is a
field of its site, never a site. A site that cannot be read is listed with its
reason and does not stop the others.
"""

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from gantry.config import ConfigError, PathError, error_reason, read_toml, resolve_path

# devagent's ID_TOKEN_RE (scripts/lib/wbs-parser.py); used with fullmatch.
ID_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


@dataclass(frozen=True)
class SiteEntry:
    name: str
    planning_dir: Path | None
    problem: str | None


@dataclass(frozen=True)
class SiteWarning:
    key: str
    message: str


def _read_devagent(dfile):
    data = read_toml(dfile, "the devagent config", "the devagent config ")
    projects = data.get("project")
    if not isinstance(projects, dict):
        raise ConfigError(f"{dfile}: no [project] table; is devagent_config the right file?")
    if not projects:
        raise ConfigError(f"{dfile}: the [project] table is empty; is devagent_config the right file?")
    return projects


def _stat(path, missing, label):
    """(stat result, None), or (None, `missing` or "cannot check `label`: ...")."""
    try:
        return os.stat(path), None
    except FileNotFoundError:
        return None, missing
    except (OSError, ValueError) as e:
        # ValueError: an embedded NUL, or a path too long for Windows.
        return None, f"cannot check {label}: {error_reason(e)}"


def _stat_problem(planning_dir):
    """The first problem with a planning directory and its WBS.md, or None."""
    st, problem = _stat(planning_dir, "planning directory does not exist", "the planning directory")
    if problem:
        return problem
    if not stat.S_ISDIR(st.st_mode):
        return "planning directory is not a directory"
    wbs, problem = _stat(planning_dir / "WBS.md", "no WBS.md in the planning directory", "WBS.md")
    if problem:
        return problem
    if not stat.S_ISREG(wbs.st_mode):
        return "WBS.md is not a file"
    return None


def _site(name, table, dfile):
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
    return SiteEntry(name, planning_dir, _stat_problem(planning_dir))


def load_sites(config):
    """Return (sites, warnings) for a loaded installation config."""
    dfile = config.devagent_config
    projects = _read_devagent(dfile)
    sites = [_site(name, table, dfile) for name, table in projects.items()]
    warnings = [
        SiteWarning(f"site.{name}", f"{config.path}: [site.{name}] names no project in {dfile}")
        for name in config.sites
        if name not in projects
    ]
    return sites, warnings
