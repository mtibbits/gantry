"""The week view: the build's week plan as one page, read only (#38).

render_week reads the plan's file form (planner.WeekPlan) and the BuildResult
every view reads: each leaf's title, raw est: value, own priority, model
tiers and waits_on_other_site from result.inputs, each site page model's
slack class and workflow phase row (`phases`, `phases_why`, #173) from
result.page_models, which sites have pages, and the not-built reasons. A
placement is drawn as the worksite network's leaf box, by the one rule in
gantry/views/leafbox.py (#170), with the same phase row. It computes no planning
value and never changes the plan. All 10 days are rendered here; the page
script only shows or hides days and lists. docs/week-plan.md describes the page.
"""

from dataclasses import asdict
from datetime import date
from fractions import Fraction

from gantry import phases, planner, render
from gantry.links import leaf_href
from gantry.portfolio_file import WEEKDAYS
from gantry.render import Markup, decimals, embed_json, esc, fill, hours_text, hundredths, unestimated_text
from gantry.sites import shown
from gantry.views import leafbox
from gantry.views.worksite import DOT, _count_line, _script

PAGE = "week.html"
TITLE = "Week view"
LINK = (TITLE, PAGE)

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKDAY_OF = dict(zip(WEEKDAYS, WEEKDAY_NAMES))  # the plan's weekday, "mon" to "sun", to its name
DAY_HEAD = "{weekday} {date}"
TODAY = "today"
NOT_WORKING = "the as-of date {as_of} is not a working day; the plan starts {weekday} {date}"
DAY_HOURS = "{hours} attention after reserved blocks"
RESERVED = "reserved {start}, {hours}"
RESERVED_LABEL = "reserved {start}, {hours}: {label}"  # only when the label is not blank
NO_BLOCKS = "no block planned"
START_COST = "start cost {hours}"  # a block's head: site, length, START_COST
LANES_ROW = "runs in a lane; no block needed"
LEFT = "left: {attention_pool} {attention}; {lanes_pool} {lanes}"  # pool names from the plan
STATE_TEXTS = {"in_progress": "in progress", "stuck": "stuck"}  # pending: none; unknown: raw
P0_MARK = "P0"  # started, fair_share: none; unknown tier: raw
NEED = "need {need}, {tag}"
RATE = "{rate} h per agent hour, {tag}"  # need None
CONTINUES = "continues {weekday} {date}"
CONTINUES_AFTER = "continues after {weekday} {date} ({hours} left)"
BINDING = "binding: {pool} (ran out first on {weekday} {date})"
NO_BINDING = "no pool ran out in the planned week"
SOURCE = "week plan as of {as_of}"
RULE = "{rule} (Gantry v1); devagent's forecast (mtibbits/devagent#745) may allocate differently"
MAPPING = "attention mapping: {values} operator hours per agent hour for needs 0 to 4, {source}"
MAPPING_SOURCES = {"default": "default", "portfolio_file": "from the portfolio file"}
MAPPING_REJECTED = "the portfolio file's mapping was not used: {reason}"
CAPACITY = "capacity from: {parts}"  # each "{name}: {capacity_source}", joined by "; "
# What the plan says of devagent's capacity declaration (#39), after CAPACITY (_capacity_notes).
SCALED = ("devagent declares {total} a week; the portfolio file's weekdays after reserved blocks sum to {sum}; "
          "each day is scaled by {factor}")
EVEN_SPREAD = ("devagent declares {total} a week; the portfolio file sets no attention_hours, so it is spread "
               "evenly over Monday to Friday")
