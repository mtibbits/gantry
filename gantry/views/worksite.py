"""The worksite page for one site (#9).

page_model turns a site's parsed WBS.md into the page's data: plain lists,
dicts, strings and numbers, ready for JSON. Records are arrays, never objects
keyed by a record value, so no record value can become a key the page's
script looks up on an object. render_page draws the network and fills the
template. docs/worksite-page.md describes the page.
"""

import math
import re

from gantry import network, priority, render, schedule
from gantry.deps import open_sources, refs
from gantry.est_rules import classify_est
from gantry.inputs import NO_TIERS, UNEST_WHAT, has_tiers, scheduled, site_totals
from gantry.links import leaf_anchor
from gantry.model import HOURS_PER_DAY, Estimate, Models
from gantry.render import MODEL_CLASSES, Markup, embed_json, esc, fill, model_class, slack_class, state_class

LANES_DEFAULT = 4
LANES_MAX = 8
HORIZON_DAYS = 10

SCHEDULE_LABEL = "agent capacity only; ignores operator attention; the week view is the plan"
STUCK_LINE = "{n} stuck, blocked or parked leaves are scheduled as if an agent could start them"
STUCK_LINE_ONE = "1 stuck, blocked or parked leaf is scheduled as if an agent could start it"
INCLUDES = "includes {n} unestimated leaves"
INCLUDES_ONE = "includes 1 unestimated leaf"
DATES = "dates include {n} unestimated leaves"
DATES_ONE = "dates include 1 unestimated leaf"
ESTIMATES_LINE = "Estimates read from {name}, dated {date}."
ESTIMATES_UNDATED = "Estimates read from {name} (no date)."
NON_TIME_LINE = "Not in any hour figure: {parts}"
UNESTIMATED_LINE = "{n} leaves with {what} and no estimates-file hours are unestimated: {keys}"
UNESTIMATED_LINE_ONE = "1 leaf with {what} and no estimates-file hours is unestimated: {keys}"
SUMMARY = ("With {lanes}, all open work ends around working day {end} and the critical chain around day {crit}. "
           "The chain alone needs {chain} days; the work spread evenly across the lanes needs {even}. "
           "The lanes are busy {busy}% of the time.")
SUMMARY_NO_CHAIN = ("With {lanes}, all open work ends around working day {end}. No open leaf waits on another, "
                    "so there is no critical chain; the work spread evenly across the lanes needs {even}. "
                    "The lanes are busy {busy}% of the time.")
SUMMARY_UNEST = "includes {n} unestimated leaves ({m} on the critical chain)"
SUMMARY_UNEST_ONE = "includes 1 unestimated leaf ({m} on the critical chain)"
LIMITS_CAPTION = ("At {hpd} agent-hours a day, the critical chain is {a} to {b} working days and all open work is "
                  "{c} to {d}. With one operator, the total sets the finish date. The chain decides order: its "
                  "first leaf has to start early enough that the chain is not the last thing left.")
LIMITS_CAPTION_NO_CHAIN = ("At {hpd} agent-hours a day, all open work is {c} to {d} working days. No open leaf "
                           "waits on another, so there is no critical chain, and with one operator the total sets "
                           "the finish date.")
NO_CHAIN = "none: no open leaf waits on another"
P0_NOTE = ("devagent's wbs order does not know P0 yet (mtibbits/devagent#889), so it ranks these leaves "
           "differently: ")
NO_LEAVES = "This site has no leaves."
NO_OPEN = "This site has no open leaves."
ALL_HELD = "Every open leaf waits on a dependency cycle, so nothing can be scheduled; the panel lists them."
NOTHING_MISSING = "Nothing is missing or unreadable."
NO_ISSUE_LINE = "{n} leaves have no issue: id and are named by outline position"
NO_ISSUE_LINE_ONE = "1 leaf has no issue: id and is named by outline position"
WAITS_LINE = "WBS.md line {line}: {key!r} waits on a cycle"
FOREIGN = "{raw} (on another site)"
CHAIN_LINE = "With {lanes}, the dependency chain sets the finish."
CAPACITY_LINE = "With {lanes}, capacity sets the finish."
STATE_MARKS = {"in_progress": "in progress", "stuck": "stuck", "blocked_external": "blocked", "parked": "parked"}
NO_EPIC = "no epic"
FINISHED_HEADING = "Finished leaves"
NO_ISSUE_ID = "no issue id"
NO_FINISHED = "No leaf is finished yet."
# The states a page schedules as if an agent could start them.
HELD_STATES = ("stuck", "blocked_external", "parked")
# The trace (#20): chips, the requirements panel and the trace report.
CHIPS_MAX = 3
CHIP_MORE = "+{n}"
REQUIREMENTS_HEADING = "Requirements"
SELECT_NOTE = ("Select a requirement, or a chip under a box, to mark the leaves that serve it: a solid outline "
               "serves it directly, a dashed one through refinement or a use case's needs. Select it again, or "
               "press Escape, to clear.")
LAYER_HEADINGS = {"BR": "Business requirements", "BRule": "Business rules", "UC": "Use cases",
                  "SR": "System requirements", "FR": "Functional requirements", "QA": "Quality attributes",
                  "EI": "External interfaces", "CON": "Constraints"}
ROW_DETAIL = "serving leaves not drawn"
ROW_HELD, ROW_FINISHED = "waiting on a cycle", "finished"
TRACE_HEADING = "Trace report"
TRACE_LABELS = {"unreadable": "Unreadable files", "malformed": "Malformed entries", "duplicate": "Duplicate IDs",
                "misplaced": "Misplaced requirements", "cycle": "Refinement cycles", "undefined": "Undefined IDs",
                "unserved": "Unserved requirements", "untraced": "Untraced open leaves"}
