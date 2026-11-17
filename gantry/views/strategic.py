"""The strategic page (#23): one panel per configured project, grouped under
the business requirements each built site serves, and the index's three
slots, all from one StrategicModel.

render_strategic writes the page; index_slots gives the index its headline,
each built site's backlog share and the link to the page. Every figure is a
span carrying data-figure (a kind from FIGURE_KINDS), data-source and, for
the kinds that count unestimated leaves, data-unestimated; on this page a
visible source span follows it. Record text reaches the page only through
esc, fill and embed_json; a not-built name goes through shown(). There is no
page script: the one script element is the gantry-data JSON block.
docs/strategic-page.md states the page.
"""

from gantry import render
from gantry.portfolio_file import PORTFOLIO_LABEL, format_entry
from gantry.render import Markup, embed_json, esc, fill
from gantry.sites import shown
from gantry.views.index import NOT_BUILT, OPEN_LEAVES, OPEN_LEAVES_ONE, REPORT_NONE
from gantry.views.worksite import DOT, _count_line, _includes, _num1, _round

TITLE = "Strategic page"
PLANS_ONLY = "plans and estimates only"
NO_PORTFOLIO = "no portfolio file configured"
TOTAL = "Open work across built sites: {hours} h{inc}"
SITE_ONLY_NOTE = "Site-only units are left out of totals across sites."
OFF_PAGE = "Intended share outside the gap comparison: {parts}."
OFF_NOT_BUILT = "{pct} on projects not built"
OFF_NOT_IN_CONFIG = "{pct} on projects not in devagent's config"
GAPS_NOTE = "Gaps compare each intended share rescaled over built sites."
OVERLAP_NOTE = "Groups overlap when a site serves several requirements, so group sums do not add up to the total."
GROUP_BR = "{id}: {text}"
GROUP_NO_REQUIREMENT = "Serves no listed requirement"
GROUP_NO_SHARE = "No intended share set"
GROUP_NOT_BUILT = "Not built"
NO_BUILT_SITE = "no built site serves this requirement"
SUM_INTENDED = "intended {pct} as given"
SUM_BACKLOG = "backlog share {pct}"
NOTHING_TO_SUM = "nothing to sum"
OPEN_WORK = "{leaves}, {hours} h{inc}"
HELD_OUT = "; {n} held out by a cycle, not in hours"
NON_TIME = "also {quantity} {unit} in this site's own unit, left out of totals across sites; {count}, {parts}"
NON_TIME_LEAVES = "{n} leaves"
NON_TIME_LEAF = "1 leaf"
NON_TIME_DEFAULT = "at the default unit (counted as unestimated)"
NON_TIME_FILE = "at estimates-file hours"
INTENDED = "intended {pct} (allocation from {date})"
INTENDED_NONE = "no intended share set"
BACKLOG = "backlog share, not effort: {pct}{inc}"
NO_OPEN_WORK = "no open work"
GAP = "gap {points} points against intended {comparable} rescaled over built sites"
NOT_BUILT_BACKLOG = "no page, so no open work counted"
NOT_BUILT_SERVES = "names {ids} in serves"
REPORT_HEADING = "missing or unreadable"
HEADLINE = "Largest share gap: {site}, intended {intended}, backlog share {backlog}{inc} (backlog share, not effort)"
HEADLINE_NO_SHARES = "no intended shares set"
HEADLINE_NO_GAP = ("Largest share gap: none, no built site has both a backlog share and a comparable intended share "
                   "(backlog share, not effort)")
INDEX_SHARE = "backlog share {pct}"
VIEW_LINK = ("Strategic page", "strategic.html")

SOURCE_HOURS = "WBS.md and {name}"
SOURCE_HOURS_NO_FILE = "WBS.md, no estimates file read"
SOURCE_INTENDED = "the portfolio file, allocation from {date}"
SOURCE_COMPUTED = "computed from open work"
SOURCE_TOTAL = "each built site's WBS.md and estimates file"
SOURCE_HEADLINE = "computed from open work and the portfolio file, allocation from {date}"
SOURCE_GROUP_NONE = "no intended or backlog share in this group"

