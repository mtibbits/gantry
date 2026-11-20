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
from gantry.deps import open_sources
from gantry.devagent_records import CHECKLIST_FILE, PRESENT, READ, UNREADABLE, read_checklist
from gantry.fires import RECORDS_NOT_READ
from gantry.portfolio_file import DEFAULTS
from gantry.strategic import replace_panels

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
# unknown dependencies
DEP_KINDS = ("unresolved_ref", "malformed_ref", "foreign_site_absent", "foreign_unresolved")
BY_KIND = "{n} {kind}"                            # joined ", ", zero kinds left out
CROSS_SITE_NONE = "cross-site: no data: no cross-site entries recorded (mtibbits/devagent#887)"
CROSS_SITE_RESOLVED = "resolved cross-site links: {n}, not flagged"
SOURCE_DEPS = "WBS.md depends_on entries on open leaves"
# unplanned work
STUCK_PART = "as seen by this build machine: {n} from STUCK markers"
STUCK_NOT_READ = "as seen by this build machine: STUCK markers not read"
INCOMPLETE = "incomplete: {sources} not read"     # sources joined ", "
INCOMPLETE_COUNT = "{n}, incomplete: {sources} not read"
SOURCE_NAMES = {"fires_list": "fires list", "p0_leaf": "P0 leaves", "stuck": "STUCK markers", "fires": "fires step"}
SOURCE_UNPLANNED = "the fire collector: fires list, open P0 leaves and STUCK markers"
FIRES_LIST_NOT_CONFIGURED = "; fires list not configured"  # appended to SOURCE_UNPLANNED
FIRES_NO_SITE = "Fires with no site: {text}"      # {text}: the count, INCOMPLETE or NO_FIRES_LIST
NO_FIRES_LIST = "no data: no fires list configured"
SOURCE_FIRES_NO_SITE = "the fires list"
# conflicting priorities
GAP_TEXT = "{points} points, backlog basis"
SOURCE_PRIORITIES = "the panel's gap: backlog share against intended share, backlog basis"
# neglected work
NEGLECT_DATE = "last state change {date}"
NEGLECT_NONE = "no state change in {n} weeks"
NEGLECT_SINCE = "no state change since history began {date}"
NEGLECT_UNCOMMITTED = "uncommitted changes to `WBS.md` not counted"
SOURCE_NEGLECT = "WBS.md history in the planning repository"

TEXTS = (ROW_TITLE, NO_DATA, MACHINE, UNCOMMITTED, NOT_CHECKED, FLAGGED, NOT_FLAGGED, THRESHOLD_ABOVE,
         THRESHOLD_POINTS, THRESHOLD_WEEKS, THIEVES_FAILED, HISTORY_NOT_READ, WIP_PARKED, WIP_STARTED,
         WIP_PARKED_NO_DATA, WIP_STARTED_NO_DATA, HERE_PARKED, HERE_STARTED, HERE_PARKED_UNCHECKED,
         HERE_STARTED_UNCHECKED, HERE_UNREADABLE, HERE_NONE, HERE_NOT_READ, LANES, SOURCE_WIP, BY_KIND,
         CROSS_SITE_NONE, CROSS_SITE_RESOLVED, SOURCE_DEPS, STUCK_PART, STUCK_NOT_READ, INCOMPLETE,
         INCOMPLETE_COUNT, *SOURCE_NAMES.values(), SOURCE_UNPLANNED, FIRES_LIST_NOT_CONFIGURED, FIRES_NO_SITE,
         NO_FIRES_LIST, SOURCE_FIRES_NO_SITE, GAP_TEXT, SOURCE_PRIORITIES, NEGLECT_DATE, NEGLECT_NONE,
         NEGLECT_SINCE, NEGLECT_UNCOMMITTED, SOURCE_NEGLECT, *TITLES.values())

STARTED_GLYPHS = frozenset("~!?")
UNREAD_STATUSES = frozenset({"not_read", "read_with_problems"})  # a source that could not be read in full (D5)
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
class NoSiteFires:
    count: int | None  # None: not known
    text: str
    source: str


