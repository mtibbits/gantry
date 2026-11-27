"""devagent's declared capacity: the `[capacity]` and `[project.<name>.capacity]`
tables of the devagent config (#39).

A port of the rules of devagent's shipped reader, `scripts/lib/capacity.py` at
devagent--v3.5.0 (the release tests/devagent.pin names): the rule
strings, their order, one line per key, which rule an invalid value switches
off, the pool sums and the token rounding are devagent's, and `document_text`
is the document its `scripts/capacity.sh` prints. Gantry runs no devagent
command. A change here follows a change there, never the reverse.

Two differences, both on purpose:
- The project list is every key of `[project]` whose value is a table, in
  file order, whatever its name. devagent's own list leaves out a name it
  cannot print as one `project.<name>` line (one with a dot or a line feed,
  or the empty name), so its reader gives such a project no line and no
  place in the document.
- devagent also warns that a project's "only key is capacity". Gantry lists
  such a project as a site it cannot build (#7), so only the unknown-key
  warning is mirrored.

`resolve_capacity` reads no file. `read_declared_capacity` reads the config
through the run's roots, as gantry/sites.py does.
"""

import datetime
import json
import math
import re
from dataclasses import dataclass
from typing import ClassVar

from gantry.config import read_toml

CAPACITY_SCHEMA = "1.0"
TOKEN_UNIT = "weighted_input_token_equivalents"

BINDING_WEEK = "tokens_per_week"
BINDING_WINDOWS = "tokens_per_window_x_windows_per_week"

PROJECT_DEFAULTS = {
    "priority": 5,
    "max_lanes": None,
    "paused": False,
    "deadline": None,
    "reserve_operator_hours_per_week": None,
    "reserve_tokens_per_week": None,
}

# Every numeric key has a finite upper bound; where the domain gives none it is
# 2^53 - 1, so an int times a float and an int plus a float cannot overflow.
SAFE_MAX = 9007199254740991

# (range text, low, low is allowed, high). The five texts are the complete set.
RANGE_HOURS = ("0 < x <= 168", 0, False, 168)
RANGE_COUNT = ("1 <= x <= 9007199254740991", 1, True, SAFE_MAX)
RANGE_TOKENS = ("0 < x <= 9007199254740991", 0, False, SAFE_MAX)
RANGE_WINDOWS = ("0 < x <= 33.6", 0, False, 33.6)
RANGE_NONNEG = ("0 <= x <= 9007199254740991", 0, True, SAFE_MAX)

# key -> (type, range or None), in the order the lines and the document list the keys.
SHARED_RULES = {
    "operator_hours_per_week": ("number", RANGE_HOURS),
    "lane_hours_per_week": ("number", RANGE_HOURS),
    "max_lanes": ("integer", RANGE_COUNT),
    "tokens_per_week": ("integer", RANGE_TOKENS),
    "tokens_per_window": ("integer", RANGE_TOKENS),
    "windows_per_week": ("number", RANGE_WINDOWS),
    "operator_hours_per_agent_hour": ("number", RANGE_NONNEG),
}
SHARED_OPTIONAL = ("operator_hours_per_agent_hour",)
PROJECT_RULES = {
    "priority": ("integer", RANGE_COUNT),
    "max_lanes": ("integer", RANGE_COUNT),
    "paused": ("boolean", None),
    "deadline": ("date", None),
    "reserve_operator_hours_per_week": ("number", RANGE_NONNEG),
    "reserve_tokens_per_week": ("integer", RANGE_NONNEG),
}
SHARED_KEYS = tuple(SHARED_RULES)
PROJECT_KEYS = tuple(PROJECT_RULES)