# Every text the page or the index can show; docs/strategic-page.md states each one.
STRATEGIC_TEXTS = (
    TITLE, PLANS_ONLY, NO_PORTFOLIO, TOTAL, SITE_ONLY_NOTE, OFF_PAGE, OFF_NOT_BUILT, OFF_NOT_IN_CONFIG, GAPS_NOTE,
    OVERLAP_NOTE, GROUP_BR, GROUP_NO_REQUIREMENT, GROUP_NO_SHARE, GROUP_NOT_BUILT, NO_BUILT_SITE, SUM_INTENDED,
    SUM_BACKLOG, NOTHING_TO_SUM, OPEN_WORK, HELD_OUT, NON_TIME, NON_TIME_LEAVES, NON_TIME_LEAF, NON_TIME_DEFAULT,
    NON_TIME_FILE, INTENDED, INTENDED_NONE, BACKLOG, NO_OPEN_WORK, GAP, NOT_BUILT_BACKLOG, NOT_BUILT_SERVES,
    REPORT_HEADING, HEADLINE, HEADLINE_NO_SHARES, HEADLINE_NO_GAP, INDEX_SHARE, VIEW_LINK[0], SOURCE_HOURS,
    SOURCE_HOURS_NO_FILE, SOURCE_INTENDED, SOURCE_COMPUTED, SOURCE_TOTAL, SOURCE_HEADLINE, SOURCE_GROUP_NONE,
)

FIGURE_KINDS = ("hours", "intended", "backlog", "gap", "total", "group-sum", "headline", "index-share")
GROUP_CLASSES = {"br": "g-br", "no_requirement": "g-no-requirement", "no_share": "g-no-share",
                 "not_built": "g-not-built"}
GROUP_TITLES = {"no_requirement": GROUP_NO_REQUIREMENT, "no_share": GROUP_NO_SHARE, "not_built": GROUP_NOT_BUILT}


def pct(x) -> str:
    """A share as a whole percent, half up."""
    return f"{_round(x)}%"


def _inc(n) -> str:
    return f", {_includes(n)}" if n else ""


def figure(kind, text, source, unestimated=None, *, visible_source=True) -> Markup:
    """A figure's span, then (on this page) its visible source."""
    assert kind in FIGURE_KINDS, kind
    count = "" if unestimated is None else f' data-unestimated="{int(unestimated)}"'
    out = f'<span data-figure="{kind}" data-source="{esc(source)}"{count}>{esc(text)}</span>'
    if visible_source:
        out += f'<span class="source">{esc(source)}</span>'
    return Markup(out)


def _date(model) -> str:
    return model.allocation.from_date.isoformat()


# --- the panels ------------------------------------------------------------------------


def open_work_text(panel) -> str:
    b = panel.backlog
    text = OPEN_WORK.format(leaves=_count_line(panel.open_leaves, OPEN_LEAVES, OPEN_LEAVES_ONE),
                            hours=_num1(b.hours), inc=_inc(b.unestimated_count))
    return text + HELD_OUT.format(n=panel.held_out) if panel.held_out else text


def non_time_texts(backlog) -> list[str]:
    """One line per site-only unit, in the order the site's leaves name them."""
    out = []
    for unit, entry in backlog.non_time.items():
        split = backlog.non_time_split.get(unit, {})
        parts, first = [], True
        for n, what in ((split.get("default", 0), NON_TIME_DEFAULT), (split.get("estimates_file", 0), NON_TIME_FILE)):
            if n:
                parts.append(f"{n} of them {what}" if first else f"{n} {what}")
                first = False
        count = _count_line(entry["leaves"], NON_TIME_LEAVES, NON_TIME_LEAF)
        out.append(NON_TIME.format(quantity=f"{entry['quantity']:g}", unit=unit, count=count, parts=", ".join(parts)))
    return out


def _intended(model, panel) -> Markup | str:
    if panel.intended is None or model.allocation is None:
        return INTENDED_NONE
    date = _date(model)
    return figure("intended", INTENDED.format(pct=pct(panel.intended), date=date), SOURCE_INTENDED.format(date=date))


