"""The site list: read only from devagent's config.toml (#7).

The sites are the keys of the parsed `project` table, in the order each first
appears in the file; a nested table such as `[project.<name>.permissions]` is a
field of its site, never a site. A site that cannot be read is listed with its
reason and does not stop the others.
"""

import os
import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

from gantry.config import ConfigError, PathError, resolve_path

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
    try:
        with open(dfile, "rb") as handle:
            data = tomllib.load(handle)
    except OSError as e:
        raise ConfigError(f"{dfile}: cannot read the devagent config: {e.strerror or e}") from None
    except ValueError as e:
        raise ConfigError(f"{dfile}: the devagent config is not valid TOML: {e}") from None
    projects = data.get("project")
    if not isinstance(projects, dict):
        raise ConfigError(f"{dfile}: no [project] table; is devagent_config the right file?")
    if not projects:
        raise ConfigError(f"{dfile}: the [project] table is empty; is devagent_config the right file?")
    return projects


def _stat_problem(planning_dir):
    """The first problem with a planning directory and its WBS.md, or None."""
    try:
        st = os.stat(planning_dir)
    except FileNotFoundError:
        return "planning directory does not exist"
    except OSError as e:
        return f"cannot check the planning directory: {e.strerror or e}"
    if not stat.S_ISDIR(st.st_mode):
        return "planning directory is not a directory"
    try:
        os.stat(planning_dir / "WBS.md")
    except FileNotFoundError:
        return "no WBS.md in the planning directory"
    except OSError as e:
        return f"cannot check WBS.md: {e.strerror or e}"
    return None


def _site(name, table, dfile):
    if not isinstance(table, dict):
        return SiteEntry(name, None, f"[project.{name}] is not a table")
    if not ID_TOKEN_RE.fullmatch(name):
        return SiteEntry(name, None, f"name does not match {ID_TOKEN_RE.pattern}")
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
    dfile = Path(config.devagent_config)
    projects = _read_devagent(dfile)
    sites = [_site(name, table, dfile) for name, table in projects.items()]
    warnings = [
        SiteWarning(f"site.{name}", f"{config.path}: [site.{name}] names no project in {dfile}")
        for name in config.sites
        if name not in projects
    ]
    return sites, warnings