DAY_CAPPED = "{weekday}: {lost} of devagent's weekly hours do not fit in the day after its reserved blocks"
INVALID = "devagent's capacity declaration is invalid; the portfolio file's capacity is used"  # with a plan
INVALID_NO_PLAN = "devagent's capacity declaration is invalid"  # on the no-plan page: no capacity is used
# For a declared ratio the planner plans as rate 0 (planner.is_rate_0), which 0 itself is.
RATIO_ZERO = "devagent declares 0 operator hours per agent hour; unscored leaves run in lanes without blocks"
TOKENS = "token budget (not scheduled): {n} {unit} a week"
COUNT = "Includes {n} unestimated leaves"
COUNT_ONE = "Includes 1 unestimated leaf"
SHARELESS = ("{n} sites have no intended share set; they are planned only into capacity left after the sites "
             "with shares")
SHARELESS_ONE = "1 site has no intended share set; it is planned only into capacity left after the sites with shares"
SHARELESS_ALL = "{site}: on every planned day"
SHARELESS_DAYS = "{site}: on {days}"
P0_CAPTION = "P0 leaves"
HELD_BY = "held by {holders}"
UNPLANNED_HEAD = "Unplanned leaves"
BEYOND = "{n} leaves beyond the 10 days"
BEYOND_ONE = "1 leaf beyond the 10 days"
BEYOND_BUTTON = "show or hide"
DAYS_BUTTONS = (("5", "5 days"), ("10", "10 days"))
NOTHING_UNPLANNED = "no unplanned leaves"
REASON_TEXTS = {"paused": "paused in devagent's capacity declaration",
                "blocked_externally": "blocked externally", "parked": "parked",
                "waits_on_cycle": "waits on a cycle", "cross_site_cycle": "part of a cycle across sites",
                "waits_on_site_absent": "waits on a site not in this installation",
                "waits_on_unresolved": "waits on a reference that could not be resolved",
                "waits_on_unplanned": "waits on an unplanned leaf"}
UNKNOWN_REASON = "not planned: {code}"
NOT_BUILT_LINE = "{name}: not built: {reason}"
PER_SITE_HEAD = "Per site"
SITE_HEAD = "site"
NO_SHARE = "no intended share set"
SHARE_HEAD = "share on {weekday} {date}"
CHARGED = "{charged} charged, {target} target"
NO_PLAN = "no week plan: {reason}"
# The legend (#170): what a box's red fill and the P0 mark say.
LEGEND_CRIT = "critical: slack 0 on its own site's network"
LEGEND_NEAR = "near-critical: slack 1\u20133 on its own site's network"
LEGEND_P0 = "P0: in the P0 tier, because its own priority or that of a leaf it blocks, directly or not, is P0"

BEYOND_HORIZON = planner.BEYOND_HORIZON


def rate_text(r) -> str:
    """A rate as written, with no exponent: 1e-06 is "0.000001", 20.0 is "20"."""
    exact = Fraction(repr(float(r)))
    if exact.denominator == 1:
        return str(exact.numerator)
    digits = 0
    while (exact * 10 ** digits).denominator != 1:
        digits += 1
    whole = abs(exact.numerator) * 10 ** digits // exact.denominator
    text = f"{whole // 10 ** digits}.{whole % 10 ** digits:0{digits}d}"
    return ("-" if exact < 0 else "") + text


def share_text(share) -> str:
    """A share in percent, rounded as hours are: "50%", "33.33%"."""
    return hundredths(share) + "%"


def factor_text(factor) -> str:
    """A scaling factor, rounded as hours are but to four decimals: "1.5", "0.6667"."""
    return decimals(factor, 4)


def _named(fmt, text, **kw):
    """fmt with {weekday} and {date} filled from a YYYY-MM-DD date."""
    return fmt.format(weekday=WEEKDAY_NAMES[date.fromisoformat(text).weekday()], date=text, **kw)


def _kept_row(looks, why):
    """A page model's phase row as a PhaseRow, or None: five members of LOOKS,
    either all unknown with a non-empty str reason, or none unknown."""
    if not isinstance(looks, list) or len(looks) != len(phases.PHASE_NAMES):
        return None
    if not all(isinstance(look, str) and look in phases.LOOKS for look in looks):
        return None
    if all(look == "unknown" for look in looks):
        return phases.PhaseRow(tuple(looks), why) if isinstance(why, str) and why else None
    return phases.PhaseRow(tuple(looks), None) if "unknown" not in looks else None