UNSERVED_SCOPES = {"site": "served only within this site"}
UNSERVED_SCOPE_OTHER = "scope: {scope}"  # a scope this Gantry does not know yet (#21)
TRACE_COUNT = "{label}: {n}"
TRACE_NONE = "none"
ENTRY_LINE, ENTRY_WHOLE = "{source} line {line}: {message}", "{source}: {message}"
# The page script's selection texts (#20), through the page's texts.
SATISFIES = "Satisfies:"
LIT = "{n} lit"


# --- the optional inputs (gantry/inputs.py fills them, #10) -----------------------


def _span(leaf, unit):
    """(lo, hi, unestimated): the leaf's Estimate, else the default unit.

    apply_inputs is the one validator of a range; any container but an
    Estimate is a caller's bug.
    """
    est = leaf.estimate
    if est is None:
        return unit, unit, True
    if not isinstance(est, Estimate):
        raise TypeError(f"a leaf's estimate is a {type(est).__name__}, not an Estimate")
    return est.lo_h, est.hi_h, est.source == "default"


def _tiers(leaf):
    """[thinking, checking], or None when the leaf records neither."""
    models = leaf.models
    if models is None:
        return None
    if not isinstance(models, Models):
        raise TypeError(f"a leaf's models is a {type(models).__name__}, not Models")
    found = [t if isinstance(t, str) and t else None for t in (models.thinking, models.checking)]
    return found if any(found) else None


def _non_time(leaf):
    """The leaf's non-time est: as text ("2.5 pr"), or None."""
    value = classify_est(leaf.meta.get("est"))
    return f"{value.number:g} {value.unit}" if value.kind == "non_time" else None


def _prio_mark(leaf):
    """The schedule's marker class for the leaf's own priority as written."""
    own = priority.rank(leaf.priority)
    return "pr-p0" if own == 0 else "pr-hi" if own in (1, 2) else None


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


def _includes(n):
    return _count_line(n, INCLUDES, INCLUDES_ONE)


def _lanes_text(k):
    return "1 lane" if k == 1 else f"{k} lanes"


def _round(x):
    """Half up, as the page script's Math.round; round() is half to even (U6)."""
    return math.floor(x + 0.5)


def plan_finish_day(items, p):
    """The working day the plan `p` ends in, counting from 1: its latest finish
    over HOURS_PER_DAY, rounded up, so 8 hours at 6 a day ends in day 2; or
    None with no items. The index dates a finish by it (#11)."""
    if not items:
        return None
    end = 0
    for item in items:
        end = max(end, p[item["key"]][1] / HOURS_PER_DAY)
    # round() first, so a float a hair over a whole day is not a day later.
    return max(1, math.ceil(round(end, 9)))


def summary_text(items, p, path, lanes, unest_site, unest_path):
    """The schedule summary for plan `p`: worksite.js's compute(), step for step.

    Plain loops, not sum(): Python's float sum is compensated, the script's is
    not, and the two must round the same values.
    """
    if not items:
        return ""
    on_path = set(path)
    end = busy = chain = crit = 0
    for item in items:
        start, finish, _ = p[item["key"]]
        a, b = start / HOURS_PER_DAY, finish / HOURS_PER_DAY
        end = max(end, b)
        busy += b - a
        if item["key"] in on_path:
            chain += b - a
            crit = max(crit, b)
    figures = {"lanes": _lanes_text(lanes), "end": _round(end), "even": _round(busy / lanes),
               "busy": _round(busy / (lanes * end) * 100)}
    if not on_path:
        # No chain: no chain figures (the script's same branch).
        text = SUMMARY_NO_CHAIN.format(**figures)
        return text + (f" The plan {_includes(unest_site)}." if unest_site else "")
    text = SUMMARY.format(crit=_round(crit), chain=_round(chain), **figures)
    if unest_site:
        form = SUMMARY_UNEST_ONE if unest_site == 1 else SUMMARY_UNEST
        text += " The plan " + form.format(n=unest_site, m=unest_path) + "."
    return text


def _panel(data):
    """The report as lines: issue-less leaves as one count, held-out leaves by
    name, unestimated leaves as one line per est: class."""
    held_at = {leaf.line: leaf.key for leaf in data.leaves if leaf.key in data.held_out}
    no_issue = sum(e.kind == "no_issue" for e in data.report)
    # By the leaf's own est: class, never by parsing the message.
    leaf_at = {leaf.line: leaf for leaf in data.leaves}
    unestimated = {}
    for entry in data.report:
        if entry.kind == "unestimated":
            leaf = leaf_at[entry.line]
            unestimated.setdefault(leaf.estimate.est_kind, []).append(leaf.key)
    lines, counted, grouped = [], False, set()
    for entry in data.report:
        if entry.kind == "no_issue":
            if not counted:
                lines.append(_count_line(no_issue, NO_ISSUE_LINE, NO_ISSUE_LINE_ONE))
                counted = True
        elif entry.kind == "unestimated":
            kind = leaf_at[entry.line].estimate.est_kind
            if kind not in grouped:
                keys = unestimated[kind]
                text = UNESTIMATED_LINE_ONE if len(keys) == 1 else UNESTIMATED_LINE
                lines.append(text.format(n=len(keys), what=UNEST_WHAT[kind], keys=", ".join(keys)))
                grouped.add(kind)
        elif entry.kind == "waits_on_cycle":
            lines.append(WAITS_LINE.format(line=entry.line, key=held_at[entry.line]))
        else:
            lines.append(entry.message)
    return lines


def _foreign(site, leaf, row_at):
    """The leaf's foreign entries, then each open ancestor row's, top-down."""
    return [FOREIGN.format(raw=d.raw) for source in open_sources(leaf, row_at)
            for d in refs(source.meta, site) if d.kind == "foreign"]


