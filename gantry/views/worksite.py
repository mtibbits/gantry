"""The worksite page for one site (#9).

page_model turns a site's parsed WBS.md into the page's data: plain lists,
dicts, strings and numbers, ready for JSON. Records are arrays, never objects
keyed by a record value, so no record value can become a key the page's
script looks up on an object. render_page draws the network and fills the
template. docs/worksite-page.md describes the page.
"""

import math

from gantry import network, priority, schedule
from gantry.deps import refs
from gantry.model import FINISHED_GLYPHS, meta_scalar
from gantry.render import MODEL_CLASSES, model_class, slack_class, state_class

LANES_DEFAULT = 4
LANES_MAX = 8
HORIZON_DAYS = 10
HOURS_PER_DAY = 6

SCHEDULE_LABEL = "agent capacity only; ignores operator attention; the week view is the plan"
STUCK_LINE = "{n} stuck, blocked or parked leaves are scheduled as if an agent could start them"
STUCK_LINE_ONE = "1 stuck, blocked or parked leaf is scheduled as if an agent could start it"
INTERIM_NOTE = "every duration is the default unit; no estimates are read yet"
NO_TIERS = "no per-leaf model tiers recorded"
NO_LEAVES = "This site has no leaves."
NO_OPEN = "This site has no open leaves."
NOTHING_MISSING = "Nothing is missing or unreadable."
NO_ISSUE_LINE = "{n} leaves have no issue: id and are named by outline position"
NO_ISSUE_LINE_ONE = "1 leaf has no issue: id and is named by outline position"
WAITS_LINE = "WBS.md line {line}: {key!r} waits on a cycle"
FOREIGN = "{raw} (on another site)"
CHAIN_LINE = "With {lanes}, the dependency chain sets the finish."
CAPACITY_LINE = "With {lanes}, capacity sets the finish."
STATE_MARKS = {"in_progress": "in progress", "stuck": "stuck", "blocked_external": "blocked", "parked": "parked"}
NO_EPIC = "no epic"
# The states a page schedules as if an agent could start them.
HELD_STATES = ("stuck", "blocked_external", "parked")


# --- the optional inputs (#10 fills them; until then each is None) ---------------


def _number(x):
    return not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x)


def _hours(leaf, unit):
    """(hours, unestimated): an estimate's midpoint, else the default unit."""
    est = leaf.estimate
    lo, hi = getattr(est, "lo_h", None), getattr(est, "hi_h", None)
    if est is not None and _number(lo) and _number(hi) and 0 < lo <= hi:
        return (lo + hi) / 2, False
    return unit, True


def _tiers(leaf):
    """[thinking, checking], or None when the leaf records neither."""
    found = [getattr(leaf.models, side, None) for side in ("thinking", "checking")]
    found = [t if isinstance(t, str) and t else None for t in found]
    return found if any(found) else None


def _flags(leaf):
    flags = leaf.flags
    if flags is None:
        return []
    if isinstance(flags, str):
        return [flags]
    return [f for f in flags if isinstance(f, str)]


# --- the model -------------------------------------------------------------------


def _count_line(n, many, one):
    return one if n == 1 else many.format(n=n)


def _panel(data):
    """The report as lines: issue-less leaves as one count, held-out leaves by name."""
    held_at = {leaf.line: leaf.key for leaf in data.leaves if leaf.key in data.held_out}
    no_issue = sum(e.kind == "no_issue" for e in data.report)
    lines, counted = [], False
    for entry in data.report:
        if entry.kind == "no_issue":
            if not counted:
                lines.append(_count_line(no_issue, NO_ISSUE_LINE, NO_ISSUE_LINE_ONE))
                counted = True
        elif entry.kind == "waits_on_cycle":
            lines.append(WAITS_LINE.format(line=entry.line, key=held_at[entry.line]))
        else:
            lines.append(entry.message)
    return lines


def _foreign(site, leaf, row_at):
    """The leaf's foreign entries, then each open ancestor row's, top-down."""
    out = [FOREIGN.format(raw=d.raw) for d in leaf.deps if d.kind == "foreign"]
    parts = leaf.outline.split(".")
    for i in range(1, len(parts)):
        row = row_at[".".join(parts[:i])]
        if row.glyph not in FINISHED_GLYPHS:
            out += [FOREIGN.format(raw=d.raw) for d in refs(row.meta, site) if d.kind == "foreign"]
    return out


def _tier_counts(tiers):
    """[[tier, count], ...]: the known tiers in order, then the rest as first seen."""
    counts = {}
    for t in tiers:
        if t is not None:
            counts[t] = counts.get(t, 0) + 1
    known = [t for t in MODEL_CLASSES if t in counts]
    return [[t, counts[t]] for t in known + [t for t in counts if t not in MODEL_CLASSES]]