class _Page:
    def __init__(self, plan, result):
        self.plan = plan
        self.result = result
        self.leaves = {}
        for site, inputs in result.inputs.items():
            for leaf in inputs.data.leaves:
                self.leaves[(site, leaf.key)] = leaf
        self.records = {(entry["site"], entry["key"]): entry for entry in plan.leaves}
        # {(site, key): its slack class or None}, from each rendered site's page model (#170),
        # kept only when it is one of render's slack classes.
        self.sclass = {(site, leaf["key"]): leaf["sclass"] if leaf["sclass"] in render.SLACK_CLASSES else None
                       for site, m in result.page_models.items() for leaf in m["leaves"]}
        # {(site, key): its workflow phase row (#173)}, from the same page models, kept only
        # when it is a row the phase rule can give (_kept_row); anything else draws no row.
        self.phases = {(site, leaf["key"]): row for site, m in result.page_models.items() for leaf in m["leaves"]
                       if (row := _kept_row(leaf.get("phases"), leaf.get("phases_why"))) is not None}
        # (site, key) -> [site, key, title, est, priority, thinking, checking], in the order the page names them
        self.named = {}

    def title(self, site, key):
        leaf = self.leaves.get((site, key))
        if (site, key) not in self.named:
            tiers = (leafbox.model_tiers(leaf.models) if leaf else None) or [None, None]
            self.named[(site, key)] = [site, key, leaf.title if leaf else None, leaf.meta.get("est") if leaf else None,
                                       leaf.priority if leaf else None, *tiers]
        return leaf.title if leaf is not None and leaf.title else key

    def link(self, site, key, inner=None):
        """The leaf's title (or `inner`, escaped unless Markup), linked to its worksite anchor when the site's page was written."""
        title = esc(self.title(site, key) if inner is None else inner) if not isinstance(inner, Markup) else inner
        if site not in self.result.pages:
            return f'<span class="title">{title}</span>'
        return f'<a href="{esc(leaf_href(site, key, from_top=True))}">{title}</a>'

    def box(self, site, key, record):
        """(leafbox.Box, its cue classes) for a placement, by the worksite's rule."""
        title = self.title(site, key)
        # The named row's priority and tiers: the page's data and its band read one value.
        *_, prio, thinking, checking = self.named[(site, key)]
        leaf = self.leaves.get((site, key))
        b = leafbox.box(key, prio, title, [thinking, checking] if thinking or checking else None)
        return b, leafbox.box_classes(self.sclass.get((site, key)), render.state_class(record["state"]),
                                      record["unestimated"], bool(leaf and leaf.waits_on_other_site))


def _p(cls, text):
    return f'<p class="{cls}">{esc(text)}</p>'


def _span(cls, text):
    return f'<span class="{cls}">{esc(text)}</span>'


def _placements(plan):
    """{(site, key): [date, ...]}, one per placement in page order."""
    found = {}
    for day in plan.days:
        for entry in [*(e for blk in day["blocks"] for e in blk["leaves"]), *day["lanes_row"]]:
            found.setdefault((entry["site"], entry["key"]), []).append(day["date"])
    return found