def _known_first(tiers):
    """The known tiers in MODEL_CLASSES order, then the rest as given."""
    rank = list(MODEL_CLASSES)
    return sorted(tiers, key=lambda t: rank.index(t) if t in MODEL_CLASSES else len(rank))


def _tier_counts(tiers):
    """[[tier, count], ...]: the known tiers in order, then the rest as first seen."""
    counts = {}
    for t in tiers:
        if t is not None:
            counts[t] = counts.get(t, 0) + 1
    return [[t, counts[t]] for t in _known_first(counts)]


def page_model(site, data, *, default_unit_hours, inputs=None, trace=None) -> dict:
    """The page's data for one site's parsed WBS.md.

    `inputs` is apply_inputs's result for the site; without it every leaf is
    on the default unit and no estimates file is named. `trace` is
    gantry.trace.page_trace's result (#20); None, as in a direct test
    render, draws no trace.
    """
    if inputs is not None:
        data = inputs.data
    source = inputs.estimates if inputs is not None else None
    work = scheduled(data)
    keys = [leaf.key for leaf in work]
    keyset = set(keys)
    links = [(a, b) for a, b in data.links if a in keyset and b in keyset]
    eff = priority.effective(work, links)
    net = network.build(keys, links, {leaf.key: leaf.epic for leaf in work})
    spans = {leaf.key: _span(leaf, default_unit_hours) for leaf in work}
    hours = {k: ((lo + hi) / 2, unest) for k, (lo, hi, unest) in spans.items()}
    totals = inputs.totals if inputs is not None else site_totals(work, default_unit_hours)
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
    planned = schedule.plan(items, LANES_DEFAULT)
    starts = {k: v[0] for k, v in planned.items()}

    row_at = {row.outline: row for row in data.rows}
    leaves = []
    for leaf in work:
        k = leaf.key
        linked = k in net.slack
        tiers = _tiers(leaf)
        wave_full = linked and (net.slack[k] == 0 or priority.rank(leaf.priority) <= 1
                                or leaf.state == "in_progress"
                                or starts[k] < HORIZON_DAYS * HOURS_PER_DAY)
        leaves.append({
            "key": k, "anchor": leaf_anchor(k), "issue": leaf.issue, "outline": leaf.outline, "title": leaf.title,
            "lane": lane_of[leaf.epic], "state": leaf.state, "sc": state_class(leaf.state),
            "mark": STATE_MARKS.get(leaf.state), "priority": leaf.priority, "eff": eff[k],
            "prio_mark": _prio_mark(leaf),
            "hours": hours[k][0], "lo": spans[k][0], "hi": spans[k][1], "unestimated": hours[k][1],
            "source": leaf.estimate.source if leaf.estimate is not None else "default",
            "effort": leaf.estimate.effort if leaf.estimate is not None else None,
            "non_time": _non_time(leaf), "linked": linked,
            "slack": net.slack[k] if linked else None,
            "sclass": slack_class(net.slack[k]) if linked else None,
            "es": net.es[k] if linked else None, "wave_full": wave_full,
            "models": tiers, "mc": [model_class(t) for t in tiers] if tiers else None,
            "flags": _flags(leaf), "meta": [[mk, mv] for mk, mv in leaf.meta.items()],
            "foreign": _foreign(site, leaf, row_at),
            "preds": net.preds.get(k, []), "succs": net.succs.get(k, []),
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
            "unestimated": sum(leaf["unestimated"] for leaf in mine),
            "models": {"think": _tier_counts(t[0] for t in tiers), "check": _tier_counts(t[1] for t in tiers)},
        })

    chain, chain_total = network.heaviest_chain(net, {k: hours[k][0] for k in net.nodes})
    path = network.critical_chain(net)
    near = {}
    for k in net.nodes:
        if 0 < net.slack[k] <= 3:
            near.setdefault(net.slack[k], []).append(k)
    held = sum(leaf.state in HELD_STATES for leaf in work)
    unest = {k for k in keys if hours[k][1]}
    unest_path = sum(k in unest for k in path)
    if source is None:
        estimates_line = None
    elif source.date is None:
        estimates_line = ESTIMATES_UNDATED.format(name=source.name)
    else:
        estimates_line = ESTIMATES_LINE.format(name=source.name, date=source.date.isoformat())
    parts = [f"{e['quantity']:g} {unit} ({_count_line(e['leaves'], '{n} leaves', '1 leaf')})"
             for unit, e in totals.non_time.items()]
    empty = not data.leaves and not data.rows
    held_out = set(data.held_out)
    drawn = set(net.nodes)
    # Each leaf the network does not draw anchors on an element of its own, in WBS order.
    anchors = {"schedule": [leaf_anchor(k) for k in keys if k not in drawn],
               "held": [leaf_anchor(leaf.key) for leaf in data.leaves if leaf.open and leaf.key in held_out]}
    finished = [{"key": leaf.key, "issue": leaf.issue, "title": leaf.title, "anchor": leaf_anchor(leaf.key)}
                for leaf in data.leaves if not leaf.open]
    if items:
        status = None
    elif empty:
        status = NO_LEAVES
    else:
        # No items but an open leaf: every open leaf is held out.
        status = ALL_HELD if any(leaf.open for leaf in data.leaves) else NO_OPEN
    return {
        "site": site,
        "empty": empty,
        "status": status,
        "lanes": lanes,
        "lanes_default": LANES_DEFAULT,
        "lanes_max": LANES_MAX,
        "hours_per_day": HOURS_PER_DAY,
        "leaves": leaves,
        "anchors": anchors,
        "finished": finished,
        "finish_day": plan_finish_day(items, planned),
        "schedule": {"items": items},
        "network": {
            "nodes": list(net.nodes),
            "edges": [{"u": u, "v": v, "cross": (u, v) in net.cross, "crit": (u, v) in net.crit_edges}
                      for u, v in net.edges],
            "links": len(links),
            "length": net.length,
            "critical": list(net.critical),
            "path": list(path),
            "near": [[s, near[s]] for s in sorted(near)],
            "heaviest": {"keys": list(chain), "hours": chain_total, "same": bool(path) and chain_total == sum(hours[k][0] for k in path)},
        },
        "panel": _panel(data),
        "summary": summary_text(items, planned, path, LANES_DEFAULT, totals.unestimated_count,
                                unest_path),
        "unestimated": {"site": totals.unestimated_count, "heaviest": sum(k in unest for k in chain),
                        "path": unest_path},
        "totals": {"hours": totals.hours, "hours_lo": totals.hours_lo, "hours_hi": totals.hours_hi,
                   "unestimated_count": totals.unestimated_count,
                   "non_time": [[unit, e["quantity"], e["leaves"]] for unit, e in totals.non_time.items()]},
        "estimates": None if source is None else {
            "name": source.name, "date": source.date.isoformat() if source.date is not None else None},
        "notes": {
            "estimates": estimates_line,
            "non_time": NON_TIME_LINE.format(parts="; ".join(parts)) if parts else None,
            "no_tiers": None if has_tiers(work) else NO_TIERS,
            "stuck": _count_line(held, STUCK_LINE, STUCK_LINE_ONE) if held else None,
            # Each P0 leaf and each leaf whose effective priority comes from one, in WBS order.
            "p0": [k for k in keys if eff[k] == 0] or None,
        },
        "texts": {"label": SCHEDULE_LABEL, "chain": CHAIN_LINE, "capacity": CAPACITY_LINE,
                  "includes": INCLUDES, "includes_one": INCLUDES_ONE, "dates": DATES, "dates_one": DATES_ONE,
                  "summary": SUMMARY, "summary_no_chain": SUMMARY_NO_CHAIN, "summary_unest": SUMMARY_UNEST,
                  "summary_unest_one": SUMMARY_UNEST_ONE, "satisfies": SATISFIES, "lit": LIT},
        "trace": trace,
    }