def _p(content) -> str:
    return f"<p>{content if isinstance(content, Markup) else esc(content)}</p>"


def _panel(model, panel) -> str:
    rows = []
    if panel.built:
        b = panel.backlog
        source = SOURCE_HOURS.format(name=panel.estimates) if panel.estimates is not None else SOURCE_HOURS_NO_FILE
        rows.append(f'<a class="name" href="{esc(panel.href)}">{esc(panel.site)}</a>')
        rows.append(_p(figure("hours", open_work_text(panel), source, b.unestimated_count)))
        rows += [_p(text) for text in non_time_texts(b)]
        rows.append(_p(_intended(model, panel)))
        if b.share is None:
            rows.append(_p(NO_OPEN_WORK))
        else:
            rows.append(_p(figure("backlog", BACKLOG.format(pct=pct(b.share), inc=_inc(b.unestimated_count)),
                                  SOURCE_COMPUTED, b.unestimated_count)))
        if panel.gap is not None:
            rows.append(_p(figure("gap", GAP.format(points=_round(panel.gap), comparable=pct(panel.comparable)),
                                  SOURCE_COMPUTED)))
    else:
        rows.append(f'<span class="name">{esc(shown(panel.site))}</span>{esc(DOT)}'
                    f'<span class="reason">{esc(NOT_BUILT.format(reason=panel.reason))}</span>')
        rows.append(_p(_intended(model, panel)))
        if panel.serves:
            rows.append(_p(NOT_BUILT_SERVES.format(ids=", ".join(panel.serves))))
        rows.append(_p(NOT_BUILT_BACKLOG))
    return f'<li class="panel">{"".join(rows)}</li>'


def _group_sum(model, group) -> Markup:
    parts, sources = [], []
    if group.intended_sum is not None:
        parts.append(SUM_INTENDED.format(pct=pct(group.intended_sum)))
        sources.append(SOURCE_INTENDED.format(date=_date(model)))
    if group.backlog_sum is not None:
        parts.append(SUM_BACKLOG.format(pct=pct(group.backlog_sum)))
        sources.append(SOURCE_COMPUTED)
    if not parts:
        return figure("group-sum", NOTHING_TO_SUM, SOURCE_GROUP_NONE, group.unestimated)
    return figure("group-sum", "; ".join(parts), "; ".join(sources), group.unestimated)


def _group(model, group) -> str:
    title = GROUP_BR.format(id=group.br_id, text=group.text) if group.kind == "br" else GROUP_TITLES[group.kind]
    head = f'<h2>{esc(title)} {_group_sum(model, group)}</h2>'
    if group.sites:
        body = f'<ul>{"".join(_panel(model, model.panels[name]) for name in group.sites)}</ul>'
    else:
        body = _p(NO_BUILT_SITE)
    return f'<section class="group {GROUP_CLASSES[group.kind]}">{head}{body}</section>'


# --- the page's lines ------------------------------------------------------------------


def _notes(model) -> str:
    notes = []
    if not model.configured:
        notes.append(NO_PORTFOLIO)
    if any(g.kind == "br" for g in model.groups):
        notes.append(OVERLAP_NOTE)
    return "".join(f"<li>{esc(text)}</li>" for text in notes)


def _totals(model) -> str:
    text = TOTAL.format(hours=_num1(model.total_hours), inc=_inc(model.total_unestimated))
    return f"{figure('total', text, SOURCE_TOTAL, model.total_unestimated)} {esc(SITE_ONLY_NOTE)}"


def off_page_text(model) -> str | None:
    parts = []
    if model.off_not_built > 0:
        parts.append(OFF_NOT_BUILT.format(pct=pct(model.off_not_built)))
    if model.off_not_in_config > 0:
        parts.append(OFF_NOT_IN_CONFIG.format(pct=pct(model.off_not_in_config)))
    return OFF_PAGE.format(parts=" and ".join(parts)) if parts else None


def _off_page(model) -> str:
    lines = [off_page_text(model)] if off_page_text(model) is not None else []
    if model.allocation is not None:
        lines.append(GAPS_NOTE)
    return esc(" ".join(lines))


