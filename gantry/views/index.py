"""The index: one line per site, in config order, and the build's warnings (#11).

render_index reads the BuildResult every view reads. A built site's line
links its page and gives its open leaves, its finish at the default lanes
and its report count; a site that was not built gives its reason. The index
has three slots. The strategic page fills two (#23, views/strategic.py's
index_slots): a headline and a per-site slot on each line, after a
separator. The list of links to other views holds the week view, week.html
(#38), and the strategic page, each when the build wrote it. Rendered with
nothing supplied, as when neither was written, the slots stay empty. There
is no page script. docs/worksite-page.md describes the index.
"""

from gantry import render
from gantry.links import page_href
from gantry.render import Markup, embed_json, esc, fill
from gantry.sites import shown
from gantry.stamp import public_text
from gantry.views.worksite import DOT, LANES_DEFAULT, SCHEDULE_LABEL, _count_line, _includes, devagent_line

TITLE = "Worksites"
FINISH_LABEL = "Finishes at {lanes} agent lanes: {label}"
OPEN_LEAVES = "{n} open leaves"
OPEN_LEAVES_ONE = "1 open leaf"
FINISH = "finishes working day {day}, {date}"
NO_FINISH = "nothing is scheduled"
REPORT = "{n} missing or unreadable"
REPORT_NONE = "nothing missing or unreadable"
NOT_BUILT = "not built: {reason}"
# One of devagent's shown report entries about a project with no page (#12), among the warnings.
UNPLACED = "{project}: {line}"
UNPLACED_KEY = "devagent"


def devagent_unplaced(result) -> list:
    """devagent's shown report entries whose project has no page in this build, in document
    order: derived from the final pages, after every re-render and write failure (#12)."""
    if result.document is None:
        return []
    return [entry for entry in result.document.devagent_report if entry.project not in result.pages]


def _part(cls, text):
    return f'<span class="{cls}">{esc(text)}</span>'


def _extra(value):
    return Markup(f'<span class="extra">{value if isinstance(value, Markup) else esc(value)}</span>')


def _finish(s):
    if s.finish_day is None:
        return NO_FINISH
    text = FINISH.format(day=s.finish_day, date=s.finish_date.isoformat())
    return f"{text}; {_includes(s.unestimated_count)}" if s.unestimated_count else text


def _site_line(s, extra):
    if s.status != "built":
        parts = [_part("name", shown(s.name)), _part("reason", NOT_BUILT.format(reason=s.reason))]
    else:
        opened = _count_line(s.open_leaves, OPEN_LEAVES, OPEN_LEAVES_ONE)
        report = REPORT.format(n=s.report_count) if s.report_count else REPORT_NONE
        parts = [f'<a href="{esc(page_href(s.name, from_top=True))}">{esc(s.name)}</a>',
                 _part("open", opened), _part("finish", _finish(s)), _part("report", report)]
    # A filled slot follows its own separator; an empty one adds nothing visible.
    return f"<li>{DOT.join(parts)}{DOT if extra else ''}{_extra(extra)}</li>"


def _data(result, warnings):
    return {
        "stamp": result.stamp,
        "as_of": result.config.as_of.isoformat(),
        "sites": [{"name": s.name, "status": s.status, "reason": s.reason, "open_leaves": s.open_leaves,
                   "finish_day": s.finish_day,
                   "finish_date": s.finish_date.isoformat() if s.finish_date is not None else None,
                   "unestimated_count": s.unestimated_count, "report_count": s.report_count}
                  for s in result.summaries],
        "warnings": [{"key": key, "text": text} for key, text in warnings],
    }


def render_index(result, *, headline="", extra=None, view_links=None) -> str:
    """The index page for a BuildResult, as one HTML text.

    `headline` and each `extra` value are text, or Markup a view built from
    escaped parts; `view_links` is a list of (label, relative href).
    """
    extra = extra or {}
    roots = result.roots
    # A warning names a path by its config key or file kind, as a reason does.
    warnings = [(w.key, public_text(w.message, roots)) for w in result.warnings]
    warnings += [(UNPLACED_KEY, public_text(UNPLACED.format(project=shown(e.project), line=devagent_line(e)), roots))
                 for e in devagent_unplaced(result)]
    return fill(
        render.asset("templates/index.html"),
        TITLE=TITLE,
        STAMP=render.stamp_html(result.stamp),
        HEADLINE=headline,
        WARNINGS=Markup("".join(f"<li>{esc(text)}</li>" for _, text in warnings)),
        FINISH_LABEL=FINISH_LABEL.format(lanes=LANES_DEFAULT, label=SCHEDULE_LABEL),
        SITES=Markup("".join(_site_line(s, extra.get(s.name, "")) for s in result.summaries)),
        VIEW_LINKS=Markup("".join(f'<li><a href="{esc(href)}">{esc(label)}</a></li>'
                                  for label, href in (view_links or []))),
        DATA=embed_json(_data(result, warnings)),
    )
