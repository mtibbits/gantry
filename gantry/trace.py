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
and its TraceReport. page_trace binds a SiteTrace to the worksite page's
`trace` data (#20), and counts_line gives the build's `trace <site>:` line
from that data. This module renders and prints nothing.

Identifiers: requirement maps (`serving`, `use_cases`, Catalog.requirements,
Catalog.flows) are keyed by `str(ReqId)`; `leaf_ids` by leaf key; Catalog.sets
and Catalog.link_report by namespace. Every sequence of IDs holds `ReqId`,
except TraceReport.unserved, which lists `str(ReqId)`.

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

from dataclasses import dataclass, replace

from gantry import requirements
from gantry.inputs import scheduled
from gantry.model import meta_values
from gantry.portfolio_file import PORTFOLIO_LABEL, Portfolio, PortfolioEntry, read_portfolio_file
from gantry.requirements import (PROJECT_LAYERS, ReportEntry, ReqId, Requirement, RequirementSet, in_trace_sites,
                                 parse_id, read_requirements)

UNSERVED_SCOPE = "site"  # only this site's leaves are counted; #21 widens it
LEAF_SOURCE = "WBS.md"
TRACE_KINDS = ("malformed_satisfies", "unbracketed_list", "undefined_cross_link", "undefined_id")
FIELDS = {  # TraceReport entry field -> the kinds it holds
    "unreadable": ("unreadable",),
    "malformed": ("malformed_item", "malformed_meta", "malformed_link", "malformed_comment", "malformed_satisfies",
                  "unbracketed_list"),
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

WHY = {  # why an ID names no requirement: the {why} of undefined_id and undefined_cross_link
    "not_a_site": "namespace {ns!r} is not a site of this installation, nor br_namespace",
    "no_file": "no requirements file is configured for namespace {ns!r}",
    "no_portfolio": "portfolio_file is not set, so no business requirement is defined",
    "unreadable": "the requirements file of namespace {ns!r} could not be read",
    "portfolio_unreadable": "the portfolio file could not be read",
    "no_br_section": "the portfolio file has no '## Business requirements' section",
    "not_in_file": "the requirements file of namespace {ns!r} defines no {rid}",
    "not_in_portfolio": "the portfolio file defines no {rid}",
}

# The page's `trace` data and the build's line (#20).
COUNTS_LINE = ("trace {site}: unreadable {unreadable}, malformed {malformed}, duplicate {duplicate}, "
               "misplaced {misplaced}, cycle {cycle}, undefined {undefined}, unserved {unserved}, untraced {untraced}")
COUNT_OPEN, COUNT_HELD, COUNT_FINISHED = "{n} open", "{n} waiting on a cycle", "{n} finished"  # joined with ", "
# A requirements file's entries name it by kind on the page, never by its path.
REQUIREMENTS_LABEL = "the requirements file"
CATEGORIES = (*FIELDS, "unserved", "untraced")


@dataclass(frozen=True)
class Catalog:
    """Every requirement set of the build, read once."""
    br_namespace: str
    namespaces: frozenset[str]  # every site name, plus br_namespace
    sets: dict[str, RequirementSet]  # in-trace sites in config order, then br_namespace when portfolio_file is set
    portfolio_set: bool
    portfolio_report: tuple[PortfolioEntry, ...]  # the portfolio file's entries outside its settings
    link_report: dict[str, tuple[ReportEntry, ...]]  # namespace -> the cross-file entries attributed to it
    requirements: dict[str, Requirement]  # str(ReqId) -> requirement, every set merged, in catalog order
    flows: dict[str, list[str]]  # str(ReqId) -> what serving it also serves: what it refines, use cases needing it
    # The portfolio file as read, whole: the run's one read, kept for the week
    # plan (#61). None when portfolio_file is not set.
    portfolio: Portfolio | None = None

    def lookup(self, rid) -> tuple[Requirement | None, str | None]:
        """(requirement, None), or (None, why) when `rid` names no requirement."""
        ns = rid.namespace
        if ns == self.br_namespace and not self.portfolio_set:
            return None, WHY["no_portfolio"]
        if ns not in self.namespaces:
            return None, WHY["not_a_site"].format(ns=ns)
        rs = self.sets.get(ns)
        if rs is None:
            return None, WHY["no_file"].format(ns=ns)
        found = rs.requirements.get(str(rid))
        if found is not None:
            return found, None
        if ns == self.br_namespace:
            for kind in ("portfolio_unreadable", "no_br_section"):
                if any(e.kind == kind for e in self.portfolio_report):
                    return None, WHY[kind]
            return None, WHY["not_in_portfolio"].format(rid=rid)
        if any(e.kind == "unreadable" for e in rs.report):
            return None, WHY["unreadable"].format(ns=ns)
        return None, WHY["not_in_file"].format(ns=ns, rid=rid)

    def in_trace(self, site) -> bool:
        return site in self.sets and site != self.br_namespace


@dataclass(frozen=True)
class Serving:
    leaf: str  # the leaf's key
    how: str  # "direct" or "indirect"


@dataclass(frozen=True)
class UseCase:
    needs: tuple[ReqId, ...]  # as written: an ID that names no requirement stays, and is reported undefined
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


def load_catalog(roots, config, sites) -> Catalog:
    """Read every in-trace site's requirements file and, when portfolio_file
    is set, the portfolio file, under the run's `roots`: its business
    requirements join the catalog, and the whole read is kept as `portfolio`.
    `sites` is load_sites(config)[0]; in_trace_sites' warnings are
    build.site_list's to print. Never raises on a file's content."""
    br = config.requirements.br_namespace
    traced, _ = in_trace_sites(config, sites)
    sets = {name: read_requirements(roots, path, name, layers=PROJECT_LAYERS, br_namespace=br)
            for name, path in traced.items()}
    portfolio, portfolio_report = None, ()
    if config.portfolio_file is not None:
        portfolio = read_portfolio_file(roots, config.portfolio_file, br_namespace=br)
        sets[br] = portfolio.brs
        portfolio_report = tuple(e for e in portfolio.report if e.section != "settings")
    merged = {key: r for rs in sets.values() for key, r in rs.requirements.items()}
    catalog = Catalog(
        br_namespace=br,
        namespaces=frozenset({site.name for site in sites} | {br}),
        sets=sets,
        portfolio_set=config.portfolio_file is not None,
        portfolio_report=portfolio_report,
        link_report={},
        requirements=merged,
        flows=_flows(merged),
        portfolio=portfolio,
    )
    return replace(catalog, link_report=_links(catalog))


_KIND_ORDER = {kind: i for i, kind in enumerate(requirements.REPORT_KINDS + TRACE_KINDS)}


def _entry_key(e):
    return e.line, _KIND_ORDER[e.kind]


def _links(catalog):
    """{namespace: entries} for the cross-namespace links that name no
    requirement and the refines: cycles that cross files (D7). A link within
    one namespace, and a cycle within one file, are the reader's."""
    found = {}

    def add(r, kind, message):
        ns = r.id.namespace
        source = PORTFOLIO_LABEL if ns == catalog.br_namespace else r.source
        found.setdefault(ns, []).append(ReportEntry(source, r.line, kind, message))

    reqs = catalog.requirements
    order = {key: i for i, key in enumerate(reqs)}
    for r in reqs.values():
        for key, targets in (("refines", [r.refines]), ("needs", r.needs)):
            for t in targets:
                if t is None or t.namespace == r.id.namespace:
                    continue
                _, why = catalog.lookup(t)
                if why is not None:
                    add(r, "undefined_cross_link",
                        MESSAGES["undefined_cross_link"].format(key=key, rid=t, why=why))

    def follow(key):
        t = reqs[key].refines
        return str(t) if t is not None and str(t) in reqs else None

    # Each requirement refines at most one other: walk the paths in catalog
    # order, visiting each requirement once.
    state = {}
    for start in reqs:
        path, node = [], start
        while node is not None and node not in state:
            state[node] = "path"
            path.append(node)
            node = follow(node)
        if node is not None and state[node] == "path":
            cycle = path[path.index(node):]
            if len({reqs[k].id.namespace for k in cycle}) > 1:
                first = cycle.index(min(cycle, key=order.__getitem__))
                cycle = cycle[first:] + cycle[:first]
                add(reqs[cycle[0]], "refines_cycle",
                    MESSAGES["refines_cycle"].format(chain=" -> ".join(cycle + cycle[:1])))
        for key in path:
            state[key] = "done"
    return {ns: tuple(sorted(found[ns], key=_entry_key))
            for ns in catalog.sets if ns in found}


def build_site_trace(site, leaves, catalog) -> SiteTrace:
    """The trace of the site named `site` (its namespace): `leaves` are its
    parsed leaves in WBS order. No entry stops the build."""
    leaves = tuple(leaves)
    in_trace = catalog.in_trace(site)
    leaf_ids, leaf_entries = {}, []
    for leaf in leaves:
        ids, problems = satisfies_ids(leaf, catalog.namespaces)
        leaf_entries.extend(problems)
        resolved = []
        for rid in ids:
            _, why = catalog.lookup(rid)
            if why is None:
                resolved.append(rid)
            else:
                leaf_entries.append(ReportEntry(LEAF_SOURCE, leaf.line, "undefined_id",
                                                MESSAGES["undefined_id"].format(rid=rid, why=why)))
        if resolved:
            leaf_ids[leaf.key] = tuple(resolved)
    serving = _serving(leaves, leaf_ids, catalog)
    if in_trace:
        own = catalog.sets[site]
        own_entries, link_entries = tuple(own.report), catalog.link_report.get(site, ())
        use_cases = {key: UseCase(tuple(r.needs), serving.get(key, ()))
                     for key, r in own.requirements.items() if r.layer == "UC"}
        unserved = tuple(key for key, r in own.requirements.items() if r.layer != "CON" and key not in serving)
        untraced = tuple(leaf.key for leaf in leaves if leaf.open and leaf.key not in leaf_ids)
    else:
        own_entries, link_entries, use_cases, unserved, untraced = (), (), {}, None, None
    leaf_entries.sort(key=_entry_key)
    by_field = {name: tuple(e for group in (own_entries, link_entries, leaf_entries)
                            for e in group if e.kind in kinds)
                for name, kinds in FIELDS.items()}
    report = TraceReport(**by_field, unserved=unserved, unserved_scope=UNSERVED_SCOPE, untraced=untraced)
    return SiteTrace(site, in_trace, leaf_ids, serving, use_cases, report)


def _flows(reqs):
    """{str(X): [str(Y), ...]}: what serving X also serves. X flows to the
    requirement it refines and to each use case that needs it, when the
    target resolves; flows go up, never down."""
    flows = {}
    for key, r in reqs.items():
        if r.refines is not None and str(r.refines) in reqs:
            flows.setdefault(key, []).append(str(r.refines))
        for need in r.needs:
            if str(need) in reqs:
                flows.setdefault(str(need), []).append(key)
    return flows


def _serving(leaves, leaf_ids, catalog):
    """{str(ReqId): serving leaves in WBS order}, keyed in catalog order. A
    skipped leaf never serves; a leaf serves directly what it names, and
    indirectly what that flows to. Each walk visits a requirement once, so a
    cycle cannot loop."""
    found = {}
    for leaf in leaves:
        if leaf.state == "skipped" or leaf.key not in leaf_ids:
            continue
        named = {str(rid) for rid in leaf_ids[leaf.key]}
        seen, stack = set(named), list(named)
        while stack:
            for nxt in catalog.flows.get(stack.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        # Collect the whole walk before appending, so a leaf that reaches a
        # requirement both directly and indirectly serves it once.
        for key in seen:
            found.setdefault(key, []).append(Serving(leaf.key, "direct" if key in named else "indirect"))
    return {key: tuple(found[key]) for key in catalog.requirements if key in found}


# --- the page's `trace` data (#20) ----------------------------------------------


def _label(rid, site):
    """The ID as a page shows it: without its namespace when that is the site's."""
    return f"{rid.code}-{rid.number}" if rid.namespace == site else str(rid)


def _source(entry):
    """An entry's source as the page shows it: a leaf's and the portfolio
    file's as they are, a requirements file's by kind, never its path."""
    return entry.source if entry.source in (LEAF_SOURCE, PORTFOLIO_LABEL) else REQUIREMENTS_LABEL


def page_trace(trace: SiteTrace, catalog: Catalog, data) -> dict:
    """The worksite page's `trace` data for one site, from its SiteTrace and
    its SiteData (`data`, the leaves the page draws). Records are arrays,
    never objects keyed by a record value. docs/worksite-page.md states it."""
    site, reqs = trace.site, catalog.requirements
    wanted = dict.fromkeys(catalog.sets[site].requirements if trace.in_trace else ())
    for ids in trace.leaf_ids.values():
        wanted.update(dict.fromkeys(str(rid) for rid in ids))
    order = {key: i for i, key in enumerate(reqs)}
    rank = {layer: i for i, layer in enumerate(requirements.LAYERS)}
    keys = sorted(wanted, key=lambda key: (rank[reqs[key].layer], order[key]))

    work = scheduled(data)
    drawn = {leaf.key for leaf in work}
    held_out = set(data.held_out)
    by_key = {leaf.key: leaf for leaf in data.leaves}
    rows = []
    for key in keys:
        r = reqs[key]
        light, held, finished = [], [], []
        for s in trace.serving.get(key, ()):
            leaf = by_key[s.leaf]
            if s.leaf in drawn:
                light.append([s.leaf, s.how])
            else:
                (held if leaf.open and s.leaf in held_out else finished).append([s.leaf, leaf.issue, leaf.title, s.how])
        counts = [COUNT_OPEN.format(n=len(light))]
        counts += [COUNT_HELD.format(n=len(held))] if held else []
        counts += [COUNT_FINISHED.format(n=len(finished))] if finished else []
        rows.append({
            "id": key, "label": _label(r.id, site), "layer": r.layer,
            # A business requirement's text is never on a worksite page (#23).
            "text": None if r.id.namespace == catalog.br_namespace else r.text,
            "needs": [str(n) for n in r.needs if str(n) in wanted and str(n) != key] if r.layer == "UC" else [],
            "light": light, "held": held, "finished": finished, "counts": ", ".join(counts),
        })
    report = [[name, [[_source(e), e.line, e.message] for e in getattr(trace.report, name)]] for name in FIELDS]
    if trace.in_trace:
        report.append(["unserved", [[key, _label(reqs[key].id, site)] for key in trace.report.unserved]])
        report.append(["untraced", [[key, by_key[key].title] for key in trace.report.untraced]])
    return {
        "in_trace": trace.in_trace,
        "scope": trace.report.unserved_scope,
        "requirements": rows,
        "leaves": [[leaf.key, [[str(rid), _label(rid, site)] for rid in trace.leaf_ids[leaf.key]]]
                   for leaf in work if leaf.key in trace.leaf_ids],
        "report": report,
    }


def counts_line(site: str, page: dict) -> str | None:
    """The build's `trace <site>:` line from the page's `trace` data, every
    category in CATEGORIES order and a left-out one as 0; None for a site
    not in the trace whose report has no entry."""
    counts = dict.fromkeys(CATEGORIES, 0)
    for name, items in page["report"]:
        counts[name] = len(items)
    if not page["in_trace"] and not any(counts.values()):
        return None
    return COUNTS_LINE.format(site=site, **counts)