def section_label(entry) -> str:
    return entry.section if entry.section is not None else PORTFOLIO_LABEL


def report_line(entry) -> str:
    return f"{section_label(entry)}: {format_entry(entry)}"


def _report(model) -> str:
    if not model.report:
        return f"<li>{esc(REPORT_NONE)}</li>"
    return "".join(f"<li>{esc(report_line(e))}</li>" for e in model.report)


def _data(result, model) -> dict:
    def panel(p):
        b = p.backlog
        return {"site": p.site, "status": "built" if p.built else "not_built", "reason": p.reason,
                "open_leaves": p.open_leaves, "held_out": p.held_out, "hours": b.hours if b else None,
                "unestimated_count": b.unestimated_count if b else None, "non_time": b.non_time if b else None,
                "non_time_split": b.non_time_split if b else None, "estimates": p.estimates,
                "intended": p.intended, "backlog_share": b.share if b else None, "comparable": p.comparable,
                "gap": p.gap, "serves": list(p.serves)}

    allocation = model.allocation
    return {
        "stamp": result.stamp,
        "as_of": model.as_of.isoformat(),
        "portfolio": "configured" if model.configured else "not configured",
        "allocation": None if allocation is None else {"from": allocation.from_date.isoformat(),
                                                       "shares": dict(allocation.shares)},
        "projects": list(model.projects),
        "groups": [{"kind": g.kind, "id": g.br_id, "text": g.text, "sites": list(g.sites)} for g in model.groups],
        "panels": [panel(p) for p in model.panels.values()],
        "totals": {"hours": model.total_hours, "unestimated_count": model.total_unestimated,
                   "not_built_share": model.off_not_built, "not_in_config_share": model.off_not_in_config},
        "report": [{"section": section_label(e), "line": e.line, "kind": e.kind, "key_path": e.key_path,
                    "message": e.message} for e in model.report],
    }


def render_strategic(result, model) -> str:
    """The strategic page for a BuildResult and its StrategicModel, as one HTML text."""
    return fill(
        render.asset("templates/strategic.html"),
        TITLE=TITLE,
        STAMP=render.stamp_html(result.stamp),
        PLANS_ONLY=PLANS_ONLY,
        NOTES=Markup(_notes(model)),
        TOTALS=Markup(_totals(model)),
        OFF_PAGE=Markup(_off_page(model)),
        GROUPS=Markup("".join(_group(model, g) for g in model.groups)),
        REPORT_HEADING=REPORT_HEADING,
        REPORT=Markup(_report(model)),
        DATA=embed_json(_data(result, model)),
    )


# --- the index's slots ---------------------------------------------------------------


def _headline(model) -> Markup:
    h = model.headline
    if h is not None:
        b = h.backlog
        text = HEADLINE.format(site=h.site, intended=pct(h.intended), backlog=pct(b.share),
                               inc=_inc(b.unestimated_count))
        return figure("headline", text, SOURCE_HEADLINE.format(date=_date(model)), b.unestimated_count,
                      visible_source=False)
    text = HEADLINE_NO_SHARES if model.allocation is None else HEADLINE_NO_GAP
    if model.allocation is not None:
        source = SOURCE_HEADLINE.format(date=_date(model))
    else:
        source = PORTFOLIO_LABEL if model.configured else NO_PORTFOLIO
    return figure("headline", text, source, 0, visible_source=False)


def index_share_text(backlog) -> str:
    if backlog.share is None:
        return NO_OPEN_WORK
    text = INDEX_SHARE.format(pct=pct(backlog.share))
    return f"{text} ({_includes(backlog.unestimated_count)})" if backlog.unestimated_count else text


def index_slots(model) -> dict:
    """render_index's keyword arguments: the headline, each built site's
    backlog share and the link to this page."""
    extra = {name: figure("index-share", index_share_text(b), SOURCE_COMPUTED, b.unestimated_count,
                          visible_source=False) for name, b in model.backlog.items()}
    return {"headline": _headline(model), "extra": extra, "view_links": [VIEW_LINK]}
