"""The installation config: one TOML file that says where an installation's
data lives (#7). docs/config.md is its reference.

Every key is a row of KEYS or SITE_KEYS. A later issue adds a key by adding a
row here, a row in docs/config.md (a test enforces it) and a sample entry.
"""

import math
import os
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

CONFIG_VERSION = 1


class ConfigError(Exception):
    """A config Gantry refuses. The message starts with the file it is about."""


class PathError(ValueError):
    """A path value Gantry refuses; the message is the reason, not the key."""


@dataclass(frozen=True)
class Key:
    name: str
    kind: str
    required: bool
    default: object
    owner: str
    doc_name: str


KEYS = (
    Key("config_version", "int", True, None, "#7", "`config_version`"),
    Key("devagent_config", "path", True, None, "#7", "`devagent_config`"),
    Key("output_dir", "path", True, None, "#9", "`output_dir`"),
    Key("as_of", "date", False, None, "every dated view", "`as_of`"),
    Key("default_unit_hours", "positive_number", False, 6, "#9", "`default_unit_hours`"),
    Key("portfolio_file", "path", False, None, "#22", "`portfolio_file`"),
    Key("fires_list", "path", False, None, "#30", "`fires_list`"),
    Key("site", "site_tables", False, None, "#7", "`[site.<name>]`"),
)

SITE_KEYS = (
    Key("requirements", "path", False, None, "#15", "`requirements`"),
    Key("estimates", "path_or_false", False, None, "#10", "`estimates`"),
)

_KEYS_BY_NAME = {key.name: key for key in KEYS}
_SITE_KEYS_BY_NAME = {key.name: key for key in SITE_KEYS}


@dataclass(frozen=True)
class SiteOverride:
    requirements: Path | None
    estimates: Path | Literal[False] | None


@dataclass(frozen=True)
class Config:
    path: Path
    config_version: int
    devagent_config: Path
    output_dir: Path
    as_of: date
    default_unit_hours: int | float
    portfolio_file: Path | None
    fires_list: Path | None
    sites: dict[str, SiteOverride]


# --- paths ---------------------------------------------------------------------


def classify_path(raw: str, flavour: str) -> Literal["absolute", "relative", "foreign"]:
    """Classify a path string under one operating system's rules.

    `flavour` is "nt" or "posix" (callers pass os.name). A path that is
    absolute only under the other system's rules is "foreign": it must never
    be joined to a base folder.
    """
    w = PureWindowsPath(raw)
    if flavour == "nt":
        if w.is_absolute():
            return "absolute"
    elif flavour == "posix":
        if PurePosixPath(raw).is_absolute():
            return "absolute"
    else:
        raise ValueError(f"unknown path flavour {flavour!r}")
    if w.drive or w.root:
        return "foreign"
    return "relative"


def _expand_tilde(raw: str, flavour: str) -> str:
    """Expand a bare ~ (alone, or followed by a separator); refuse ~name."""
    if not raw.startswith("~"):
        return raw
    seps = ("/", "\\") if flavour == "nt" else ("/",)
    if raw != "~" and raw[1] not in seps:
        raise PathError("uses ~ with a user name; only a bare ~ is expanded")
    home = os.path.expanduser("~")
    if home.startswith("~"):
        raise PathError("starts with ~ but the home folder cannot be found")
    return home + raw[1:]


def resolve_path(raw: str, base_dir, *, flavour: str) -> Path:
    """Make a config path absolute, lexically (no filesystem access).

    A bare ~ expands; a relative path joins `base_dir`, the folder of the file
    that holds it; a path absolute only on another OS is refused.
    """
    expanded = _expand_tilde(raw, flavour)
    kind = classify_path(expanded, flavour)
    if kind == "foreign":
        w = PureWindowsPath(expanded)
        if flavour == "nt" and w.drive and not w.root:
            raise PathError("is drive-relative; write it absolute or relative")
        raise PathError("is rooted but not absolute on this operating system (a path for another OS?)")
    if kind == "relative":
        return Path(os.path.normpath(os.path.join(os.fspath(base_dir), expanded)))
    return Path(os.path.normpath(expanded))


# --- types -----------------------------------------------------------------------

_KIND_TEXT = {
    "int": "an integer",
    "path": "a path string",
    "date": "a TOML date (YYYY-MM-DD)",
    "positive_number": "a positive number",
    "path_or_false": "a path string or false",
    "site_tables": "a table",
    "table": "a table",
}


def _describe(value) -> str:
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, int):
        text = str(value) if abs(value) < 10**18 else "a very large integer"
        return text
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return "an empty string" if value == "" else "a string"
    if isinstance(value, datetime):
        return "a date-time"
    if isinstance(value, date):
        return "a date"
    if isinstance(value, dict):
        return "a table"
    if isinstance(value, list):
        return "an array"
    return type(value).__name__


def _kind_ok(kind: str, value) -> bool:
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "path":
        return isinstance(value, str) and value != ""
    if kind == "date":
        return isinstance(value, date) and not isinstance(value, datetime)
    if kind == "positive_number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value > 0
        )
    if kind == "path_or_false":
        return value is False or (isinstance(value, str) and value != "")
    if kind in ("site_tables", "table"):
        return isinstance(value, dict)
    raise ValueError(f"unknown key kind {kind!r}")


