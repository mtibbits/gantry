"""The fire collector (#32): one ordered FireReport per full build, across
every site. docs/daily-screen.md states the rules.

A pure collector: it reads only what build_all hands it (the BuildResult,
the config, the fires list as the run read it, {site: SiteRecords} and one
aware reference time) and never opens a file or reads the clock. Each
source in the ordered registry SOURCES returns fires and a read status for
what it read; collect_fires isolates a source that fails, merges an open P0
leaf with its STUCK fire, orders the fires and builds the report, so
"nothing on fire" (FireReport.confirmed_none) is never said by accident.

Callers: the daily screen (#34) shows the report; the time thieves (#29)
count its fires per site and its site-less fires; the warm-up list (#33)
reads the same build's records. FireSource,
FireContext and SourceResult are provisional until a second source outside
this module binds to them. Record and fires-list text is kept verbatim:
escaping happens once, where a page renders it.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from gantry import fires_list, priority
from gantry.devagent_records import ABSENT, FOLDER_MISSING, PRESENT, READ, UNREADABLE
from gantry.sites import NO_WBS
from gantry.stamp import public_text

KINDS = ("fires_list", "p0_leaf", "stuck")  # also the three source names, in registry order
STATUSES = ("read", "read_with_problems", "absent", "not_read", "not_configured")
OK_STATUSES = frozenset({"read", "absent"})  # every other status stops all_read
OPEN_STATES = ("pending", "in_progress", "stuck", "blocked_external")  # keyed on Leaf.state, never Leaf.open
FAIL_CLOSED = ("leaf_unknown", "parked_unknown")


@dataclass(frozen=True)
class Fire:
    kinds: tuple[str, ...]  # source names, in registry order
    why: tuple[str, ...]  # one per kind, parallel to kinds
    site: str | None = None  # a configured site name
    site_as_written: str | None = None  # a fires-list @name that is not a site
    issue: str | None = None  # the leaf's issue: or the folder name
    leaf_key: str | None = None
    title: str | None = None
    unreadable: bool = False
    fail_closed: str | None = None  # None, "leaf_unknown" or "parked_unknown"
    since: datetime | None = None  # aware; in the reference's zone when read from a naive time
    date_only: bool = False
    age: timedelta | None = None  # never below 0; None exactly when since is None
    still_a_fire: bool | None = None  # None unless "fires_list" is in kinds

    @property
    def this_machine(self) -> bool:
        """STUCK markers are this machine's."""
        return "stuck" in self.kinds


@dataclass(frozen=True)
class SourceStatus:
    source: str
    site: str | None  # None for a source that is not read per site
    status: str  # one of STATUSES
    reason: str | None  # public text: no absolute path


@dataclass(frozen=True)
class SourceResult:  # provisional
    fires: tuple
    statuses: tuple
    stuck_parked: dict = field(default_factory=dict)  # {site: folder names}
    stuck_no_open_leaf: dict = field(default_factory=dict)  # {site: folder names}


@dataclass(frozen=True)
class FireContext:  # provisional
    result: object  # the BuildResult
    config: object
    fires_list: object  # the FiresList the run read
    records: dict  # {site: SiteRecords}
    reference: datetime  # aware


@dataclass(frozen=True)
class FireReport:
    fires: tuple[Fire, ...]
    sources: tuple[SourceStatus, ...]
    stuck_parked: dict[str, tuple[str, ...]] = field(default_factory=dict)
    stuck_no_open_leaf: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def all_read(self) -> bool:
        """Every source read fully; false with no status at all (fail closed)."""
        return bool(self.sources) and all(s.status in OK_STATUSES for s in self.sources)

    @property
    def confirmed_none(self) -> bool:
        """Nothing on fire, and every source read: the one "nothing on fire"."""
        return self.all_read and not self.fires

    @property
    def not_fully_read(self) -> int:
        return sum(s.status not in OK_STATUSES for s in self.sources)

    def by_site(self) -> dict:
        """{site or None: fires}, keys in order of first appearance."""
        found = {}
        for fire in self.fires:
            found.setdefault(fire.site, []).append(fire)
        return {site: tuple(group) for site, group in found.items()}


def _aware(value) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def fire_reference(config, now, local=None) -> datetime:
    """The build's one aware reference time: the as-of date at 00:00 UTC when
    the config pins as_of, else `now` in the local zone. `local` is a tzinfo
    (None: the system zone). The rule itself is gantry.fires_list's."""
    if not _aware(now):
        raise ValueError("the build time must be timezone-aware")
    here = now.astimezone(local)
    naive = fires_list.fires_reference(config, here.replace(tzinfo=None))
    zone = timezone.utc if config.as_of_pinned else timezone(here.utcoffset())
    return naive.replace(tzinfo=zone)


