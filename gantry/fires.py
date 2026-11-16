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

from gantry import fires_list

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