# The rule strings: devagent's contract. A violation is "capacity: <where>: <rule>".
MISSING = "missing required key"
WRONG_TYPE = "wrong type: expected "
OUT_OF_RANGE = "out of range: "
NOT_A_DATE = "not a calendar date"
EXCEEDS_LANES = "exceeds capacity.max_lanes"
TABLE_MISSING = "[capacity] table missing"
POOL_HOURS = "reserve_operator_hours_per_week sum exceeds capacity.operator_hours_per_week"
POOL_TOKENS = "reserve_tokens_per_week sum exceeds the effective weekly token budget"
RULES = (
    MISSING,
    *(WRONG_TYPE + kind for kind in ("number", "integer", "boolean", "date", "table")),
    *(OUT_OF_RANGE + rng[0] for rng in (RANGE_HOURS, RANGE_COUNT, RANGE_TOKENS, RANGE_WINDOWS, RANGE_NONNEG)),
    NOT_A_DATE,
    EXCEEDS_LANES,
    TABLE_MISSING,
    POOL_HOURS,
    POOL_TOKENS,
)
VIOLATION = "capacity: {where}: {rule}"
WARNING = "capacity: warning: {where}: unknown key (ignored)"

# The hours pool refuses only past this fixed tolerance: it absorbs binary rounding,
# so an exactly-full declaration (4.4 + 11.8 + 3.8 against 20) passes in any order.
HOURS_TOLERANCE = 1e-9

_BARE_PART = re.compile(r"[A-Za-z0-9_-]+")
_DATE_TEXT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# 2^52: every double at or above it is already whole, and adding 0.5 there can
# round the sum up to the next integer.
_WHOLE_DOUBLES = 4503599627370496


@dataclass(frozen=True)
class ProjectCapacity:
    """One project as devagent's document lists it: each of the six keys as
    written, or its default (priority 5, paused false, the rest None).
    `set_keys` names the keys the config sets, in PROJECT_KEYS order; a
    deadline is `YYYY-MM-DD` text for both of its spellings."""

    name: str
    priority: int
    max_lanes: int | None
    paused: bool
    deadline: str | None
    reserve_operator_hours_per_week: int | float | None
    reserve_tokens_per_week: int | None
    set_keys: tuple[str, ...]


@dataclass(frozen=True)
class DeclaredCapacity:
    """What the devagent config declares, with the lines devagent's reader
    writes about it.

    `declared` is whether the file has a `capacity` key. Each shared value
    keeps its parsed type (20 is not 20.0); `operator_hours_per_agent_hour`
    is None when unset, and every shared value is None when nothing is
    declared. `token_budget` is devagent's effective weekly token budget, in
    `token_unit`, and `token_binding_term` the term that sets it. `paused`
    names the paused projects, in project order.

    With a violation devagent prints no document, so nothing declared can be
    read here either: the seven shared values, `token_budget` and
    `token_binding_term` are None, and `projects` and `paused` are empty.
    `capacity_schema` and `token_unit` name the contract, not the declaration,
    and are always set. `violations` and `warnings` hold whole lines, in
    devagent's order.
    """

    capacity_schema: ClassVar[str] = CAPACITY_SCHEMA
    token_unit: ClassVar[str] = TOKEN_UNIT

    declared: bool
    operator_hours_per_week: int | float | None
    lane_hours_per_week: int | float | None
    max_lanes: int | None
    tokens_per_week: int | None
    tokens_per_window: int | None
    windows_per_week: int | float | None
    operator_hours_per_agent_hour: int | float | None
    token_budget: int | None
    token_binding_term: str | None
    projects: tuple[ProjectCapacity, ...]
    violations: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def paused(self) -> tuple[str, ...]:
        return tuple(project.name for project in self.projects if project.paused)

    @property
    def usable(self) -> bool:
        """Declared and valid: the only declaration a plan is made from."""
        return self.declared and not self.violations


def where(parts) -> str:
    """A dotted path: a part matching [A-Za-z0-9_-]+ in full is bare, any other
    is a JSON string, so a name with a newline stays on one line."""
    out = []
    for part in parts:
        if _BARE_PART.fullmatch(part):
            out.append(part)
        else:
            out.append(json.dumps(part, ensure_ascii=False))
    return ".".join(out)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _calendar_date(text):
    """The date a YYYY-MM-DD string names, or None. ASCII digits only: never
    fromisoformat, never \\d."""
    if not _DATE_TEXT.fullmatch(text):
        return None
    try:
        return datetime.date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
    except ValueError:
        return None