def _quoted(names) -> str:
    return ", ".join(f"'{name}'" for name in names)


# --- the loader ------------------------------------------------------------------

_BOM = b"\xef\xbb\xbf"


def error_reason(e: Exception) -> str:
    """An OSError's own text, or any other error's message."""
    return getattr(e, "strerror", None) or str(e)


def read_toml(path, name: str, subject: str) -> dict:
    """Read a TOML file; raise ConfigError naming the file on any failure.

    `name` is what "cannot read ..." calls the file; `subject` prefixes the
    other messages ("" or "the devagent config ").
    """
    file = str(path)
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except (OSError, ValueError) as e:
        # ValueError: an embedded NUL, or a path too long for Windows.
        raise ConfigError(f"{file}: cannot read {name}: {error_reason(e)}") from None
    if data.startswith(_BOM):
        raise ConfigError(f"{file}: {subject}starts with a UTF-8 byte-order mark; save it without one")
    try:
        return tomllib.loads(data.decode("utf-8"))
    except ValueError as e:
        # TOMLDecodeError, UnicodeDecodeError and the integer-digit limit.
        raise ConfigError(f"{file}: {subject}is not valid TOML: {e}") from None


def load_config(path) -> Config:
    """Load and check an installation config; raise ConfigError if refused."""
    file = str(path)

    def fail(message):
        return ConfigError(f"{file}: {message}")

    raw = read_toml(path, "the config", "")

    # The version first, so a newer file reports its version, not its new keys.
    if "config_version" not in raw:
        raise fail(f"no config_version; this Gantry reads config_version {CONFIG_VERSION}")
    version = raw["config_version"]
    if not _kind_ok("int", version):
        raise fail(f"config_version must be an integer, not {_describe(version)}")
    if version > CONFIG_VERSION:
        raise fail(
            f"config_version {version} is newer than config_version {CONFIG_VERSION}, "
            "the version this Gantry reads"
        )
    if version < CONFIG_VERSION:
        raise fail(
            f"config_version {version} is older than config_version {CONFIG_VERSION}, "
            "the version this Gantry reads; docs/config.md lists what changed"
        )

    unknown = [name for name in raw if name not in _KEYS_BY_NAME]
    if unknown:
        noun = "key" if len(unknown) == 1 else "keys"
        raise fail(f"unknown {noun} {_quoted(unknown)}")

    missing = [key.name for key in KEYS if key.required and key.name not in raw]
    if missing:
        noun = "key" if len(missing) == 1 else "keys"
        raise fail(f"missing required {noun} {_quoted(missing)}")

    for key in KEYS:
        if key.name in raw and not _kind_ok(key.kind, raw[key.name]):
            raise fail(
                f"key '{key.name}' must be {_KIND_TEXT[key.kind]}, "
                f"not {_describe(raw[key.name])}"
            )

    site_tables = raw.get("site", {})
    for name, table in site_tables.items():
        dotted = f"site.{name}"
        if not _kind_ok("table", table):
            raise fail(f"key '{dotted}' must be a table, not {_describe(table)}")
        unknown = [f"{dotted}.{k}" for k in table if k not in _SITE_KEYS_BY_NAME]
        if unknown:
            noun = "key" if len(unknown) == 1 else "keys"
            raise fail(f"unknown {noun} {_quoted(unknown)}")
        for key in SITE_KEYS:
            if key.name in table and not _kind_ok(key.kind, table[key.name]):
                raise fail(
                    f"key '{dotted}.{key.name}' must be {_KIND_TEXT[key.kind]}, "
                    f"not {_describe(table[key.name])}"
                )

    base = Path(os.path.abspath(path)).parent

    def resolved(dotted, value):
        if value is None or value is False:
            return value
        try:
            return resolve_path(value, base, flavour=os.name)
        except PathError as e:
            raise fail(f"key '{dotted}' {e}: {value!r}") from None

    # Registry order: the first bad path reported is the first in KEYS.
    devagent_config = resolved("devagent_config", raw["devagent_config"])
    output_dir = resolved("output_dir", raw["output_dir"])
    portfolio_file = resolved("portfolio_file", raw.get("portfolio_file"))
    fires_list = resolved("fires_list", raw.get("fires_list"))
    sites = {
        name: SiteOverride(
            requirements=resolved(f"site.{name}.requirements", table.get("requirements")),
            estimates=resolved(f"site.{name}.estimates", table.get("estimates")),
        )
        for name, table in site_tables.items()
    }
    as_of = raw.get("as_of")
    if as_of is None:
        as_of = date.today()  # at load time, never a def-time default
    return Config(
        path=Path(os.path.abspath(path)),
        config_version=version,
        devagent_config=devagent_config,
        output_dir=output_dir,
        as_of=as_of,
        default_unit_hours=raw.get("default_unit_hours", _KEYS_BY_NAME["default_unit_hours"].default),
        portfolio_file=portfolio_file,
        fires_list=fires_list,
        sites=sites,
    )