def _box(page, entry, cont):
    """A placement as the worksite's leaf box: the top line, the title lines,
    the facts, then the model band."""
    site, key = entry["site"], entry["key"]
    record = page.records[(site, key)]
    leaf = page.leaves.get((site, key))
    state, tier, tag = record["state"], record["tier"], record["tag"]
    b, cues = page.box(site, key, record)
    prio = f'{_span("prio", b.priority)}{esc(DOT)}' if b.priority is not None else ""
    top = (f'<p class="top">{_span("key", b.key)}'
           f'<span class="meta">{prio}{_span("hours", hours_text(entry["agent_hours"]))}</span></p>')
    ttl = f'<p class="ttl">{page.link(site, key, Markup("".join(_span("tl", line) for line in b.title_lines)))}</p>'
    parts = []
    if record["unestimated"]:
        parts.append(_span("unest", unestimated_text(leaf.meta.get("est") if leaf is not None else None)))
    if state != "pending":
        parts.append(_span("state", STATE_TEXTS.get(state, state)))
    if tier == planner.TIER_P0:
        parts.append(_span("tier", P0_MARK))
    elif tier not in planner.TIERS:
        parts.append(_span("tier", tier))
    if record["need"] is None:
        parts.append(_span("need", RATE.format(rate=rate_text(record["rate"]), tag=tag)))
    else:
        parts.append(_span("need", NEED.format(need=record["need"], tag=tag)))
    parts.append(_span("basis", record["basis"]))
    if cont:
        parts.append(_span("cont", cont))
    row = page.phases.get((site, key))
    row_html = f'<p class="phases">{leafbox.phase_svg(row)}</p>' if row is not None else ""
    bands = ""
    if b.band:
        bands = f'<p class="bands">{"".join(_span(f"band {cls}", text) for text, cls in b.band)}</p>'
    classes = " ".join(["leaf", *cues, render.tier_class(tier), render.tag_class(tag)])
    return (f'<li class="{classes}" data-leaf="{esc(key)}" data-site="{esc(site)}" data-tag="{esc(tag)}" '
            f'data-state="{esc(state)}" data-tier="{esc(tier)}">{top}{ttl}{row_html}'
            f'<p class="facts">{"".join(parts)}</p>{bands}</li>')


def _conts(plan, records, placements):
    """{(site, key): an iterator of continues text or None, one per placement in page order."""
    last_day = plan.horizon_days[-1] if plan.horizon_days else None
    out = {}
    for node, dates in placements.items():
        texts = [_named(CONTINUES, nxt) for nxt in dates[1:]]
        after = records[node]["hours_after_horizon"]
        left = _named(CONTINUES_AFTER, last_day, hours=hours_text(after)) if after > 0 and last_day else None
        out[node] = iter(texts + [left])
    return out


def _day(page, day, conts):
    date_text = day["date"]
    later = date_text not in page.plan.planned_week
    today = date_text == page.plan.as_of
    head = [f'<h2>{esc(_named(DAY_HEAD, date_text))}</h2>']
    if today:
        head.append(_p("today", TODAY))
    head.append(_p("hours", DAY_HOURS.format(hours=hours_text(day["attention_hours"]))))
    for r in day["reserved"]:
        label = r["label"]
        if isinstance(label, str) and label.strip():
            text = RESERVED_LABEL.format(start=r["start"], hours=hours_text(r["hours"]), label=label)
        else:
            text = RESERVED.format(start=r["start"], hours=hours_text(r["hours"]))
        head.append(_p("reserved", text))

    def line(entry):
        return _box(page, entry, next(conts[(entry["site"], entry["key"])]))

    body = []
    for blk in day["blocks"]:
        name = esc(blk["site"])
        title = DOT.join([blk["site"], hours_text(blk["length_hours"]),
                          START_COST.format(hours=hours_text(blk["start_cost_hours"]))])
        body.append(f'<div class="block" data-block="{esc(blk["number"])}" data-site="{name}"><h3>{esc(title)}</h3>'
                    f'<ul>{"".join(line(entry) for entry in blk["leaves"])}</ul></div>')
    if not day["blocks"]:
        body.append(_p("none", NO_BLOCKS))
    row = day["lanes_row"]
    if row:
        # A lanes row mixes sites: one group per site, headed by its name, in
        # first-appearance order, with plan order inside a site (#170).
        groups = {}
        for entry in row:
            groups.setdefault(entry["site"], []).append(entry)
        lanes = "".join(f'<div class="lane-site" data-site="{esc(site)}"><h4>{esc(site)}</h4>'
                        f'<ul>{"".join(line(entry) for entry in entries)}</ul></div>'
                        for site, entries in groups.items())
        body.append(f'<div class="lanes" data-lanes="{len(row)}"><h3>{esc(LANES_ROW)}</h3>{lanes}</div>')
    pools = page.plan.pools
    body.append(_p("left", LEFT.format(attention_pool=pools[0]["name"],
                                       attention=hours_text(day["left"]["attention_hours"]),
                                       lanes_pool=pools[1]["name"], lanes=hours_text(day["left"]["lane_hours"]))))
    classes = "day later" if later else "day"
    today_attr = ' data-today="true"' if today else ""
    return (f'<section class="{classes}" data-day="{esc(date_text)}"{today_attr}>'
            f'{"".join(head)}{"".join(body)}</section>')