def naive_reference(reference) -> datetime:
    """The reference with its zone removed, as gantry.fires_list takes it."""
    return reference.replace(tzinfo=None)


def _in_zone(value, reference):
    """An aware time: a naive one is read in the reference's zone."""
    if value is None or _aware(value):
        return value
    return value.replace(tzinfo=reference.tzinfo)


def _age(since, reference):
    return None if since is None else max(reference - since, timedelta(0))


# --- sites --------------------------------------------------------------------------------

# WBS.md lines the parser did not read as leaves: a mistyped P0, or the leaf a STUCK marker names, may hide
# there (fail closed). no_nodes is a WBS.md with no node line at all.
UNREAD_LINE_KINDS = ("bad_priority", "skipped_line", "no_nodes")


@dataclass(frozen=True)
class SiteView:
    name: str
    wbs: str  # "read", "absent" or "not_read"
    reason: str | None
    leaves: tuple
    unread_lines: int = 0  # report entries of an UNREAD_LINE_KINDS kind


def site_views(result) -> dict[str, SiteView]:
    """{site: SiteView}, every site in config order: the one definition of
    "was this site's WBS.md read" that every per-site source reads. A site
    whose trace or render failed after its read stays read."""
    summaries = {s.name: s for s in result.summaries}
    views = {}
    for entry in result.sites:
        name = entry.name
        if name in result.inputs:
            data = result.inputs[name].data
            unread = sum(e.kind in UNREAD_LINE_KINDS for e in data.report)
            views[name] = SiteView(name, "read", None, tuple(data.leaves), unread)
        elif entry.problem == NO_WBS:
            views[name] = SiteView(name, "absent", entry.problem, ())
        else:
            summary = summaries.get(name)
            reason = summary.reason if summary is not None and summary.reason else entry.problem
            views[name] = SiteView(name, "not_read", reason, ())
    return views


# --- sources --------------------------------------------------------------------------------


class FireSource:  # provisional
    """A source of fires: `collect(context)` returns a SourceResult."""

    name = None

    def collect(self, context) -> SourceResult:
        raise NotImplementedError


def source_failed(e: Exception, what="source") -> str:
    return f"{what} failed: {type(e).__name__}"


class PerSiteSource(FireSource):
    """A source read site by site, in config order, each site in its own
    `try`: a site that raises gets one not_read status, and its fires and
    counts are dropped."""

    def collect(self, context) -> SourceResult:
        views = site_views(context.result)
        found, statuses, parked, no_leaf = [], [], {}, {}
        for entry in context.result.sites:
            try:
                got = self.collect_site(context, entry, views[entry.name])
            except Exception as e:
                statuses.append(SourceStatus(self.name, entry.name, "not_read", source_failed(e)))
                continue
            found += got.fires
            statuses += got.statuses
            parked.update(got.stuck_parked)
            no_leaf.update(got.stuck_no_open_leaf)
        return SourceResult(tuple(found), tuple(statuses), parked, no_leaf)

    def collect_site(self, context, entry, view) -> SourceResult:
        raise NotImplementedError

    def status(self, context, site, status, reason=None) -> SourceStatus:
        if reason is not None:
            reason = public_text(reason, context.result.roots)
        return SourceStatus(self.name, site, status, reason)


class FiresListSource(FireSource):
    """Every fires-list entry that is not ended; one status for the file."""

    name = "fires_list"

    def collect(self, context) -> SourceResult:
        listed, reference = context.fires_list, context.reference
        naive = naive_reference(reference)
        found = []
        for entry in listed.entries:
            if entry.ended:
                continue
            unreadable = entry.state == "unreadable"
            found.append(Fire(
                kinds=(self.name,),
                why=(entry.raw if unreadable else entry.reason,),
                site=entry.site if entry.site_known else None,
                site_as_written=None if entry.site_known else entry.site,
                unreadable=unreadable,
                since=_in_zone(fires_list.entry_since(entry), reference),
                date_only=entry.date is not None and entry.time is None,
                age=fires_list.entry_age(entry, naive),
                still_a_fire=fires_list.still_a_fire(entry, naive, context.config.fires_check_days),
            ))
        reason = listed.reason
        if listed.status == "read_with_problems":
            reason = f"fires list report entries: {len(listed.report)}"
        return SourceResult(tuple(found), (SourceStatus(self.name, None, listed.status, reason),))


