"""Cross-site depends_on entries, resolved in a full build (#68).

resolve_foreign reads every read site's parsed nodes and resolves each foreign
entry (`<site>:<id>`) against the site it names. It is pure over its inputs:
it returns new Inputs, never changes the ones it is given, so build_all can
re-resolve from the same raw read after a render failure. It never touches
links, slack or the schedule; the page draws what it records beside them.
docs/worksite-page.md states the rules.
"""

from dataclasses import replace

from gantry.deps import open_sources, refs
from gantry.links import leaf_href
from gantry.model import ForeignOutcome, ReportEntry, Row, report_order

MESSAGES = {
    "foreign_site_absent": "WBS.md line {line}: depends_on entry {entry!r} names a site not in this installation",
    "foreign_unresolved": "WBS.md line {line}: depends_on entry {entry!r} is unresolved: {reason}",
}
NOT_BUILT = "{site} is not built: {reason}"
NO_ISSUE = "{site}'s WBS.md has no issue: id {issue!r}"
# The outcomes a waiting leaf shows; a satisfied entry needs no box.
WAITING = frozenset({"open", "absent", "unresolved"})


def _holders(data):
    """{issue: node} for each issue's first holder: its key is its issue (#8)."""
    return {node.issue: node for node in (*data.leaves, *data.rows)
            if node.issue is not None and node.key == node.issue}


def _first_open_under(data, row):
    prefix = row.outline + "."
    return next((leaf for leaf in data.leaves if leaf.open and leaf.outline.startswith(prefix)), None)


def _outcome(site, node, ref, names, inputs, reasons, holders):
    """One entry's ForeignOutcome."""
    target = ref.site
    base = dict(site=site, source_key=node.key, source_line=node.line, source_is_row=isinstance(node, Row),
                raw=ref.raw, target_site=target, target_issue=ref.issue,
                target_key=None, target_is_row=None, title=None, href=None, reason=None)
    if target not in names:
        return ForeignOutcome(status="absent", **base)
    if target in reasons or target not in inputs:
        return ForeignOutcome(status="unresolved", **{
            **base, "reason": NOT_BUILT.format(site=target, reason=reasons[target])})
    holder = holders[target].get(ref.issue)
    if holder is None:
        return ForeignOutcome(status="unresolved", **{**base, "reason": NO_ISSUE.format(site=target, issue=ref.issue)})
    is_row = isinstance(holder, Row)
    first = _first_open_under(inputs[target].data, holder) if is_row else (holder if holder.open else None)
    found = {**base, "target_key": holder.key, "target_is_row": is_row}
    if first is None:
        return ForeignOutcome(status="satisfied", **found)
    return ForeignOutcome(status="open", **{
        **found, "title": holder.title, "href": leaf_href(target, first.key, from_top=False)})


def _resolve_site(site, inputs, names, all_inputs, reasons, holders):
    """(the site's resolved Inputs, its outcomes in line order)."""
    data = inputs.data
    nodes = sorted([*data.leaves, *data.rows], key=lambda node: node.line)
    by_node = {}
    report = list(data.report)
    for node in nodes:
        found = []
        for ref in refs(node.meta, site):
            if ref.kind != "foreign":
                continue
            o = _outcome(site, node, ref, names, all_inputs, reasons, holders)
            found.append(o)
            if o.status == "absent":
                report.append(ReportEntry(site, node.line, "foreign_site_absent",
                                          MESSAGES["foreign_site_absent"].format(line=node.line, entry=ref.raw)))
            elif o.status == "unresolved":
                report.append(ReportEntry(site, node.line, "foreign_unresolved", MESSAGES["foreign_unresolved"].format(
                    line=node.line, entry=ref.raw, reason=o.reason)))
        by_node[node.key] = found
    row_at = {row.outline: row for row in data.rows}
    leaves = []
    for leaf in data.leaves:
        if not leaf.open:
            leaves.append(leaf)
            continue
        targets, seen = [], set()
        for source in open_sources(leaf, row_at):
            for o in by_node[source.key]:
                if o.status in WAITING and (o.target_site, o.target_issue) not in seen:
                    seen.add((o.target_site, o.target_issue))
                    targets.append(o)
        leaves.append(replace(leaf, waits_on_other_site=bool(targets), foreign_targets=tuple(targets)))
    report.sort(key=report_order)
    resolved = replace(inputs, data=replace(data, leaves=tuple(leaves), report=tuple(report)))
    return resolved, tuple(o for node in nodes for o in by_node[node.key])


def resolve_foreign(sites, inputs, reasons):
    """(resolved inputs, {waiting site: outcomes}) over every read site.

    `sites` is the config-order site list, `inputs` every read site's raw
    Inputs, `reasons` every not-built site's reason. A target site counts as
    built when it was read and has no reason.
    """
    names = {s.name for s in sites}
    holders = {name: _holders(found.data) for name, found in inputs.items()}
    resolved, foreign = {}, {}
    for name, found in inputs.items():
        resolved[name], foreign[name] = _resolve_site(name, found, names, inputs, reasons, holders)
    order = [s.name for s in sites if s.name in inputs]
    return resolved, {name: foreign[name] for name in order}