def _capacity_notes(plan) -> str:
    """What the plan says of the capacity it was made from (#39): the lines
    above the days, and the lines under the reason on the no-plan page; ""
    when none applies. In order, each only when it applies:

    - each pool's capacity source, on the no-plan page only when devagent's
      declaration is used: with no plan no other capacity is, so the page
      of a plan with nothing declared is unchanged and that of an invalid
      declaration names no source;
    - how devagent's weekly hours were laid on the weekdays, when they were
      scaled by a factor other than 1 or spread evenly;
    - one line per weekday that lost hours to its day's cap, in the plan's
      order (Monday first), a weekday that is no day of the plan included;
    - an invalid declaration's note, then devagent's violation lines;
    - devagent's unknown-key lines;
    - the declared ratio, when the planner plans it as rate 0 or refused it;
    - the token budget.

    A no_plan plan holds no attention record, so the no-plan page never
    has the second or the third."""
    capacity, planned = plan.capacity, plan.status == "planned"
    out = []
    if planned or capacity["declaration"] == planner.USED:
        parts = "; ".join(f"{p['name']}: {p['capacity_source']}" for p in plan.pools)
        out.append(_p("capacity", CAPACITY.format(parts=parts)))
    attention = capacity["attention"]
    if attention is not None:
        if attention["rule"] == planner.RULE_EVEN_SPREAD:
            out.append(_p("capacity", EVEN_SPREAD.format(total=hours_text(attention["total_hours"]))))
        elif attention["rule"] == planner.RULE_SCALED and attention["factor"] != 1:
            out.append(_p("capacity", SCALED.format(total=hours_text(attention["total_hours"]),
                                                    sum=hours_text(attention["weekday_sum_hours"]),
                                                    factor=factor_text(attention["factor"]))))
        for entry in attention["weekdays_lost_to_cap"]:
            weekday = entry["weekday"]
            if isinstance(weekday, str):
                weekday = WEEKDAY_OF.get(weekday, weekday)  # one the page does not know: as the plan has it
            out.append(_p("capacity", DAY_CAPPED.format(weekday=weekday, lost=hours_text(entry["hours"]))))
    if capacity["declaration"] == planner.INVALID:
        out.append(_p("capacity invalid", INVALID if planned else INVALID_NO_PLAN))
    for name, kind in (("capacity-violations", planner.CAPACITY_INVALID),
                       ("capacity-warnings", planner.CAPACITY_UNKNOWN_KEY)):
        items = "".join(f"<li>{esc(entry['text'])}</li>" for entry in plan.report if entry["kind"] == kind)
        if items:
            out.append(f'<ul id="{name}">{items}</ul>')
    ratio = capacity["declared_ratio"]
    if ratio is not None:
        if ratio["used"] and planner.is_rate_0(ratio["value"]):
            out.append(_p("capacity", RATIO_ZERO))
        elif not ratio["used"] and ratio["reason"] is not None:
            out.append(_p("capacity", ratio["reason"]))
    budget = capacity["token_budget"]
    if budget is not None:
        out.append(_p("capacity", TOKENS.format(n=budget["tokens_per_week"], unit=budget["unit"])))
    return "".join(out)


