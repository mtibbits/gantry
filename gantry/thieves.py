"""The five time thieves (#29): for each built site, too much work in
progress, unknown dependencies, unplanned work, conflicting priorities and
neglected work, each with its value, threshold, flag and source, or
"no data: <reason>". docs/strategic-page.md states the rules.

Derived from what the build already holds (the resolved inputs, the fire
report, the devagent records and the strategic model) plus each site's
WBS.md history (gantry.wbs_history, #28). Every git read goes through that
module's attributes, so a test can spy on it. Nothing here reads the clock:
the as-of date is passed. Nothing here renders: the view
(gantry.views.strategic) does, escaping every text once.
"""

import math
from dataclasses import dataclass, replace
from datetime import date

from gantry import wbs_history
from gantry.config import ID_TOKEN_RE
from gantry.devagent_records import PRESENT, READ, UNREADABLE, read_checklist
from gantry.fires import RECORDS_NOT_READ
from gantry.portfolio_file import DEFAULTS

KINDS = ("wip", "unknown_deps", "unplanned", "priorities", "neglect")
FIGURES = {"wip": "thief-wip", "unknown_deps": "thief-unknown-deps", "unplanned": "thief-unplanned",
           "priorities": "thief-priorities", "neglect": "thief-neglect"}
FIRES_NO_SITE_FIGURE = "fires-no-site"
TITLES = {"wip": "Too much WIP", "unknown_deps": "Unknown dependencies", "unplanned": "Unplanned work",
          "priorities": "Conflicting priorities", "neglect": "Neglected work"}
# The portfolio file's threshold key for each thief.
THRESHOLD_KEYS = {"wip": "wip_max", "unknown_deps": "unknown_deps_max", "unplanned": "unplanned_max",
                  "priorities": "share_gap_points", "neglect": "neglect_weeks"}
ROW_TITLE = "Time thieves"
NO_DATA = "no data: {reason}"
MACHINE = "as seen by this build machine"
UNCOMMITTED = "as seen by this build machine: `WBS.md` has uncommitted changes"
NOT_CHECKED = "as seen by this build machine: not checked against a commit ({reason})"
FLAGGED, NOT_FLAGGED = "flagged", "not flagged"
THRESHOLD_ABOVE = "flagged above {n}"            # wip, unknown_deps, unplanned
THRESHOLD_POINTS = "flagged above {n} points"    # priorities
THRESHOLD_WEEKS = "flagged at {n} weeks or more"  # neglect
THIEVES_FAILED = "thieves not derived: {type}"
HISTORY_NOT_READ = "history not read for this site"
# WIP
WIP_PARKED = "{n} parked, not counted"
WIP_STARTED = "{n} started, glyph pending"
WIP_PARKED_NO_DATA = "parked, not counted: no data: {reason}"
WIP_STARTED_NO_DATA = "started, glyph pending: no data: {reason}"
HERE_PARKED = "{n} parked here only, still counted: {issues}"
HERE_STARTED = "{n} started here only, glyph pending, not counted: {issues}"
HERE_PARKED_UNCHECKED = "{n} parked here, still counted: {issues}"
HERE_STARTED_UNCHECKED = "{n} started here, glyph pending, not counted: {issues}"
HERE_UNREADABLE = "checklists here not read: {n} ({issues})"
HERE_NONE = "nothing parked or started here"
HERE_NOT_READ = "checklists here not read: {reason}"
LANES = "lanes in flight: no data: lane records are per-machine (mtibbits/devagent#748)"
SOURCE_WIP = "WBS.md glyphs and committed checklists; this machine's checklists for the labelled part"
# neglected work
NEGLECT_DATE = "last state change {date}"
NEGLECT_NONE = "no state change in {n} weeks"
NEGLECT_SINCE = "no state change since history began {date}"
NEGLECT_UNCOMMITTED = "uncommitted changes to `WBS.md` not counted"
SOURCE_NEGLECT = "WBS.md history in the planning repository"

STARTED_GLYPHS = frozenset("~!?")
PENDING = " "


@dataclass(frozen=True)
class Part:
    text: str
    machine: bool = False  # this build machine's view, labelled


@dataclass(frozen=True)
class Thief:
    kind: str
    value: int | float | date | None
    text: str
    threshold: int | float
    flagged: bool
    source: str
    no_data: str | None = None  # the reason, when the thief has no value
    by_kind: tuple[tuple[str, int], ...] = ()
    parts: tuple[Part, ...] = ()
    label: str | None = None  # the whole figure's machine label
    counts: tuple[tuple[str, int | None], ...] = ()  # WIP: ("parked", n), ("started", n); None = no data