# --- the page --------------------------------------------------------------------

ELLIPSIS = "\u2026"
DASH = "\u2013"
DOT = " \u00b7 "
# A .lim-val character's width in viewBox units (13px monospace), to keep a value inside the figure.
VAL_CHAR_WIDTH = 7.8
ARROW = " \u2192 "
# The network's geometry, in SVG units: the label gutter, a column, a box, a
# row, a lane's padding, the header and the model band.
GUT, COLW, BW, BH, ROWH, PAD, TOP, BAND = 204, 170, 152, 66, 80, 14, 46, 17
TITLE_MAX = 40
# A chip row sits in the ROWH - BH gap under its box (#20). CHIP_FONT is the
# CSS .chip-t font size in px (a test holds the pair); a monospace character
# is taken as 0.6 of it, so a row's width is known here.
CHIP_FONT = 8
CHIP_CHAR_W = 0.6 * CHIP_FONT
CHIP_CHARS = 12  # a label's budget; the full ID is the chip's title
CHIP_PAD, CHIP_GAP, CHIP_H, CHIP_DY = 3, 3, 10, 2
UNSAFE_SCRIPT_RE = re.compile(r"</script|<!--", re.IGNORECASE)


def _script(name):
    text = render.asset(name)
    # Either would end or bend the inline script that holds it.
    if UNSAFE_SCRIPT_RE.search(text):
        raise ValueError(f"{name} must not hold </script or <!--")
    return Markup(text)


def _cut(s, n):
    """s at most n characters long; truncate before escaping."""
    return s if len(s) <= n else s[:n - 1].rstrip() + ELLIPSIS


def _wrap(text, width, lines):
    out, cur = [], ""
    for word in text.split():
        while len(word) > width:
            if cur:
                out.append(cur)
                cur = ""
            out.append(word[:width])
            word = word[width:]
        if cur and len(cur) + 1 + len(word) > width:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}" if cur else word
    if cur:
        out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1][:width - 1].rstrip() + ELLIPSIS
    return out


def _n(x):
    """An SVG number: one decimal, none when whole."""
    text = f"{x:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _h(hours):
    return f"{hours:g} h"


def _text(cls, x, y, value, anchor=None):
    extra = f' text-anchor="{anchor}"' if anchor else ""
    return f'<text class="{cls}" x="{_n(x)}" y="{_n(y)}"{extra}>{esc(value)}</text>'


def _layout(nodes, es, lane_of, preds):
    """Each node's row in its lane, near the rows of its same-lane predecessors."""
    index = {n: i for i, n in enumerate(nodes)}
    cells = {}
    for n in nodes:
        cells.setdefault((es[n], lane_of[n]), []).append(n)
    row = {}
    for (_, lane), group in sorted(cells.items()):
        def want(n, lane=lane):
            same = [row[p] for p in preds[n] if lane_of[p] == lane and p in row]
            return sum(same) / len(same) if same else 99 + index[n] / 1000
        taken = set()
        for n in sorted(group, key=want):
            w = want(n)
            target = 0 if w >= 99 else round(w)
            r = min((r for r in range(len(group) + target + 1) if r not in taken),
                    key=lambda r: (abs(r - target), r))
            taken.add(r)
            row[n] = r
    return row


def _tier_rows(lane):
    """[(tier, think count, check count)], the known tiers first."""
    think, check = dict(lane["models"]["think"]), dict(lane["models"]["check"])
    return [(t, think.get(t, 0), check.get(t, 0)) for t in _known_first(dict.fromkeys([*think, *check]))]