def _lines(page):
    plan = page.plan
    out = []
    if plan.as_of not in plan.horizon_days and plan.horizon_days:
        out.append(_p("not-working", _named(NOT_WORKING, plan.horizon_days[0], as_of=plan.as_of)))
    binding = plan.binding
    if binding["pool"] is None:
        text = NO_BINDING
    else:
        text = _named(BINDING, binding["first_day"], pool=binding["pool"])
    out.append(f'<p data-figure="binding" data-source="{esc(SOURCE.format(as_of=plan.as_of))}">{esc(text)}</p>')
    out.append(_p("rule", RULE.format(rule=plan.allocation_rule)))
    mapping = plan.mapping
    out.append(_p("mapping", MAPPING.format(values=", ".join(rate_text(v) for v in mapping["values"]),
                                            source=MAPPING_SOURCES.get(mapping["source"], mapping["source"]))))
    if mapping["rejected"] is not None:
        out.append(_p("mapping", MAPPING_REJECTED.format(reason=mapping["rejected"])))
    out.append(_capacity_notes(plan))
    n = plan.unestimated_planned
    count = _count_line(n, COUNT, COUNT_ONE) if n else ""
    out.append(f'<p data-unestimated="{esc(n)}">{esc(count)}</p>')
    shareless = [s for s in plan.sites if s["shareless_days"]]
    if shareless:
        out.append(_p("shareless", _count_line(len(shareless), SHARELESS, SHARELESS_ONE)))
        items = []
        for s in shareless:
            if s["shareless_days"] == plan.horizon_days:
                text = SHARELESS_ALL.format(site=s["name"])
            else:
                text = SHARELESS_DAYS.format(site=s["name"],
                                             days=", ".join(_named(DAY_HEAD, d) for d in s["shareless_days"]))
            items.append(f"<li>{esc(text)}</li>")
        out.append(f'<ul id="shareless">{"".join(items)}</ul>')
    if plan.notes:
        notes = "".join(f"<li>{esc(note['text'])}</li>" for note in plan.notes)
        out.append(f'<ul id="notes">{notes}</ul>')
    p0 = [(s["name"], entry) for s in plan.sites for entry in s["p0"]]
    if p0:
        items = []
        for site, entry in p0:
            held = (" " + _span("held", HELD_BY.format(holders=", ".join(entry["held_by"])))
                    if entry["held_by"] else "")
            items.append(f'<li class="p0-leaf" data-site="{esc(site)}" data-leaf="{esc(entry["key"])}">'
                         f'{esc(site)}: {page.link(site, entry["key"])}{held}</li>')
        out.append(f'<div id="p0"><ul>{"".join(items)}</ul><p>{esc(P0_CAPTION)}</p></div>')
    return f'<section id="lines">{"".join(out)}</section>'


def _unplanned(page):
    out = [f"<h2>{esc(UNPLANNED_HEAD)}</h2>"]
    for i, s in enumerate(page.plan.sites, start=1):
        name = s["name"]
        beyond = [e for e in s["unplanned"] if e["reason"] == BEYOND_HORIZON]
        others = [e for e in s["unplanned"] if e["reason"] != BEYOND_HORIZON]
        body = [f"<h3>{esc(name)}</h3>"]
        if beyond:
            n = len(beyond)
            body.append(f'<p data-beyond="{n}">{esc(_count_line(n, BEYOND, BEYOND_ONE))}</p>')
            items = "".join(f'<li class="{render.reason_class(BEYOND_HORIZON)}" data-unplanned="{BEYOND_HORIZON}" '
                            f'data-site="{esc(name)}" data-leaf="{esc(e["key"])}">{page.link(name, e["key"])}</li>'
                            for e in beyond)
            body.append(f'<button class="gone" data-control="beyond-{i}" data-value="toggle" aria-expanded="false">'
                        f'{esc(BEYOND_BUTTON)}</button>')
            body.append(f'<ul class="beyond-list" id="beyond-{i}">{items}</ul>')
        if others:
            items = []
            for e in others:
                reason = e["reason"]
                text = REASON_TEXTS.get(reason) if isinstance(reason, str) else None
                text = text or UNKNOWN_REASON.format(code=reason)
                items.append(f'<li class="{render.reason_class(reason)}" data-unplanned="{esc(reason)}" '
                             f'data-site="{esc(name)}" data-leaf="{esc(e["key"])}">'
                             f'{page.link(name, e["key"])} {_span("reason", text)}</li>')
            body.append(f'<ul>{"".join(items)}</ul>')
        if not s["unplanned"]:
            body.append(_p("none", NOTHING_UNPLANNED))
        out.append(f'<div class="site-unplanned" data-site="{esc(name)}">{"".join(body)}</div>')
    lines = "".join(f"<li>{esc(NOT_BUILT_LINE.format(name=shown(s.name), reason=s.reason))}</li>"
                    for s in _not_built(page.result))
    out.append(f'<ul id="not-built">{lines}</ul>')
    return f'<section id="unplanned">{"".join(out)}</section>'