def _check(value, kind, rng):
    """The one rule `value` breaks (wrong type first), or None when it is valid."""
    if kind == "integer":
        if not _is_int(value):
            return WRONG_TYPE + kind
    elif kind == "number":
        if not (_is_int(value) or isinstance(value, float)):
            return WRONG_TYPE + kind
    elif kind == "boolean":
        if not isinstance(value, bool):
            return WRONG_TYPE + kind
    elif kind == "date":
        # A date-time is a subclass of date: refuse it first.
        if isinstance(value, datetime.datetime):
            return WRONG_TYPE + kind
        if isinstance(value, datetime.date):
            return None
        if not isinstance(value, str):
            return WRONG_TYPE + kind
        return None if _calendar_date(value) else NOT_A_DATE
    if rng is not None:
        text, low, low_ok, high = rng
        # Compare the value as parsed: never convert a huge int to a float, and
        # only a float can be non-finite (math.isfinite of a huge int overflows).
        if isinstance(value, float) and not math.isfinite(value):
            return OUT_OF_RANGE + text
        if (value < low) or (value == low and not low_ok) or value > high:
            return OUT_OF_RANGE + text
    return None


def _validate(table, rules, base, violations, warnings, required=(), max_lanes=None):
    """Append a violation per broken rule (in `rules` order), then a warning per
    unknown key (in table order). Return (the valid values, the keys that broke
    a rule). `max_lanes`: the valid shared lane pool, which a valid max_lanes
    may not exceed."""
    ok = {}
    bad = set()
    for key, rule_args in rules.items():
        if key not in table:
            if key in required:
                violations.append((base + [key], MISSING))
            continue
        rule = _check(table[key], *rule_args)
        if rule is None and key == "max_lanes" and max_lanes is not None and table[key] > max_lanes:
            rule = EXCEEDS_LANES  # a paused project too
        if rule:
            violations.append((base + [key], rule))
            bad.add(key)
        else:
            ok[key] = table[key]
    for key in table:
        if key not in rules:
            warnings.append(base + [key])
    return ok, bad


def _whole_tokens(x) -> int:
    """x rounded half up to an int, as floor(x + 0.5): a product such as
    60000000 * 4.1 is 245999999.99999997 as a double."""
    if isinstance(x, int) or x >= _WHOLE_DOUBLES:
        return int(x)
    return math.floor(x + 0.5)


def _effective(shared):
    """(min(tokens_per_week, tokens_per_window * windows_per_week), the term
    that binds), the product rounded to whole tokens; a tie binds on
    tokens_per_week."""
    week = shared["tokens_per_week"]
    windows = _whole_tokens(shared["tokens_per_window"] * shared["windows_per_week"])
    if week <= windows:
        return week, BINDING_WEEK
    return windows, BINDING_WINDOWS


def _pool_sum(unpaused, key):
    """Each unpaused project's set reservation, added with + from an integer 0
    in project order. Never sum(), math.fsum or decimal: sum() rounds
    differently from Python 3.12 on, and devagent adds this way."""
    total = 0
    for table, _ in unpaused:
        value = table.get(key)
        if value is not None:
            total = total + value
    return total


def _doc_value(value):
    """A config value as the document writes it: a TOML date as YYYY-MM-DD (a
    valid date string already is)."""
    if isinstance(value, datetime.date):
        return value.isoformat()
    return value


def _project(name, table) -> ProjectCapacity:
    values = {key: _doc_value(table[key]) if key in table else PROJECT_DEFAULTS[key] for key in PROJECT_KEYS}
    return ProjectCapacity(name=name, set_keys=tuple(key for key in PROJECT_KEYS if key in table), **values)