def _lane_label(lane, top, tiers_shown):
    """(svg parts, height) of a lane's label: name, counts and its model table."""
    full = " ".join(s for s in (lane["tag"], lane["name"]) if s)
    out = [f'<g class="lane-label" data-unestimated="{lane["unestimated"]}">', f"<title>{esc(full)}</title>"]
    ty = top + PAD + 13
    if lane["tag"]:
        out.append(_text("lane-num", 14, ty, _cut(lane["tag"], 24)))
    names = _wrap(lane["name"], 24, 2)
    for j, ln in enumerate(names):
        out.append(_text("lane-name", 14, ty + 16 + j * 14, ln))
    y = ty + 16 + len(names) * 14 + 4
    out.append(_text("lane-free", 14, y, f"{lane['open']} open{DOT}{_h(lane['hours'])}"))
    if lane["unestimated"]:
        y += 14
        out.append(_text("lane-free", 14, y, _includes(lane["unestimated"])))
    if tiers_shown:
        y += 18
        out += [_text("mt-h", 14, y, "model"), _text("mt-h", 136, y, "think", "end"),
                _text("mt-h", 184, y, "check", "end"),
                f'<line class="mt-rule" x1="14" y1="{_n(y + 4)}" x2="186" y2="{_n(y + 4)}"/>']
        y += 3
        for tier, think, check in _tier_rows(lane):
            y += 14
            out.append(f'<g class="mt-row"><title>{esc(tier)}</title>'
                       f'<rect class="mt-sw {model_class(tier)}" x="14" y="{_n(y - 9)}" width="10" height="10"/>')
            out.append(_text("mt-m", 30, y, _cut(tier, 12)))
            for count, x in ((think, 136), (check, 184)):
                out.append(_text("mt-v" if count else "mt-v zero", x, y, count or DASH, "end"))
            out.append("</g>")
    if lane["unlinked"]:
        y += 18
        out.append(_text("lane-free", 14, y, f"+{lane['unlinked']} unlinked"))
    out.append("</g>")
    return out, y + 12 - top