def _per_site(page):
    plan = page.plan
    first = plan.horizon_days[0]
    heads = [SITE_HEAD, _named(SHARE_HEAD, first), plan.pools[0]["name"], plan.pools[1]["name"]]
    rows = []
    for s in plan.sites:
        share = s["shares"][0]["share"] if s["shares"] else None
        cells = [s["name"], NO_SHARE if share is None else share_text(share),
                 CHARGED.format(charged=hours_text(s["attention_hours_charged"]),
                                target=hours_text(s["attention_hours_target"])),
                 CHARGED.format(charged=hours_text(s["lane_hours_charged"]),
                                target=hours_text(s["lane_hours_target"]))]
        rows.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in cells) + "</tr>")
    head = "<tr>" + "".join(f"<th>{esc(h)}</th>" for h in heads) + "</tr>"
    return (f"<h2>{esc(PER_SITE_HEAD)}</h2>"
            f'<table id="per-site"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table>')


def _not_built(result):
    return [s for s in result.summaries if s.status != "built"]


def _body(page):
    plan = page.plan
    if plan.status != "planned":
        return f'<p id="no-plan">{esc(NO_PLAN.format(reason=plan.reason))}</p>{_capacity_notes(plan)}'
    placements = _placements(plan)
    conts = _conts(plan, page.records, placements)
    days = "".join(_day(page, day, conts) for day in plan.days)
    lines = _lines(page)
    buttons = "".join(f'<button class="gone" data-control="days" data-value="{value}" '
                      f'aria-pressed="{"true" if value == "5" else "false"}">{esc(label)}</button>'
                      for value, label in DAYS_BUTTONS)
    legend = (f'<ul id="legend"><li><i class="sw crit"></i>{esc(LEGEND_CRIT)}</li>'
              f'<li><i class="sw near"></i>{esc(LEGEND_NEAR)}</li><li>{esc(LEGEND_P0)}</li>'
              + "".join(f'<li>{item}</li>' for item in leafbox.phase_legend_items()) + '</ul>')
    return f'{lines}{legend}<div>{buttons}</div><div id="days">{days}</div>{_unplanned(page)}{_per_site(page)}'


def render_week(plan, result, stamp) -> str:
    """The week view for a build's plan and BuildResult, as one HTML text."""
    page = _Page(plan, result)
    body = _body(page)
    data = {"stamp": stamp, "plan": asdict(plan), "leaves": list(page.named.values()),
            "not_built": [[s.name, s.reason] for s in _not_built(result)]}
    return fill(render.asset("templates/week.html"), LEAF_COLOURS=leafbox.colours(), PHASE_CSS=leafbox.phases_css(),
                TITLE=TITLE,
                STAMP=render.stamp_html(stamp), BODY=Markup(body), DATA=embed_json(data), PAGE_JS=_script("static/week.js"))
