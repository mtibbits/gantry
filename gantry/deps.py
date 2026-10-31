"""depends_on entries: classification, resolution and cycles (#8).

An entry with no `:` names an issue: id in this site's WBS.md. `<site>:<id>`,
both halves id tokens, names this site when `<site>` is exactly its name and
another site otherwise. Every other form is malformed.
"""

from gantry.model import FINISHED_GLYPHS, DepRef, ReportEntry, Row, meta_values
from gantry.sites import ID_TOKEN_RE


def classify(entry: str, site: str) -> DepRef:
    if ":" not in entry:
        return DepRef(entry, "local", site, entry)
    prefix, _, suffix = entry.partition(":")
    if ID_TOKEN_RE.fullmatch(prefix) and ID_TOKEN_RE.fullmatch(suffix):
        if prefix == site:
            return DepRef(entry, "local", site, suffix)
        return DepRef(entry, "foreign", prefix, suffix)
    return DepRef(entry, "malformed", None, None)


def refs(meta, site) -> tuple[DepRef, ...]:
    """A node's depends_on entries, classified."""
    return tuple(classify(e, site) for e in meta_values(meta.get("depends_on")))


MESSAGES = {
    "unresolved_ref": "WBS.md line {line}: depends_on entry {entry!r} names no issue: id in this file",
    "malformed_ref": "WBS.md line {line}: depends_on entry {entry!r} is not an id or <site>:<id>",
    "cycle": "WBS.md line {line}: dependency cycle among {keys}",
    "waits_on_cycle": "WBS.md line {line}: {key!r} is held out: it is in, or waits on, a dependency cycle",
}


def _inside(node, row):
    return node.outline.startswith(row.outline + ".")


def _report(report, site, line, kind, **fields):
    report.append(ReportEntry(site, line, kind, MESSAGES[kind].format(line=line, **fields)))


def _targets(site, node, by_issue, report):
    """Report a source's bad entries; return the keys its local entries name."""
    out = []
    for ref in refs(node.meta, site):
        if ref.kind == "malformed":
            _report(report, site, node.line, "malformed_ref", entry=ref.raw)
        elif ref.kind == "local":
            if ref.issue in by_issue:
                out.append(by_issue[ref.issue])
            else:
                _report(report, site, node.line, "unresolved_ref", entry=ref.raw)
    return out


def find_cycles(keys, links) -> list[list[str]]:
    """The dependency cycles among `keys`: each strongly connected component of
    two or more, members in `keys` order. Iterative Tarjan: no recursion."""
    index = {k: i for i, k in enumerate(keys)}
    succ = [[] for _ in keys]
    for a, b in links:
        if a in index and b in index:
            succ[index[a]].append(index[b])
    n = len(keys)
    order, low, on_stack = [-1] * n, [0] * n, [False] * n
    stack, found, counter = [], [], 0
    for start in range(n):
        if order[start] != -1:
            continue
        order[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack[start] = True
        work = [(start, 0)]
        while work:
            v, i = work[-1]
            if i < len(succ[v]):
                work[-1] = (v, i + 1)
                w = succ[v][i]
                if order[w] == -1:
                    order[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack[w] = True
                    work.append((w, 0))
                elif on_stack[w]:
                    low[v] = min(low[v], order[w])
                continue
            work.pop()
            if work:
                u = work[-1][0]
                low[u] = min(low[u], low[v])
            if low[v] == order[v]:
                component = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    component.append(w)
                    if w == v:
                        break
                if len(component) >= 2:
                    found.append(sorted(component))
    found.sort()
    return [[keys[i] for i in component] for component in found]


def resolve(site, leaves, rows, by_issue):
    """(links, held_out, report entries) for one site's parsed nodes.

    Every node's entries are checked, open or finished; links, cycles and
    held-out keys are over open leaves only. An open leaf takes its own
    entries and those of its open ancestor rows; an entry naming a row means
    the open leaves under it.
    """
    report = []
    nodes = sorted([*leaves, *rows], key=lambda node: node.line)
    by_key = {node.key: node for node in nodes}
    row_at = {row.outline: row for row in rows}
    named = {node.key: _targets(site, node, by_issue, report) for node in nodes}

    def ancestors(leaf):
        parts = leaf.outline.split(".")
        return [row_at[".".join(parts[:i])] for i in range(1, len(parts))]

    open_leaves = [leaf for leaf in leaves if leaf.open]
    lineages = {leaf.key: ancestors(leaf) for leaf in open_leaves}
    open_under = {row.key: [] for row in rows}
    for leaf in open_leaves:
        for row in lineages[leaf.key]:
            open_under[row.key].append(leaf.key)

    links = set()
    for leaf in open_leaves:
        lineage = lineages[leaf.key]
        above = {row.key for row in lineage}
        sources = [leaf] + [row for row in lineage if row.glyph not in FINISHED_GLYPHS]
        for source in sources:
            for key in named[source.key]:
                target = by_key[key]
                if key == leaf.key or key in above:
                    continue
                if isinstance(source, Row) and _inside(target, source):
                    continue
                if isinstance(target, Row):
                    links.update((t, leaf.key) for t in open_under[key] if t != leaf.key)
                elif target.open:
                    links.add((key, leaf.key))
    links = tuple(sorted(links, key=lambda link: (by_key[link[1]].line, by_key[link[0]].line)))

    keys = [leaf.key for leaf in open_leaves]
    cycles = find_cycles(keys, links)
    dependents = {}
    for a, b in links:
        dependents.setdefault(a, []).append(b)
    queue = [k for cycle in cycles for k in cycle]
    held = set(queue)
    while queue:
        for d in dependents.get(queue.pop(), ()):
            if d not in held:
                held.add(d)
                queue.append(d)
    held_out = tuple(k for k in keys if k in held)
    for cycle in cycles:
        _report(report, site, by_key[cycle[0]].line, "cycle", keys=", ".join(repr(k) for k in cycle))
    for key in held_out:
        _report(report, site, by_key[key].line, "waits_on_cycle", key=key)
    return links, held_out, report