@dataclass(frozen=True)
class Thieves:
    wip: Thief
    unknown_deps: Thief
    unplanned: Thief
    priorities: Thief
    neglect: Thief
    uncommitted: bool | None = None  # the site's working-tree WBS.md against HEAD; None: not checked


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


def _git_failed(reason) -> bool:
    return reason == wbs_history.GIT_NOT_FOUND or reason.startswith(wbs_history.GIT_FAILED.split("{")[0])


class _Unchecked(Exception):
    """A committed read gave no data: the whole site is unchecked (D6)."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _committed_states(history, issues):
    """{issue: ChecklistState or None (absent at HEAD)} for `issues`, read
    through the ls-tree prefilter; raises _Unchecked on any no-data answer.
    It reads HEAD only, so a history answer that is no data for the walk
    (shallow history, no commit in the window) still reads through its
    repository; one with no repository, or where git itself failed, makes no
    git call (one failure per site, not two)."""
    if isinstance(history.repo, wbs_history.NoData):
        raise _Unchecked(history.repo.reason)
    if history.no_data is not None and _git_failed(history.no_data.reason):
        raise _Unchecked(history.no_data.reason)
    listed = wbs_history.committed_paths(history.repo, CHECKLIST_FILE)
    if isinstance(listed, wbs_history.NoData):
        raise _Unchecked(listed.reason)
    out = {}
    for issue in issues:
        if issue in out:
            continue
        path = f"{issue}/{CHECKLIST_FILE}"
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


# --- unknown dependencies --------------------------------------------------------------------


def _open_lines(data) -> set:
    """The lines of the nodes whose entries an open leaf takes (D4)."""
    row_at = {row.outline: row for row in data.rows}
    return {node.line for leaf in data.leaves if leaf.open for node in open_sources(leaf, row_at)}


def deps_thief(name, data, foreign, history, threshold) -> Thief:
    lines = _open_lines(data)
    counts = dict.fromkeys(DEP_KINDS, 0)
    for entry in data.report:
        if entry.kind in counts and entry.line in lines:
            counts[entry.kind] += 1
    value = sum(counts.values())
    parts = []
    if value:
        parts.append(Part(", ".join(BY_KIND.format(n=n, kind=kind) for kind, n in counts.items() if n)))
    if not any(foreign.values()):
        parts.append(Part(CROSS_SITE_NONE))
    else:
        resolved = sum(o.status in ("open", "satisfied") and o.source_line in lines for o in foreign.get(name, ()))
        parts.append(Part(CROSS_SITE_RESOLVED.format(n=resolved)))
    return Thief("unknown_deps", value, str(value), threshold, value > threshold, SOURCE_DEPS,
                 by_kind=tuple(counts.items()), parts=tuple(parts), label=wbs_label(history))


# --- unplanned work ----------------------------------------------------------------------------


def _unread(statuses) -> list[str]:
    """The names of the sources not read in full, first-seen order, once each."""
    names = [SOURCE_NAMES.get(s.source, s.source) for s in statuses if s.status in UNREAD_STATUSES]
    return list(dict.fromkeys(names))


def _counted_text(n, unread):
    """(value, text) of a count over sources some of which may be unread."""
    if not unread:
        return n, str(n)
    sources = ", ".join(unread)
    if n:
        return n, INCOMPLETE_COUNT.format(n=n, sources=sources)
    return None, INCOMPLETE.format(sources=sources)


def unplanned_thief(name, fires, threshold) -> Thief:
    if fires is None:
        value, text = _counted_text(0, [SOURCE_NAMES["fires"]])
        return Thief("unplanned", value, text, threshold, False, SOURCE_UNPLANNED,
                     parts=(Part(STUCK_NOT_READ, machine=True),))
    mine = [f for f in fires.fires if f.site == name]
    statuses = [s for s in fires.sources if s.site in (name, None)]
    value, text = _counted_text(len(mine), _unread(statuses))
    source = SOURCE_UNPLANNED
    if any(s.source == "fires_list" and s.status == "not_configured" for s in statuses):
        source += FIRES_LIST_NOT_CONFIGURED
    stuck = [s for s in statuses if s.source == "stuck"]
    # read_with_problems still found its markers, and they count: show how many (the value says incomplete).
    if stuck and all(s.status != "not_read" for s in stuck):
        part = Part(STUCK_PART.format(n=sum(f.this_machine for f in mine)), machine=True)
    else:
        part = Part(STUCK_NOT_READ, machine=True)
    flagged = value is not None and value > threshold
    return Thief("unplanned", value, text, threshold, flagged, source, parts=(part,))


def fires_without_site(fires) -> NoSiteFires:
    """The fires that name no site, counted once for the page."""
    if fires is None:
        return NoSiteFires(None, INCOMPLETE.format(sources=SOURCE_NAMES["fires"]), SOURCE_FIRES_NO_SITE)
    statuses = [s for s in fires.sources if s.site is None]
    if any(s.source == "fires_list" and s.status == "not_configured" for s in statuses):
        return NoSiteFires(None, NO_FIRES_LIST, SOURCE_FIRES_NO_SITE)
    count, text = _counted_text(sum(f.site is None for f in fires.fires), _unread(statuses))
    return NoSiteFires(count, text, SOURCE_FIRES_NO_SITE)


# --- conflicting priorities --------------------------------------------------------------------


def points(gap) -> int:
    """The gap in whole points, half up: the view's rounding (D9)."""
    return math.floor(gap + 0.5)