def resolve_capacity(data) -> DeclaredCapacity:
    """The declaration in a parsed devagent config, by devagent's rules."""
    violations = []  # (parts, rule)
    warnings = []  # parts

    # The shared table. `shared_ok` holds each key that is present and valid.
    declared = "capacity" in data
    shared_table = data.get("capacity")
    shared_ok = {}
    if declared and not isinstance(shared_table, dict):
        violations.append((["capacity"], WRONG_TYPE + "table"))
    elif declared:
        required = [key for key in SHARED_KEYS if key not in SHARED_OPTIONAL]
        shared_ok, _ = _validate(shared_table, SHARED_RULES, ["capacity"], violations, warnings, required)

    # Each project, in file order. A capacity that is not a table is read by
    # the pool rules as unpaused with no reservations; its own line reports it.
    project_tables = data.get("project")
    if not isinstance(project_tables, dict):
        project_tables = {}
    projects = []  # (name, capacity table, the keys that broke a rule)
    for name, table in project_tables.items():
        if not isinstance(table, dict):
            continue
        own = {}
        bad = set()
        if "capacity" in table:
            base = ["project", name, "capacity"]
            if not isinstance(table["capacity"], dict):
                violations.append((base, WRONG_TYPE + "table"))
            else:
                own = table["capacity"]
                if not declared:
                    violations.append((base, TABLE_MISSING))
                _, bad = _validate(own, PROJECT_RULES, base, violations, warnings,
                                   max_lanes=shared_ok.get("max_lanes"))
        projects.append((name, own, bad))

    # The pool rules, each only when every value it reads is valid (an absent
    # optional value is valid). Neither runs when [capacity] is absent or not a table.
    paused_ok = all("paused" not in bad for _, _, bad in projects)
    unpaused = [(own, bad) for _, own, bad in projects if own.get("paused") is not True]
    if (paused_ok and "operator_hours_per_week" in shared_ok
            and all("reserve_operator_hours_per_week" not in bad for _, bad in unpaused)):
        total = _pool_sum(unpaused, "reserve_operator_hours_per_week")
        if total > shared_ok["operator_hours_per_week"] + HOURS_TOLERANCE:
            violations.append((["pool"], POOL_HOURS))
    budget = binding = None
    if all(key in shared_ok for key in ("tokens_per_week", "tokens_per_window", "windows_per_week")):
        budget, binding = _effective(shared_ok)
        if paused_ok and all("reserve_tokens_per_week" not in bad for _, bad in unpaused):
            # Strict: the token sum is an exact integer.
            if _pool_sum(unpaused, "reserve_tokens_per_week") > budget:
                violations.append((["pool"], POOL_TOKENS))

    lines = tuple(VIOLATION.format(where=where(parts), rule=rule) for parts, rule in violations)
    warning_lines = tuple(WARNING.format(where=where(parts)) for parts in warnings)
    if lines:
        # devagent prints no document: nothing declared is readable.
        shared_ok, budget, binding, projects = {}, None, None, []
    return DeclaredCapacity(
        declared=declared,
        **{key: shared_ok.get(key) for key in SHARED_KEYS},
        token_budget=budget,
        token_binding_term=binding,
        projects=tuple(_project(name, own) for name, own, _ in projects),
        violations=lines,
        warnings=warning_lines,
    )


def read_declared_capacity(roots, path) -> DeclaredCapacity:
    """The declaration in the devagent config at `path`, read through `roots`.

    A file that cannot be read, or is not valid TOML, raises ConfigError
    (devagent's exit 4): it is never a violation."""
    data = read_toml(roots, path, "the devagent config", "the devagent config ", key="devagent_config")
    return resolve_capacity(data)


def _entry(value, source):
    return {"value": value, "source": source}


def document_text(declared) -> str | None:
    """The capacity document as devagent's reader prints it on stdout, or None
    with a violation, when it prints none."""
    if declared.violations:
        return None
    shared = None
    if declared.declared:
        # A valid declaration sets every required key: None is the optional key unset.
        shared = {}
        for key in SHARED_KEYS:
            value = getattr(declared, key)
            shared[key] = _entry(value, "default" if value is None else "config")
    effective = None
    if declared.token_budget is not None:
        effective = {"value": declared.token_budget, "binding": declared.token_binding_term}
    projects = []
    for project in declared.projects:
        item = {"name": project.name}
        for key in PROJECT_KEYS:
            item[key] = _entry(getattr(project, key), "config" if key in project.set_keys else "default")
        projects.append(item)
    doc = {
        "capacity_schema": declared.capacity_schema,
        "declared": declared.declared,
        "token_unit": declared.token_unit,
        "shared": shared,
        "effective_tokens_per_week": effective,
        "projects": projects,
    }
    return json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