def in_open_state(leaf) -> bool:
    return leaf.state in OPEN_STATES


class P0Source(PerSiteSource):
    """Every open leaf whose own priority is P0 by gantry.priority's rule."""

    name = "p0_leaf"

    def collect_site(self, context, entry, view) -> SourceResult:
        found = tuple(Fire(kinds=(self.name,), why=("P0 leaf",), site=view.name, issue=leaf.issue, leaf_key=leaf.key,
                           title=leaf.title)
                      for leaf in view.leaves if priority.rank(leaf.priority) == 0 and in_open_state(leaf))
        if view.unread_lines:  # only a read WBS.md has unread lines
            status = self.status(context, view.name, "read_with_problems",
                                 f"WBS.md lines not read as leaves: {view.unread_lines}")
        else:
            status = self.status(context, view.name, view.wbs, view.reason)
        return SourceResult(found, (status,))


LEAF_UNKNOWN_WHY = "leaf unknown: WBS.md was not read into the build"
PARKED_UNKNOWN_WHY = "parked unknown: checklist.md could not be read"
MARKER_UNKNOWN_WHY = "step and reason unknown: STUCK could not be read"
RECORDS_NOT_READ = "records not read"
FINISHED_STATES = ("done", "skipped")


def holds_marker(records) -> bool:
    """Whether an issue folder holds a STUCK marker. A folder that vanished
    between listings holds none, as #31 reads a STUCK deleted between listing
    and read; any other folder #31 could not list holds an unreadable one."""
    record = records.stuck
    if record.status == ABSENT:
        return False
    return not (record.status == UNREADABLE and record.reason == FOLDER_MISSING)


def marker_why(record) -> str:
    """`step N name: reason`, with what is missing named as unknown."""
    if record.status != PRESENT:
        return MARKER_UNKNOWN_WHY
    m = record.value
    step = "step unknown" if m.step is None else f"step {m.step} {m.step_name}"
    reason = m.reason if m.reason is not None and m.reason.strip() != "" else "reason unknown"
    return f"{step}: {reason}"


class StuckSource(PerSiteSource):
    """Every STUCK marker in a site's issue folders. Each has exactly one
    outcome, tested in this order: a fire when the site's WBS.md was not read
    (leaf_unknown); the no-open-leaf count when no leaf names the folder or
    the first leaf that does is done or skipped (or the site has no WBS.md);
    the parked count when that leaf or its checklist is parked; else a fire
    (parked_unknown when the checklist could not be read)."""

    name = "stuck"

    def collect_site(self, context, entry, view) -> SourceResult:
        name = entry.name
        if entry.planning_dir is None:
            return SourceResult((), (self.status(context, name, "not_read", entry.problem),))
        records = context.records.get(name)
        if records is None:
            return SourceResult((), (self.status(context, name, "not_read", RECORDS_NOT_READ),))
        if records.status != READ:
            return SourceResult((), (self.status(context, name, "not_read", records.reason),))
        joined = {}
        for leaf in view.leaves:
            joined.setdefault(leaf.issue, leaf)
        found, parked, no_leaf = [], [], []
        for issue in sorted(records.issues):
            folder = records.issues[issue]
            if not holds_marker(folder):
                continue
            leaf = joined.get(issue)
            checklist = folder.checklist
            if view.wbs == "not_read":
                found.append(self.fire(context, name, issue, None, folder, LEAF_UNKNOWN_WHY, "leaf_unknown"))
            elif view.wbs == "absent" or leaf is None or leaf.state in FINISHED_STATES:
                no_leaf.append(issue)
            elif leaf.state == "parked" or (checklist.status == PRESENT and checklist.value.parked):
                parked.append(issue)
            elif checklist.status == UNREADABLE:
                found.append(self.fire(context, name, issue, leaf, folder, PARKED_UNKNOWN_WHY, "parked_unknown"))
            else:
                found.append(self.fire(context, name, issue, leaf, folder))
        problems = []
        partly = sum(f.unreadable or f.fail_closed == "parked_unknown" or f.since is None for f in found)
        if partly:
            problems.append(f"stuck fires not fully read: {partly}")
        if records.skipped:
            problems.append(f"linked issue folders not read: {len(records.skipped)}")
        if no_leaf and view.unread_lines:
            problems.append(f"markers with no open leaf while WBS.md lines were not read: {len(no_leaf)}")
        if problems:
            status = self.status(context, name, "read_with_problems", "; ".join(problems))
        else:
            status = self.status(context, name, "read")
        return SourceResult(tuple(found), (status,), {name: tuple(parked)} if parked else {},
                            {name: tuple(no_leaf)} if no_leaf else {})

    def fire(self, context, site, issue, leaf, folder, closed_why=None, fail_closed=None) -> Fire:
        record = folder.stuck
        created = record.value.created if record.status == PRESENT else None
        since = _in_zone(created, context.reference)
        why = marker_why(record)
        if closed_why is not None:
            why = f"{closed_why}; {why}"
        return Fire(
            kinds=(self.name,), why=(why,), site=site, issue=issue,
            leaf_key=None if leaf is None else leaf.key, title=None if leaf is None else leaf.title,
            unreadable=record.status != PRESENT, fail_closed=fail_closed, since=since,
            age=_age(since, context.reference),
        )