def _network_svg(m):
    leaves = {leaf["key"]: leaf for leaf in m["leaves"]}
    net = m["network"]
    nodes = net["nodes"]
    chip_ids = dict(m["trace"]["leaves"]) if m["trace"] is not None else {}
    es = {n: leaves[n]["es"] for n in nodes}
    lane_of = {n: leaves[n]["lane"] for n in nodes}
    row = _layout(nodes, es, lane_of, {n: leaves[n]["preds"] for n in nodes})
    tiers_shown = m["notes"]["no_tiers"] is None
    length = net["length"]
    W = GUT + max(length, 1) * COLW + 8

    lane_parts, lane_top, y = [], {}, TOP
    for li, lane in enumerate(m["lanes"]):
        label, label_h = _lane_label(lane, y, tiers_shown)
        used = max([row[n] + 1 for n in nodes if lane_of[n] == li], default=1)
        h = max(PAD * 2 + used * ROWH - (ROWH - BH), label_h)
        alt = " alt" if li % 2 else ""
        lane_parts.append(f'<rect class="lane{alt}" x="0" y="{_n(y - 4)}" width="{W}" height="{_n(h + 4)}"/>')
        lane_parts += label
        lane_top[li] = y
        y += h
    H = y + 8

    pos = {n: (GUT + es[n] * COLW + (COLW - BW) / 2, lane_top[lane_of[n]] + PAD + row[n] * ROWH) for n in nodes}
    compact = {n for n in nodes if not leaves[n]["wave_full"]}
    radius = {n: 3.0 * math.sqrt(leaves[n]["hours"]) for n in nodes}

    def centre(n):
        x, yy = pos[n]
        return x + BW / 2, yy + BH / 2 - 6

    def anchor(n, side, mode):
        if mode == "wave" and n in compact:
            cx, cy = centre(n)
            return (cx + radius[n], cy) if side == "out" else (cx - radius[n] - 2, cy)
        x, yy = pos[n]
        return (x + BW, yy + (BH - BAND) / 2) if side == "out" else (x - 2, yy + (BH - BAND) / 2)

    def curve(u, v, mode):
        x1, y1 = anchor(u, "out", mode)
        x2, y2 = anchor(v, "in", mode)
        c = max(30, (x2 - x1) * 0.5)
        return f"M{x1:.1f},{y1:.1f} C{x1 + c:.1f},{y1:.1f} {x2 - c:.1f},{y2:.1f} {x2:.1f},{y2:.1f}"

    out = [f'<svg class="net mode-wave" data-control="network-detail" data-control-items viewBox="0 0 {W} {_n(H)}" '
           f'width="{W}" height="{_n(H)}" role="img" aria-label="Dependency network of the open leaves">', "<defs>"]
    for cls in ("arrow", "arrow-crit", "arrow-cross"):
        out.append(f'<marker id="{cls}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                   f'orient="auto-start-reverse"><path class="{cls}" d="M0,1 L10,5 L0,9 z"/></marker>')
    out.append("</defs>")
    out += lane_parts
    out.append(_text("col-cap", 14, 26, "earliest slot"))
    for c in range(length):
        out.append(_text("col-num", GUT + c * COLW + COLW / 2, 26, c + 1, "middle"))
        out.append(f'<line class="col-rule" x1="{GUT + c * COLW}" y1="34" x2="{GUT + c * COLW}" y2="{_n(H)}"/>')

    out.append('<g class="edges">')
    for e in net["edges"]:
        u, v = e["u"], e["v"]
        cls = "edge" + (" crit" if e["crit"] else "") + (" cross" if e["cross"] else "")
        mk = "arrow-crit" if e["crit"] else "arrow-cross" if e["cross"] else "arrow"
        d_all, d_wave = curve(u, v, "all"), curve(u, v, "wave")
        forms = [("", d_all)] if d_all == d_wave else [(" e-all", d_all), (" e-wave", d_wave)]
        for extra, d in forms:
            out.append(f'<path class="{cls}{extra}" data-u="{esc(u)}" data-v="{esc(v)}" d="{d}" marker-end="url(#{mk})"/>')
    out.append("</g>")

    for n in nodes:
        leaf, (x, yy) = leaves[n], pos[n]
        classes = (["node", leaf["sclass"], leaf["sc"]] + (["compact"] if n in compact else [])
                   + (["unest"] if leaf["unestimated"] else []))
        out.append(f'<g class="{" ".join(classes)}" id="{esc(leaf["anchor"])}" data-item="{esc(n)}" tabindex="0">')
        tip = f"{n} {leaf['title']}"
        if leaf["priority"] is not None:
            tip += f"{DOT}priority {leaf['priority']}"
        if leaf["mark"] is not None:
            tip += f"{DOT}{leaf['mark']}"
        if leaf["unestimated"]:
            tip += f"{DOT}unestimated"
        out.append(f"<title>{esc(tip)}</title>")
        mc = leaf["mc"]
        if n in compact:
            cx, cy = centre(n)
            r = radius[n]
            dot = "dot" + (f" {mc[0]}" if mc else "")
            out.append(f'<g class="dot-form"><circle class="{dot}" cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(r)}"/>'
                       + _text("dot-l", cx, cy + r + 12, _cut(n, 14), "middle") + "</g>")
        out.append('<g class="full-form">')
        out.append(f'<rect class="box" x="{_n(x)}" y="{_n(yy)}" width="{BW}" height="{BH}" rx="3"/>')
        out.append(_text("nid", x + 8, yy + 15, _cut(n, 16)))
        meta = ([_cut(leaf["priority"], 6)] if leaf["priority"] is not None else []) + [_h(leaf["hours"])]
        out.append(_text("nmeta", x + BW - 8, yy + 15, DOT.join(meta), "end"))
        for j, ln in enumerate(_wrap(_cut(leaf["title"], TITLE_MAX), 25, 2)):
            out.append(_text("ntitle", x + 8, yy + 30 + j * 12.5, ln))
        if mc:
            by, half = yy + BH - BAND, BW / 2
            for k, label in enumerate(("think", "check")):
                bx = x + k * half
                tier = leaf["models"][k]
                out.append(f'<rect class="band {mc[k]}" x="{_n(bx)}" y="{_n(by)}" width="{_n(half)}" height="{BAND}"/>')
                out.append(_text(f"band-t {mc[k]}", bx + half / 2, by + 12,
                                 f"{label} {_cut(tier if tier is not None else 'none', 9)}", "middle"))
            out.append(f'<line class="band-split" x1="{_n(x + half)}" y1="{_n(by)}" x2="{_n(x + half)}" y2="{_n(yy + BH)}"/>')
        out.append(f'<rect class="outline" x="{_n(x)}" y="{_n(yy)}" width="{BW}" height="{BH}" rx="3"/>')
        out += _chips(chip_ids.get(n, []), x, yy)
        out.append("</g></g>")
    out.append("</svg>")
    return Markup("\n".join(out))


def _limits_svg(m):
    """(svg, caption) of the Two-limits figure, the prototype's geometry (#10).

    The chain row sums the named critical chain, the total row the scheduled
    leaves; each row's value carries its N and, above 0, a note below its bar.
    """
    leaves = {leaf["key"]: leaf for leaf in m["leaves"]}
    path = m["network"]["path"]
    ch_lo = sum(leaves[k]["lo"] for k in path)
    ch_hi = sum(leaves[k]["hi"] for k in path)
    tot = m["totals"]
    tot_lo, tot_hi = tot["hours_lo"], tot["hours_hi"]
    scale = 560 / tot_hi if tot_hi > 0 else 0
    unest = m["unestimated"]
    dy = 0
    if path:
        out = [_text("lim-label", 0, 30, f"Critical chain ({_count_line(len(path), '{n} leaves', '1 leaf')})"),
               f'<rect class="bar-crit" x="190" y="16" width="{_n(ch_lo * scale)}" height="20"/>',
               f'<rect class="bar-crit span" x="{_n(190 + ch_lo * scale)}" y="16" '
               f'width="{_n((ch_hi - ch_lo) * scale)}" height="20"/>',
               f'<g data-unestimated="{unest["path"]}">']
        value, x = f"{ch_lo:g}{DASH}{ch_hi:g} h", 190 + ch_hi * scale + 8
        if x + VAL_CHAR_WIDTH * len(value) <= 760:
            out.append(_text("lim-val", x, 31, value))
        else:
            # Past the figure's edge (a chain near the whole total): under the bar's end, as the total row's.
            out.append(_text("lim-val", 750, 52, value, "end"))
            dy += 16
    else:
        # No chain: the row says so and draws no bar.
        out = [_text("lim-label", 0, 30, "Critical chain"), '<g data-unestimated="0">',
               _text("lim-val", 190, 31, NO_CHAIN)]
    if unest["path"]:
        out.append(_text("lim-note", 190, 52 + dy, _includes(unest["path"])))
        dy += 16
    out.append("</g>")
    n = len(m["schedule"]["items"])
    out += [_text("lim-label", 0, 78 + dy, _count_line(n, "All {n} scheduled leaves", "The 1 scheduled leaf")),
            f'<rect class="bar-open" x="190" y="{64 + dy}" width="{_n(tot_lo * scale)}" height="20"/>',
            f'<rect class="bar-open span" x="{_n(190 + tot_lo * scale)}" y="{64 + dy}" '
            f'width="{_n((tot_hi - tot_lo) * scale)}" height="20"/>',
            f'<g data-unestimated="{unest["site"]}">',
            _text("lim-val", 750, 102 + dy, f"{tot_lo:g}{DASH}{tot_hi:g} h", "end")]
    if unest["site"]:
        out.append(_text("lim-note", 750, 118 + dy, _includes(unest["site"]), "end"))
        dy += 16
    out += ["</g>",
            f'<line class="lim-axis" x1="190" y1="{110 + dy}" x2="750" y2="{110 + dy}"/>',
            _text("lim-note", 190, 128 + dy, "agent-hours; solid to the low estimate, pale to the high one")]
    label = (f"The critical chain is {ch_lo:g} to {ch_hi:g} agent-hours; all open work is {tot_lo:g} to "
             f"{tot_hi:g} agent-hours." if path else
             f"There is no critical chain; all open work is {tot_lo:g} to {tot_hi:g} agent-hours.")
    svg = (f'<svg viewBox="0 0 760 {136 + dy}" role="img" aria-label="{esc(label)}">' + "".join(out) + "</svg>")
    days = {"hpd": HOURS_PER_DAY, "c": _round(tot_lo / HOURS_PER_DAY), "d": _round(tot_hi / HOURS_PER_DAY)}
    if path:
        caption = LIMITS_CAPTION.format(a=_round(ch_lo / HOURS_PER_DAY), b=_round(ch_hi / HOURS_PER_DAY), **days)
    else:
        caption = LIMITS_CAPTION_NO_CHAIN.format(**days)
    if unest["site"]:
        caption += f" The total {_includes(unest['site'])}."
    return Markup(svg), caption


def _note(text):
    return Markup(f'<p class="note">{esc(text)}</p>') if text else Markup("")


def _mono(keys, sep=", "):
    return Markup(sep.join(f'<span class="mono">{esc(k)}</span>' for k in keys))


def _anchors(anchors):
    """Empty static spans, one per anchor: a link lands on the heading that holds them."""
    return Markup("".join(f'<span class="anchor" id="{esc(a)}"></span>' for a in anchors))


def _leaf_line(key, issue, title):
    return f"{_mono([key])}{DOT}{esc(issue if issue is not None else NO_ISSUE_ID)}{DOT}{esc(title)}"


def _finished(m):
    if not m["finished"]:
        return Markup(f"<p>{esc(NO_FINISHED)}</p>")
    lines = "".join(f'<li id="{esc(f["anchor"])}">{_leaf_line(f["key"], f["issue"], f["title"])}</li>'
                    for f in m["finished"])
    return Markup(f'<ul class="panel finished">{lines}</ul>')


# --- the trace (#20) ---------------------------------------------------------------


def unserved_label(scope):
    """The unserved category's label, from the report's scope; an unknown scope is named, never a KeyError."""
    return f"{TRACE_LABELS['unserved']} ({UNSERVED_SCOPES.get(scope, UNSERVED_SCOPE_OTHER.format(scope=scope))})"


def _chip_label(label):
    """A chip's label within CHIP_CHARS. A long one keeps its code and number
    whole and shortens its namespace, so two IDs of one namespace stay apart.
    A code and number of 10 or more characters is cut plainly, and two
    namespaces sharing a prefix can read alike: the chip's title has the ID."""
    if len(label) <= CHIP_CHARS:
        return label
    namespace, colon, tail = label.rpartition(":")
    keep = CHIP_CHARS - len(tail) - 2  # the ellipsis and the colon
    if not colon or keep < 1:
        return _cut(label, CHIP_CHARS)
    return namespace[:keep] + ELLIPSIS + ":" + tail


def _chips(ids, x, y):
    """The chip row in the gap under a box at (x, y): up to CHIPS_MAX chips
    that fit in its width, then CHIP_MORE for the rest."""
    out, cx, right = [], x, x + BW
    top = y + BH + CHIP_DY
    base = top + CHIP_H - 2.5
    for i, (rid, label) in enumerate(ids[:CHIPS_MAX]):
        text = _chip_label(label)
        w = len(text) * CHIP_CHAR_W + 2 * CHIP_PAD
        rest = len(ids) - i - 1
        reserve = len(CHIP_MORE.format(n=rest)) * CHIP_CHAR_W + CHIP_GAP if rest else 0
        if cx + w + reserve > right:
            break
        out.append(f'<g class="chip" data-req="{esc(rid)}" tabindex="0" role="button" aria-pressed="false">'
                   f"<title>{esc(rid)}</title>"
                   f'<rect class="chip-box" x="{_n(cx)}" y="{_n(top)}" width="{_n(w)}" height="{CHIP_H}" rx="2"/>'
                   + _text("chip-t", cx + CHIP_PAD, base, text) + "</g>")
        cx += w + CHIP_GAP
    if len(out) < len(ids):
        out.append(_text("chip-more", cx, base, CHIP_MORE.format(n=len(ids) - len(out))))
    return out


def _req_row(r):
    rid = esc(r["id"])
    text = f" {esc(r['text'])}" if r["text"] is not None else ""
    out = (f'<li class="req" data-req="{rid}"><button type="button" class="req-row" data-control="requirement" '
           f'data-value="{rid}" aria-pressed="false"><span class="mono">{esc(r["label"])}</span>{text} '
           f'<span class="req-count">{esc(r["counts"])}</span></button>')
    rows = [(row, ROW_HELD) for row in r["held"]] + [(row, ROW_FINISHED) for row in r["finished"]]
    if rows:
        lines = "".join(f"<li>{_leaf_line(key, issue, title)}{DOT}{esc(what)}</li>"
                        for (key, issue, title, _), what in rows)
        out += (f'<button type="button" class="req-more" data-control="requirement-detail" data-value="{rid}" '
                f'aria-expanded="false">{esc(ROW_DETAIL)}</button><ul class="req-detail gone">{lines}</ul>')
    return out + "</li>"


def _requirements(trace):
    """The requirements panel: on a site in the trace, or one whose leaves name an ID that resolves."""
    if trace is None or not (trace["in_trace"] or trace["requirements"]):
        return Markup("")
    groups = {}
    for r in trace["requirements"]:
        groups.setdefault(r["layer"], []).append(r)
    parts = [f'<section class="prose" id="requirements"><h2>{esc(REQUIREMENTS_HEADING)}</h2>'
             f'<p class="label">{esc(SELECT_NOTE)}</p>']
    for layer, rows in groups.items():
        parts.append(f'<h3>{esc(LAYER_HEADINGS[layer])}</h3><ul class="panel reqs">'
                     + "".join(_req_row(r) for r in rows) + "</ul>")
    return Markup("".join(parts) + "</section>")


def _trace_item(name, item):
    if name == "unserved":
        return esc(item[1])
    if name == "untraced":
        return f"{_mono([item[0]])}{DOT}{esc(item[1])}"
    source, line, message = item
    form = ENTRY_WHOLE if line is None else ENTRY_LINE
    return esc(form.format(source=source, line=line, message=message))


def _trace_panel(trace):
    """The trace report panel: drawn when any category has an item (D2)."""
    if trace is None or not any(items for _, items in trace["report"]):
        return Markup("")
    cats = []
    for name, items in trace["report"]:
        label = unserved_label(trace["scope"]) if name == "unserved" else TRACE_LABELS[name]
        head = f'<span class="k">{esc(TRACE_COUNT.format(label=label, n=len(items)))}</span>'
        body = ("<ul>" + "".join(f"<li>{_trace_item(name, item)}</li>" for item in items) + "</ul>"
                if items else f" {esc(TRACE_NONE)}")
        cats.append(f'<li class="trace-cat">{head}{body}</li>')
    return Markup(f'<section class="prose" id="trace"><h2>{esc(TRACE_HEADING)}</h2>'
                  f'<ul class="panel trace">{"".join(cats)}</ul></section>')


def render_page(site, data, *, default_unit_hours, inputs=None, stamp="", trace=None) -> str:
    """The worksite page for one site's parsed WBS.md, as one HTML text."""
    return render_model(page_model(site, data, default_unit_hours=default_unit_hours, inputs=inputs, trace=trace),
                        stamp=stamp)


def render_model(m, *, stamp="") -> str:
    """The worksite page for a page_model result, as one HTML text; `stamp` is the build stamp, if any."""
    site = m["site"]
    net = m["network"]
    items = m["schedule"]["items"]
    linked = len(net["nodes"])
    status = m["status"]
    heavy = net["heaviest"]
    if not heavy["keys"]:
        hours_note = "No open leaf waits on another."
    elif heavy["same"]:
        hours_note = f"Weighting each leaf by its hours picks the same chain, {_h(heavy['hours'])} end to end."
    else:
        hours_note = (f"Weighting each leaf by its hours picks a different chain: {ARROW.join(heavy['keys'])}, "
                      f"{_h(heavy['hours'])} end to end.")
    unest = m["unestimated"]
    if unest["heaviest"]:
        hours_note = f"{hours_note[:-1]}; {_includes(unest['heaviest'])}."
    near = Markup("; ".join(f"slack {s}: " + _mono(keys) for s, keys in net["near"]))
    options = "".join(f'<option value="{i}"{" selected" if i == LANES_DEFAULT else ""}>{i}</option>'
                      for i in range(1, LANES_MAX + 1))
    panel = m["panel"] or [NOTHING_MISSING]
    limits_svg, limits_caption = _limits_svg(m)
    return fill(
        render.asset("templates/worksite.html"),
        SITE=site,
        STAMP=render.stamp_html(stamp),
        STATUS=_note(status),
        ESTIMATES=Markup(f'<p class="label">{esc(m["notes"]["estimates"])}</p>') if m["notes"]["estimates"] else "",
        NON_TIME=_note(m["notes"]["non_time"]),
        WORK_CLASS="work gone" if status else "work",
        HORIZON=HORIZON_DAYS,
        LANES=LANES_DEFAULT,
        NO_TIERS=_note(m["notes"]["no_tiers"]),
        SVG=_network_svg(m),
        LENGTH=net["length"],
        CHAIN=_mono(net["path"], ARROW) or "none",
        NEAR=near or "none",
        HOURS_NOTE=hours_note,
        HOURS_UNEST=unest["heaviest"],
        SITE_UNEST=unest["site"],
        PATH_UNEST=unest["path"],
        SUMMARY=m["summary"],
        LIMITS_SVG=limits_svg,
        LIMITS_CAPTION=limits_caption,
        DATES=_count_line(unest["site"], DATES, DATES_ONE) if unest["site"] else "",
        COUNTS=(f"{linked} of the {len(items)} open leaves have an open link and appear in the network; the other "
                f"{len(items) - linked} appear only in the schedule. WBS.md gives {net['links']} links between "
                f"them; {net['links'] - len(net['edges'])} repeat what a longer chain implies and are not drawn."),
        HOURS_PER_DAY=HOURS_PER_DAY,
        LABEL=SCHEDULE_LABEL,
        STUCK=_note(m["notes"]["stuck"]),
        P0=Markup(f'<p class="note">{esc(P0_NOTE)}{_mono(m["notes"]["p0"])}</p>') if m["notes"]["p0"] else "",
        LANE_OPTIONS=Markup(options),
        PANEL=Markup("".join(f"<li>{esc(line)}</li>" for line in panel)),
        SCHEDULE_ANCHORS=_anchors(m["anchors"]["schedule"]),
        HELD_ANCHORS=_anchors(m["anchors"]["held"]),
        FINISHED_HEADING=FINISHED_HEADING,
        FINISHED=_finished(m),
        REQUIREMENTS=_requirements(m["trace"]),
        TRACE=_trace_panel(m["trace"]),
        DATA=embed_json(m),
        SCHEDULE_JS=_script("static/schedule.js"),
        PAGE_JS=_script("static/worksite.js"),
    )
