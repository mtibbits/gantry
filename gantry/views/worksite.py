"""The worksite page for one site (#9).

page_model turns a site's parsed WBS.md into the page's data: plain lists,
dicts, strings and numbers, ready for JSON. Records are arrays, never objects
keyed by a record value, so no record value can become a key the page's
script looks up on an object. render_page draws the network and fills the
template. docs/worksite-page.md describes the page.
"""

import math
import re
from importlib import resources

from gantry import network, priority, schedule
from gantry.deps import refs
from gantry.model import FINISHED_GLYPHS, HOURS_PER_DAY, meta_scalar
from gantry.render import MODEL_CLASSES, Markup, embed_json, esc, fill, model_class, slack_class, state_class

LANES_DEFAULT = 4
LANES_MAX = 8
HORIZON_DAYS = 10

SCHEDULE_LABEL = "agent capacity only; ignores operator attention; the week view is the plan"
STUCK_LINE = "{n} stuck, blocked or parked leaves are scheduled as if an agent could start them"
STUCK_LINE_ONE = "1 stuck, blocked or parked leaf is scheduled as if an agent could start it"
INTERIM_NOTE = "every duration is the default unit; no estimates are read yet"
NO_TIERS = "no per-leaf model tiers recorded"
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
            "models": {"think": _tier_counts(t[0] for t in tiers), "check": _tier_counts(t[1] for t in tiers)},
        })

    chain, chain_total = network.heaviest_chain(net, {k: hours[k][0] for k in net.nodes})
    path = network.critical_chain(net)
    near = {}
    for k in net.nodes:
        if 0 < net.slack[k] <= 3:
            near.setdefault(net.slack[k], []).append(k)
    held = sum(leaf.state in HELD_STATES for leaf in work)
    empty = not data.leaves and not data.rows
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
        "notes": {
            "interim": INTERIM_NOTE if keys and all(hours[k][1] for k in keys) else None,
            "no_tiers": None if any(leaf["models"] for leaf in leaves) else NO_TIERS,
            "stuck": _count_line(held, STUCK_LINE, STUCK_LINE_ONE) if held else None,
        },
        "texts": {"label": SCHEDULE_LABEL, "chain": CHAIN_LINE, "capacity": CAPACITY_LINE},
    }


# --- the page --------------------------------------------------------------------

ELLIPSIS = "\u2026"
DASH = "\u2013"
DOT = " \u00b7 "
ARROW = " \u2192 "
# The network's geometry, in SVG units: the label gutter, a column, a box, a
# row, a lane's padding, the header and the model band.
GUT, COLW, BW, BH, ROWH, PAD, TOP, BAND = 204, 170, 152, 66, 80, 14, 46, 17
TITLE_MAX = 40
UNSAFE_SCRIPT_RE = re.compile(r"</script|<!--", re.IGNORECASE)


def _asset(name):
    """A packaged page asset's text, read as UTF-8 bytes."""
    return resources.files("gantry").joinpath(name).read_bytes().decode("utf-8")


def _script(name):
    text = _asset(name)
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
    out = ['<g class="lane-label">', f"<title>{esc(full)}</title>"]
    ty = top + PAD + 13
    if lane["tag"]:
        out.append(_text("lane-num", 14, ty, _cut(lane["tag"], 24)))
    names = _wrap(lane["name"], 24, 2)
    for j, ln in enumerate(names):
        out.append(_text("lane-name", 14, ty + 16 + j * 14, ln))
    y = ty + 16 + len(names) * 14 + 4
    out.append(_text("lane-free", 14, y, f"{lane['open']} open{DOT}{_h(lane['hours'])}"))
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
        classes = ["node", leaf["sclass"], leaf["sc"]] + (["compact"] if n in compact else [])
        out.append(f'<g class="{" ".join(classes)}" data-item="{esc(n)}" tabindex="0">')
        tip = f"{n} {leaf['title']}"
        if leaf["priority"] is not None:
            tip += f"{DOT}priority {leaf['priority']}"
        if leaf["mark"] is not None:
            tip += f"{DOT}{leaf['mark']}"
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
        out.append("</g></g>")
    out.append("</svg>")
    return Markup("\n".join(out))


def _note(text):
    return Markup(f'<p class="note">{esc(text)}</p>') if text else Markup("")


def _mono(keys, sep=", "):
    return Markup(sep.join(f'<span class="mono">{esc(k)}</span>' for k in keys))


def render_page(site, data, *, default_unit_hours) -> str:
    """The worksite page for one site's parsed WBS.md, as one HTML text."""
    return render_model(page_model(site, data, default_unit_hours=default_unit_hours))


def render_model(m) -> str:
    """The worksite page for a page_model result, as one HTML text."""
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
    near = Markup("; ".join(f"slack {s}: " + _mono(keys) for s, keys in net["near"]))
    options = "".join(f'<option value="{i}"{" selected" if i == LANES_DEFAULT else ""}>{i}</option>'
                      for i in range(1, LANES_MAX + 1))
    panel = m["panel"] or [NOTHING_MISSING]
    return fill(
        _asset("templates/worksite.html"),
        SITE=site,
        STATUS=_note(status),
        INTERIM=_note(m["notes"]["interim"]),
        WORK_CLASS="work gone" if status else "work",
        HORIZON=HORIZON_DAYS,
        LANES=LANES_DEFAULT,
        NO_TIERS=_note(m["notes"]["no_tiers"]),
        SVG=_network_svg(m),
        LENGTH=net["length"],
        CHAIN=_mono(net["path"], ARROW) or "none",
        NEAR=near or "none",
        HOURS_NOTE=hours_note,
        COUNTS=(f"{linked} of the {len(items)} open leaves have an open link and appear in the network; the other "
                f"{len(items) - linked} appear only in the schedule. WBS.md gives {net['links']} links between "
                f"them; {net['links'] - len(net['edges'])} repeat what a longer chain implies and are not drawn."),
        HOURS_PER_DAY=HOURS_PER_DAY,
        LABEL=SCHEDULE_LABEL,
        STUCK=_note(m["notes"]["stuck"]),
        LANE_OPTIONS=Markup(options),
        PANEL=Markup("".join(f"<li>{esc(line)}</li>" for line in panel)),
        DATA=embed_json(m),
        SCHEDULE_JS=_script("static/schedule.js"),
        PAGE_JS=_script("static/worksite.js"),
    )