@dataclass(frozen=True)
class Thieves:
    wip: Thief
    unknown_deps: Thief
    unplanned: Thief
    priorities: Thief
    neglect: Thief


def thresholds(portfolio) -> dict[str, int | float]:
    """{thief kind: threshold}: the portfolio file's [thresholds] value, else the default."""
    given = portfolio.thresholds if portfolio is not None else None
    out = {}
    for kind, key in THRESHOLD_KEYS.items():
        value = getattr(given, key, None) if given is not None else None
        out[kind] = value if value is not None else DEFAULTS[f"thresholds.{key}"]
    return out


def no_data_thief(kind, reason, threshold, source) -> Thief:
    return Thief(kind, None, NO_DATA.format(reason=reason), threshold, False, source, no_data=reason)


# --- the machine labels ------------------------------------------------------------------------


def _unchecked_reason(history) -> str:
    if history.no_data is not None:
        return history.no_data.reason
    return (history.uncommitted_reason or "").removeprefix("no data: ")


def wbs_label(history) -> str | None:
    """The label on figures read from WBS.md: uncommitted, not checked, or none."""
    if history.uncommitted is True:
        return UNCOMMITTED
    if history.uncommitted is False:
        return None
    return NOT_CHECKED.format(reason=_unchecked_reason(history))


# --- WIP ---------------------------------------------------------------------------------------


def _valid_issue(issue) -> bool:
    return issue is not None and ID_TOKEN_RE.fullmatch(issue) is not None