# The registry, read at call time: a test or a later source appends to it.
SOURCES = (FiresListSource(), P0Source(), StuckSource())


def _merge(p0_fires, stuck_fires):
    """(P0 fires, STUCK fires left): a STUCK fire on an open P0 leaf joins
    that leaf's fire, carrying the marker's time, unreadability and fail-closed
    value."""
    merged = list(p0_fires)
    at = {(f.site, f.leaf_key): i for i, f in enumerate(merged)}
    left = []
    for fire in stuck_fires:
        i = at.get((fire.site, fire.leaf_key)) if fire.leaf_key is not None else None
        if i is None:
            left.append(fire)
            continue
        p = merged[i]
        merged[i] = replace(p, kinds=p.kinds + fire.kinds, why=p.why + fire.why, since=fire.since, age=fire.age,
                            unreadable=fire.unreadable, fail_closed=fire.fail_closed)
    return merged, left


def _newest_first(fires):
    """Stable: fires with an equal time keep their order."""
    return sorted(fires, key=lambda f: f.since, reverse=True)


def order_fires(groups, site_order) -> list:
    """The report's order. `groups` is [(source name, fires)] in registry
    order. Fires-list fires: undated in file order, then dated newest first;
    P0 fires as collected (site, then WBS.md order); STUCK fires newest first,
    ties by site then folder name, then undated by site then folder name;
    any other source's fires as collected."""
    def site_key(fire):
        return site_order.get(fire.site, len(site_order)), fire.issue or ""

    ordered = []
    for name, found in groups:
        if name == "fires_list":
            ordered += [f for f in found if f.since is None]
            ordered += _newest_first(f for f in found if f.since is not None)
        elif name == "stuck":
            by_site = sorted(found, key=site_key)
            ordered += _newest_first(f for f in by_site if f.since is not None)
            ordered += [f for f in by_site if f.since is None]
        else:
            ordered += found
    return ordered


def collect_fires(result, config, fires_list, records, reference) -> FireReport:
    """One build's FireReport. `reference` is fire_reference's aware time.
    A source that raises gets one not_read status and loses its fires; the
    other sources still run."""
    if not _aware(reference):
        raise ValueError("the reference time must be timezone-aware")
    context = FireContext(result, config, fires_list, records, reference)
    collected = [(source.name, _run(source, context)) for source in SOURCES]
    # A list, not a dict keyed by name: a second source with a taken name must not drop the first one's fires.
    groups = [[name, list(got.fires)] for name, got in collected]
    first = {}
    for group in groups:
        first.setdefault(group[0], group)
    if "p0_leaf" in first and "stuck" in first:
        p0_group, stuck_group = first["p0_leaf"], first["stuck"]
        p0_group[1], stuck_group[1] = _merge(p0_group[1], stuck_group[1])
    site_order = {entry.name: i for i, entry in enumerate(result.sites)}
    parked, no_leaf = {}, {}
    for _, got in collected:
        parked.update(got.stuck_parked)
        no_leaf.update(got.stuck_no_open_leaf)
    return FireReport(tuple(order_fires(groups, site_order)), tuple(s for _, got in collected for s in got.statuses),
                      parked, no_leaf)


def _run(source, context) -> SourceResult:
    """The source's result, read whole inside the `try` so a malformed one is
    isolated like a raise; a source that gives no status gets a not_read one
    (fail closed)."""
    try:
        got = source.collect(context)
        got = SourceResult(tuple(got.fires), tuple(got.statuses), dict(got.stuck_parked),
                           dict(got.stuck_no_open_leaf))
    except Exception as e:
        return SourceResult((), (SourceStatus(source.name, None, "not_read", source_failed(e)),))
    if not got.statuses:
        return replace(got, statuses=(SourceStatus(source.name, None, "not_read", "source gave no status"),))
    return got
