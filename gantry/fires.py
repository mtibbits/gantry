"""The fire collector (#32): one ordered FireReport per full build, across
every site. docs/daily-screen.md states the rules.

A pure collector: it reads only what build_all hands it (the BuildResult,
the config, the fires list as the run read it, {site: SiteRecords} and one
aware reference time) and never opens a file or reads the clock. Each
source in the ordered registry SOURCES returns fires and a read status for
what it read; collect_fires isolates a source that fails, merges an open P0
leaf with its STUCK fire, orders the fires and builds the report, so
"nothing on fire" (FireReport.confirmed_none) is never said by accident.

Callers: the daily screen (#34) shows the report; the time thieves (#29) and
the warm-up list (#33) read the same build's records. FireSource,
FireContext and SourceResult are provisional until a second source outside
this module binds to them. Record and fires-list text is kept verbatim:
escaping happens once, where a page renders it.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from gantry import fires_list, priority
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
    site: str | None  # a configured site name
    site_as_written: str | None  # a fires-list @name that is not a site
    issue: str | None  # the leaf's issue: or the folder name
    leaf_key: str | None
    title: str | None
    why: tuple[str, ...]  # one per kind, parallel to kinds
    unreadable: bool
    fail_closed: str | None  # None, "leaf_unknown" or "parked_unknown"
    since: datetime | None  # aware; in the reference's zone when read from a naive time
    date_only: bool
    age: timedelta | None  # never below 0; None exactly when since is None
    still_a_fire: bool | None  # None unless "fires_list" is in kinds
    this_machine: bool  # true exactly when "stuck" is in kinds: STUCK markers are this machine's


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
    here = now.astimezone(local) if local is not None else now.astimezone()
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

# A WBS.md line the parser did not read as a leaf: a mistyped P0 may hide there (fail closed).
UNREAD_LINE_KINDS = ("bad_priority", "skipped_line")


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


def source_failed(e: Exception) -> str:
    return f"source failed: {type(e).__name__}"


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
                site=entry.site if entry.site_known else None,
                site_as_written=None if entry.site_known else entry.site,
                issue=None, leaf_key=None, title=None,
                why=(entry.raw if unreadable else entry.reason,),
                unreadable=unreadable, fail_closed=None,
                since=_in_zone(fires_list.entry_since(entry), reference),
                date_only=entry.date is not None and entry.time is None,
                age=fires_list.entry_age(entry, naive),
                still_a_fire=fires_list.still_a_fire(entry, naive, context.config.fires_check_days),
                this_machine=False,
            ))
        reason = listed.reason
        if listed.status == "read_with_problems":
            reason = f"fires list report entries: {len(listed.report)}"
        return SourceResult(tuple(found), (SourceStatus(self.name, None, listed.status, reason),))


def is_open(leaf) -> bool:
    return leaf.state in OPEN_STATES


class P0Source(PerSiteSource):
    """Every open leaf whose own priority is P0 by gantry.priority's rule."""

    name = "p0_leaf"

    def collect_site(self, context, entry, view) -> SourceResult:
        found = tuple(Fire(
            kinds=(self.name,), site=view.name, site_as_written=None, issue=leaf.issue, leaf_key=leaf.key,
            title=leaf.title, why=("P0 leaf",), unreadable=False, fail_closed=None, since=None, date_only=False,
            age=None, still_a_fire=None, this_machine=False,
        ) for leaf in view.leaves if priority.rank(leaf.priority) == 0 and is_open(leaf))
        if view.wbs == "read" and view.unread_lines:
            status = self.status(context, view.name, "read_with_problems",
                                 f"WBS.md lines not read as leaves: {view.unread_lines}")
        else:
            status = self.status(context, view.name, view.wbs, view.reason)
        return SourceResult(found, (status,))