class _Unchecked(Exception):
    """A committed read gave no data: the whole site is unchecked (D6)."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _committed_states(history, issues):
    """{issue: ChecklistState or None (absent at HEAD)} for `issues`, read
    through the ls-tree prefilter; raises _Unchecked on any no-data answer."""
    if history.no_data is not None:
        raise _Unchecked(history.no_data.reason)
    listed = wbs_history.committed_paths(history.repo)
    if isinstance(listed, wbs_history.NoData):
        raise _Unchecked(listed.reason)
    out = {}
    for issue in issues:
        if issue in out:
            continue
        path = f"{issue}/checklist.md"
        if path not in listed:
            out[issue] = None
            continue
        text = wbs_history.committed_text(history.repo, path)
        if isinstance(text, wbs_history.NoData):
            raise _Unchecked(text.reason)
        out[issue] = None if isinstance(text, wbs_history.Absent) else read_checklist(text)
    return out


def _listed(issues) -> str:
    """The issues once each, in WBS.md order."""
    return ", ".join(dict.fromkeys(issues))


def _local(records, issue):
    """(state, unreadable) of the issue's checklist on this machine: state is
    a ChecklistState or None."""
    found = records.issues.get(issue) if records is not None else None
    if found is None:
        return None, False
    if found.checklist.status == PRESENT:
        return found.checklist.value, False
    return None, found.checklist.status == UNREADABLE


def _machine_part(prefix, pieces, records, none_text) -> Part | None:
    if records is None or records.status != READ:
        reason = records.reason if records is not None and records.reason else RECORDS_NOT_READ
        return Part(f"{prefix}: {HERE_NOT_READ.format(reason=reason)}", machine=True)
    if not pieces:
        return None if none_text is None else Part(f"{prefix}: {none_text}", machine=True)
    return Part(f"{prefix}: " + "; ".join(pieces), machine=True)


def _pieces(groups):
    """'n text: ids' for each non-empty (template, leaves' issues) group."""
    return [template.format(n=len(issues), issues=_listed(issues)) for template, issues in groups if issues]


def wip_thief(data, history, records, threshold) -> Thief:
    open_leaves = [leaf for leaf in data.leaves if leaf.open]
    started = [leaf for leaf in open_leaves if leaf.glyph in STARTED_GLYPHS]
    pending = [leaf for leaf in open_leaves if leaf.glyph == PENDING]
    try:
        committed = _committed_states(history, [leaf.issue for leaf in open_leaves
                                                if leaf.glyph in STARTED_GLYPHS | {PENDING} and _valid_issue(leaf.issue)])
        checked = True
    except _Unchecked as e:
        committed, checked, reason = {}, False, e.reason

    def at_head(leaf):
        return committed.get(leaf.issue) if _valid_issue(leaf.issue) else None

    def here(leaf):
        return _local(records, leaf.issue) if _valid_issue(leaf.issue) else (None, False)

    if checked:
        candidates = [leaf for leaf in open_leaves
                      if leaf.glyph in STARTED_GLYPHS
                      or (leaf.glyph == PENDING and at_head(leaf) is not None and at_head(leaf).shows_progress)]
        parked = [leaf for leaf in candidates if at_head(leaf) is not None and at_head(leaf).parked]
        counted = [leaf for leaf in candidates if leaf not in parked]
        started_n = sum(leaf.glyph == PENDING for leaf in counted)
        parts = [Part(WIP_PARKED.format(n=len(parked))), Part(WIP_STARTED.format(n=started_n))]
        counts = (("parked", len(parked)), ("started", started_n))
        rest = [leaf for leaf in pending if leaf not in candidates]
        prefix, here_parked, here_started, none_text = MACHINE, HERE_PARKED, HERE_STARTED, None
    else:
        counted = started
        parts = [Part(WIP_PARKED_NO_DATA.format(reason=reason)), Part(WIP_STARTED_NO_DATA.format(reason=reason))]
        counts = (("parked", None), ("started", None))
        rest = pending
        prefix = NOT_CHECKED.format(reason=reason)
        here_parked, here_started, none_text = HERE_PARKED_UNCHECKED, HERE_STARTED_UNCHECKED, HERE_NONE

    parked_here, started_here, unreadable = [], [], []
    for leaf in data.leaves:
        if leaf in counted:
            state, bad = here(leaf)
            if state is not None and state.parked:
                parked_here.append(leaf.issue)
        elif leaf in rest:
            state, bad = here(leaf)
            if state is not None and state.shows_progress and not state.parked:
                started_here.append(leaf.issue)
        else:
            continue
        if bad:
            unreadable.append(leaf.issue)
    pieces = _pieces(((here_parked, parked_here), (here_started, started_here), (HERE_UNREADABLE, unreadable)))
    machine = _machine_part(prefix, pieces, records, none_text)
    if machine is not None:
        parts.append(machine)
    parts.append(Part(LANES))
    value = len(counted)
    return Thief("wip", value, str(value), threshold, value > threshold, SOURCE_WIP, parts=tuple(parts),
                 label=wbs_label(history), counts=counts)


# --- neglected work ----------------------------------------------------------------------------


def neglect_thief(history, weeks, as_of) -> Thief:
    if history.no_data is not None:
        return no_data_thief("neglect", history.no_data.reason, weeks, SOURCE_NEGLECT)
    parts = (Part(NEGLECT_UNCOMMITTED),) if history.uncommitted is True else ()
    day = history.date
    if day is None:
        return Thief("neglect", None, NEGLECT_NONE.format(n=weeks), weeks, True, SOURCE_NEGLECT, parts=parts)
    template = NEGLECT_SINCE if history.history_start else NEGLECT_DATE
    flagged = (as_of - day).days >= 7 * weeks
    return Thief("neglect", day, template.format(date=day.isoformat()), weeks, flagged, SOURCE_NEGLECT, parts=parts)


# --- not derived yet (Task 3) ------------------------------------------------------------------


def _pending_thief(kind, threshold, history) -> Thief:
    return Thief(kind, None, NO_DATA.format(reason="not derived"), threshold, False, "", no_data="not derived",
                 label=wbs_label(history) if kind == "unknown_deps" else None)


# --- every site --------------------------------------------------------------------------------


def _missing_history() -> wbs_history.HistoryResult:
    no_data = wbs_history.NoData(HISTORY_NOT_READ)
    return wbs_history.HistoryResult(None, False, 0, None, no_data.text, no_data, no_data)


def derive_site(name, result, panel, portfolio, fires, records, history, as_of) -> Thieves:
    limits = thresholds(portfolio)
    data = result.inputs[name].data
    return Thieves(
        wip=wip_thief(data, history, records, limits["wip"]),
        unknown_deps=_pending_thief("unknown_deps", limits["unknown_deps"], history),
        unplanned=_pending_thief("unplanned", limits["unplanned"], history),
        priorities=_pending_thief("priorities", limits["priorities"], history),
        neglect=neglect_thief(history, limits["neglect"], as_of),
    )


def derive_thieves(result, model, portfolio, fires, records, history, as_of) -> dict[str, "Thieves"]:
    """{built site: Thieves}, in the model's panel order. A site missing from
    `history` or `records` reads as not read (fail closed)."""
    out = {}
    for name, panel in model.panels.items():
        if not panel.built:
            continue
        site_history = history.get(name) or _missing_history()
        out[name] = derive_site(name, result, panel, portfolio, fires, records.get(name), site_history, as_of)
    return out
