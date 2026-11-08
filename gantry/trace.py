"""The trace: `satisfies:` read from parsed leaves and joined, per site, with
the requirements catalog (#19). docs/requirements-format.md states the rules.

A leaf's `satisfies:` is read from the parsed leaf's `meta` through
gantry.model.meta_values, and each entry through gantry.requirements.parse_id
with no default namespace: there is one reading of a meta value and one ID
grammar. This module never reads WBS.md and never imports the stopgap parser,
so #12 changes the accessor, not this module.

load_catalog reads every in-trace site's requirements file and, when
portfolio_file is set, the portfolio file's business requirements, once per
build. build_site_trace returns one SiteTrace per site: each leaf's IDs, the
leaves serving each requirement (direct or indirect), the site's use cases
and its TraceReport. It renders and prints nothing; `SiteTrace` is
provisional until #20 binds it to the page's `trace` data.

Identifiers: every map is keyed by `str(ReqId)`; every sequence of IDs holds
`ReqId`, except TraceReport.unserved, which lists `str(ReqId)`.

Decisions:
- D1 the reader's `undefined_link` entries are carried into `undefined`,
  beside this module's `undefined_cross_link` and `undefined_id`.
- D2 entries that belong to no site stay on the Catalog: the portfolio
  file's own entries (`portfolio_report`) and links from a business
  requirement (`link_report[br_namespace]`).
- D3 `site` is the site's name, its namespace; `leaves` are in WBS order.
- D4 `unserved` and `untraced` are None for a site not in the trace.
- D5 `leaf_ids` holds every leaf, skipped included, naming at least one ID
  that resolves; `serving` keys are in catalog order.
- D6 a repeated entry on one leaf counts once, unreported.
- D7 a cycle within one file is the reader's; this module reports a cycle
  spanning namespaces once, at the member first in catalog order.
- D8 with a `satisfies` key present, every other key equal to a known
  namespace is reported as a probable unbracketed list.

Report entries are gantry.requirements.ReportEntry. An entry about a leaf has
source LEAF_SOURCE and the leaf's line; one about a requirement has its
source and line. Messages name namespaces, IDs and keys, never a path.
"""

from dataclasses import dataclass

from gantry import requirements
from gantry.model import meta_values
from gantry.requirements import ReportEntry, ReqId, parse_id

UNSERVED_SCOPE = "site"  # only this site's leaves are counted; #21 widens it
LEAF_SOURCE = "WBS.md"
TRACE_KINDS = ("malformed_satisfies", "unbracketed_list", "undefined_cross_link", "undefined_id")
FIELDS = {  # TraceReport entry field -> the kinds it holds
    "unreadable": ("unreadable",),
    "malformed": ("malformed_item", "malformed_meta", "malformed_link", "malformed_satisfies", "unbracketed_list"),
    "duplicate": ("duplicate_id",),
    "misplaced": ("misplaced",),
    "cycle": ("refines_cycle",),
    "undefined": ("undefined_link", "undefined_cross_link", "undefined_id"),
}
MESSAGES = {
    "malformed_satisfies": "satisfies entry {entry!r} is not a full requirement ID (<namespace>:<code>-<n>); "
                           "it is ignored",
    "unbracketed_list": "key {key!r} is a namespace: satisfies looks like an unbracketed list, which splits at "
                        "its first comma; write satisfies: [a:X, b:Y]",
    "undefined_id": "satisfies entry {rid} names no requirement: {why}",
    "undefined_cross_link": "{key}: {rid} names no requirement: {why}",
    "refines_cycle": "cross-file refines cycle: {chain}",
}


@dataclass(frozen=True)
class Serving:
    leaf: str  # the leaf's key
    how: str  # "direct" or "indirect"


@dataclass(frozen=True)
class UseCase:
    needs: tuple[ReqId, ...]
    serving: tuple[Serving, ...]


@dataclass(frozen=True)
class TraceReport:
    unreadable: tuple[ReportEntry, ...]
    malformed: tuple[ReportEntry, ...]
    duplicate: tuple[ReportEntry, ...]
    misplaced: tuple[ReportEntry, ...]
    cycle: tuple[ReportEntry, ...]
    undefined: tuple[ReportEntry, ...]
    unserved: tuple[str, ...] | None  # None: the site is not in the trace
    unserved_scope: str
    untraced: tuple[str, ...] | None  # leaf keys; None: the site is not in the trace


@dataclass(frozen=True)
class SiteTrace:
    site: str
    in_trace: bool
    leaf_ids: dict[str, tuple[ReqId, ...]]  # leaf key -> the IDs that resolve
    serving: dict[str, tuple[Serving, ...]]  # str(ReqId) -> serving leaves, in WBS order
    use_cases: dict[str, UseCase]  # str(ReqId) of each own use case
    report: TraceReport


def satisfies_ids(leaf, namespaces) -> tuple[list[ReqId], list[ReportEntry]]:
    """(ids, problems) for a leaf's `satisfies:`; never raises on any text."""
    ids, problems = [], []
    meta = leaf.meta
    for entry in meta_values(meta.get("satisfies")):
        rid = parse_id(entry)
        if rid is None:
            problems.append(ReportEntry(LEAF_SOURCE, leaf.line, "malformed_satisfies",
                                        MESSAGES["malformed_satisfies"].format(entry=entry)))
        elif rid not in ids:
            ids.append(rid)
    if "satisfies" in meta:
        for key in meta:
            if key != "satisfies" and key in namespaces:
                problems.append(ReportEntry(LEAF_SOURCE, leaf.line, "unbracketed_list",
                                            MESSAGES["unbracketed_list"].format(key=key)))
    return ids, problems