def priorities_thief(panel, threshold) -> Thief:
    gap = panel.gap
    if gap is None:
        return no_data_thief("priorities", panel.gap_reason or "no gap", threshold, SOURCE_PRIORITIES)
    flagged = gap > threshold and not math.isclose(gap, threshold, rel_tol=1e-9, abs_tol=1e-9)
    return Thief("priorities", gap, GAP_TEXT.format(points=points(gap)), threshold, flagged, SOURCE_PRIORITIES)


# --- every site --------------------------------------------------------------------------------


def _missing_history() -> wbs_history.HistoryResult:
    no_data = wbs_history.NoData(HISTORY_NOT_READ)
    return wbs_history.HistoryResult(None, False, 0, None, no_data.text, no_data, no_data)


SOURCES = {"wip": SOURCE_WIP, "unknown_deps": SOURCE_DEPS, "unplanned": SOURCE_UNPLANNED,
           "priorities": SOURCE_PRIORITIES, "neglect": SOURCE_NEGLECT}


def derive_site(name, result, panel, limits, fires, records, history, as_of) -> Thieves:
    data = result.inputs[name].data
    return Thieves(
        wip=wip_thief(data, history, records, limits["wip"]),
        unknown_deps=deps_thief(name, data, result.foreign, history, limits["unknown_deps"]),
        unplanned=unplanned_thief(name, fires, limits["unplanned"]),
        priorities=priorities_thief(panel, limits["priorities"]),
        neglect=neglect_thief(history, limits["neglect"], as_of),
        uncommitted=history.uncommitted,
    )


def failed_site(e: Exception, limits) -> Thieves:
    """Five no-data thieves for a site whose derivation raised (D8)."""
    reason = THIEVES_FAILED.format(type=type(e).__name__)
    return Thieves(*(no_data_thief(kind, reason, limits[kind], SOURCES[kind]) for kind in KINDS))


def failed(thieves) -> bool:
    """Whether a site's thieves are the failed-derivation ones."""
    return thieves.wip.no_data is not None and thieves.wip.no_data.startswith(THIEVES_FAILED.split("{")[0])


def derive_thieves(result, model, portfolio, fires, records, history, as_of) -> dict[str, "Thieves"]:
    """{built site: Thieves}, in the model's panel order. A site missing from
    `history` or `records` reads as not read (fail closed)."""
    limits = thresholds(portfolio)
    out = {}
    for name, panel in model.panels.items():
        if not panel.built:
            continue
        try:
            site_history = history.get(name) or _missing_history()
            out[name] = derive_site(name, result, panel, limits, fires, records.get(name), site_history, as_of)
        except Exception as e:  # one site's fault never fails the page (D8)
            out[name] = failed_site(e, limits)
    return out


def attach(model, by_site, no_site):
    """The model with each built panel's Thieves and the site-less fires attached."""
    return replace_panels(model, {name: replace(p, thieves=by_site.get(name)) for name, p in model.panels.items()},
                          fires_no_site=no_site)