def page_model(site, data, *, default_unit_hours) -> dict:
    """The page's data for one site's parsed WBS.md."""
    work = [leaf for leaf in data.leaves if leaf.open and leaf.key not in data.held_out]
    keys = [leaf.key for leaf in work]
    keyset = set(keys)
    links = [(a, b) for a, b in data.links if a in keyset and b in keyset]
    eff = priority.effective(work, links)
    net = network.build(keys, links, {leaf.key: leaf.epic for leaf in work})
    hours = {leaf.key: _hours(leaf, default_unit_hours) for leaf in work}
    wbs_index = {leaf.key: i for i, leaf in enumerate(data.leaves)}

    rows = {row.key: row for row in data.rows}
    epics = list(dict.fromkeys(row.key for row in data.rows if row.parent is None))
    used = {leaf.epic for leaf in work}
    lane_keys = [k for k in epics if k in used] + ([None] if None in used else [])
    lane_of = {k: i for i, k in enumerate(lane_keys)}

    preds = {k: [] for k in keys}
    for a, b in links:
        preds[b].append(a)
    items = [{"key": k, "hours": hours[k][0], "preds": preds[k], "eff_priority": eff[k],
              "wbs_index": wbs_index[k]} for k in keys]
    starts = {k: v[0] for k, v in schedule.plan(items, LANES_DEFAULT).items()}

    net_preds = {k: [] for k in net.nodes}
    net_succs = {k: [] for k in net.nodes}
    for u, v in net.edges:
        net_preds[v].append(u)
        net_succs[u].append(v)
    row_at = {row.outline: row for row in data.rows}
    leaves = []
    for leaf in work:
        k = leaf.key
        linked = k in net.slack
        tiers = _tiers(leaf)
        wave_full = linked and (net.slack[k] == 0 or meta_scalar(leaf.priority) == "P1"
                                or leaf.state == "in_progress"
                                or starts[k] < HORIZON_DAYS * HOURS_PER_DAY)
        leaves.append({
            "key": k, "issue": leaf.issue, "outline": leaf.outline, "title": leaf.title,
            "lane": lane_of[leaf.epic], "state": leaf.state, "sc": state_class(leaf.state),
            "mark": STATE_MARKS.get(leaf.state), "priority": leaf.priority, "eff": eff[k],
            "hours": hours[k][0], "unestimated": hours[k][1], "linked": linked,
            "slack": net.slack[k] if linked else None,
            "sclass": slack_class(net.slack[k]) if linked else None,
            "es": net.es[k] if linked else None, "crit": k in net.critical, "wave_full": wave_full,
            "models": tiers, "mc": [model_class(t) for t in tiers] if tiers else None,
            "flags": _flags(leaf), "meta": [[mk, mv] for mk, mv in leaf.meta.items()],
            "foreign": _foreign(site, leaf, row_at),
            "preds": net_preds.get(k, []), "succs": net_succs.get(k, []),
        })

    lanes = []
    for key in lane_keys:
        mine = [leaf for leaf in leaves if leaf["lane"] == lane_of[key]]
        row = rows.get(key)
        tiers = [leaf["models"] or [None, None] for leaf in mine]
        lanes.append({
            "key": key,
            "name": row.title if row is not None else NO_EPIC,
            "tag": row.issue if row is not None and row.issue is not None else "",
            "open": len(mine), "unlinked": sum(not leaf["linked"] for leaf in mine),
            "hours": sum(leaf["hours"] for leaf in mine),
            "models": {"think": _tier_counts(t[0] for t in tiers), "check": _tier_counts(t[1] for t in tiers)},
        })

    chain, chain_total = network.heaviest_chain(net, {k: hours[k][0] for k in net.nodes})
    near = {}
    for k in net.nodes:
        if 0 < net.slack[k] <= 3:
            near.setdefault(net.slack[k], []).append(k)
    held = sum(leaf.state in HELD_STATES for leaf in work)
    return {
        "site": site,
        "empty": not data.leaves and not data.rows,
        "lanes": lanes,
        "lanes_default": LANES_DEFAULT,
        "lanes_max": LANES_MAX,
        "leaves": leaves,
        "schedule": {"items": items},
        "network": {
            "nodes": list(net.nodes),
            "edges": [{"u": u, "v": v, "cross": (u, v) in net.cross, "crit": (u, v) in net.crit_edges}
                      for u, v in net.edges],
            "length": net.length,
            "critical": list(net.critical),
            "near": [[s, near[s]] for s in sorted(near)],
            "heaviest": {"keys": list(chain), "hours": chain_total, "same": chain == net.critical},
        },
        "panel": _panel(data),
        "notes": {
            "interim": INTERIM_NOTE if all(hours[k][1] for k in keys) else None,
            "no_tiers": None if any(leaf["models"] for leaf in leaves) else NO_TIERS,
            "stuck": _count_line(held, STUCK_LINE, STUCK_LINE_ONE) if held else None,
        },
        "texts": {"label": SCHEDULE_LABEL, "chain": CHAIN_LINE, "capacity": CAPACITY_LINE},
    }
