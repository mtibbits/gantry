"""The index: one line per site, the warnings and the empty slots (#11).

Every literal is hand-derived from samples/ (see test_build's harbor note).
"""

import dataclasses
from datetime import date

from gantry.build import build_all
from gantry.config import load_config
from gantry.render import Markup
from gantry.views import index
from gantry.views.index import (
    FINISH,
    FINISH_LABEL,
    NO_FINISH,
    NOT_BUILT,
    OPEN_LEAVES,
    REPORT,
    REPORT_NONE,
    render_index,
)
from gantry.views.worksite import INCLUDES, LANES_DEFAULT, SCHEDULE_LABEL
from helpers_config import copy_samples, write_site_tree
from page_safety import assert_no_external_resources, assert_page_safe
from sample_roles import BUSY, NO_WBS, READABLE, UNESTIMATED, UNREADABLE
from test_samples import ROOT
from test_worksite_page import find, text_of, tree

FIXTURES = ROOT / "tests" / "fixtures" / "wbs"


def sample_result(tmp_path):
    copy = copy_samples(tmp_path)
    return build_all(load_config(copy / "gantry.sample.toml"), samples=copy)


def by_id(t, ident):
    (i,) = [i for i, el in enumerate(t.elements) if el["attrs"].get("id") == ident]
    return i


def site_lines(t):
    """{site name: (li index, its text)} in page order, keyed by the line's first span or link."""
    lines = {}
    for li in find(t, "li", within=by_id(t, "sites")):
        (head,) = find(t, "a", within=li) or find(t, "span", ("name",), within=li)
        lines[text_of(t, head)] = (li, text_of(t, li))
    return lines


def test_index_label(tmp_path):
    page = render_index(sample_result(tmp_path))
    label = FINISH_LABEL.format(lanes=LANES_DEFAULT, label=SCHEDULE_LABEL)
    assert LANES_DEFAULT == 4
    assert page.count(label) == 1
    assert page.index(label) < page.index('id="sites"')


def test_site_line_fields(tmp_path):
    result = sample_result(tmp_path)
    t = tree(render_index(result))
    lines = site_lines(t)
    assert list(lines) == [s.name for s in result.summaries]
    li, text = lines[BUSY]
    # Hand-derived (test_build.test_summaries_on_samples): 8 open, day 12, 1 report line, N = 0.
    assert text == f"{BUSY} · 8 open leaves · finishes working day 12, 2026-02-04 · 1 missing or unreadable"
    assert "includes" not in text
    quarry = {s.name: s for s in result.summaries}[UNESTIMATED]
    assert quarry.unestimated_count > 0
    (finish,) = find(t, "span", ("finish",), within=lines[UNESTIMATED][0])
    assert text_of(t, finish).endswith("; " + INCLUDES.format(n=quarry.unestimated_count))
    (link,) = find(t, "a", within=li)
    assert t.elements[link]["attrs"]["href"] == f"sites/{BUSY}.html"


def test_no_finish_line(tmp_path):
    done = b"- [x] Done {issue: Issue-1}\n"
    held = b"- [ ] A {issue: Issue-1, depends_on: [Issue-2]}\n- [ ] B {issue: Issue-2, depends_on: [Issue-1]}\n"
    result = build_all(load_config(write_site_tree(tmp_path, {"done": done, "held": held})))
    t = tree(render_index(result))
    summaries = {s.name: s for s in result.summaries}
    assert summaries["held"].open_leaves == 2
    for name, (li, text) in site_lines(t).items():
        assert summaries[name].finish_day is None
        (finish,) = find(t, "span", ("finish",), within=li)
        assert text_of(t, finish) == NO_FINISH
        (report,) = find(t, "span", ("report",), within=li)
        assert text_of(t, report) == REPORT.format(n=summaries[name].report_count)
    # The extremes no sample reaches: nothing to report, and a plan inside day 0.
    edge = dataclasses.replace(summaries["done"], report_count=0, finish_day=0, finish_date=result.config.as_of)
    t = tree(render_index(dataclasses.replace(result, summaries=(edge,))))
    ((li, text),) = site_lines(t).values()
    assert text_of(t, find(t, "span", ("report",), within=li)[0]) == REPORT_NONE
    assert text_of(t, find(t, "span", ("finish",), within=li)[0]) == FINISH.format(
        day=0, date=result.config.as_of.isoformat())


def test_not_built_lines_show_reason(tmp_path):
    t = tree(render_index(sample_result(tmp_path)))
    lines = site_lines(t)
    for name, reason in ((NO_WBS, "no WBS.md in the planning directory"),
                         (UNREADABLE, "WBS.md is not valid UTF-8 at byte 88")):
        li, _ = lines[name]
        assert find(t, "a", within=li) == []
        (span,) = find(t, "span", ("reason",), within=li)
        assert text_of(t, span) == NOT_BUILT.format(reason=reason)
    assert len(find(t, "a", within=by_id(t, "sites"))) == len(READABLE)


def test_slots_present_and_empty(tmp_path):
    result = sample_result(tmp_path)
    t = tree(render_index(result))
    assert t.elements[by_id(t, "headline")]["text"] == []
    assert find(t, None, within=by_id(t, "headline")) == []
    assert find(t, None, within=by_id(t, "view-links")) == []
    extras = find(t, "span", ("extra",), within=by_id(t, "sites"))
    assert len(extras) == len(result.summaries)
    assert all(text_of(t, i) == "" for i in extras)


def test_slots_filled(tmp_path):
    result = sample_result(tmp_path)
    page = render_index(result, headline="<b>Plan</b>", extra={BUSY: Markup("<b>40%</b>"), NO_WBS: "<i>x</i>"},
                        view_links=[("Week", "week.html"), ("Strategy", "strategy.html")])
    t = tree(page)
    assert text_of(t, by_id(t, "headline")) == "<b>Plan</b>"
    assert find(t, "b", within=by_id(t, "headline")) == []
    lines = site_lines(t)
    (extra,) = find(t, "span", ("extra",), within=lines[BUSY][0])
    assert len(find(t, "b", within=extra)) == 1
    (escaped,) = find(t, "span", ("extra",), within=lines[NO_WBS][0])
    assert text_of(t, escaped) == "<i>x</i>" and find(t, "i", within=escaped) == []
    links = find(t, "a", within=by_id(t, "view-links"))
    assert [(text_of(t, i), t.elements[i]["attrs"]["href"]) for i in links] == [
        ("Week", "week.html"), ("Strategy", "strategy.html")]
    assert_no_external_resources(page)
    assert_page_safe(page, [])


def test_warnings_listed_at_top(tmp_path):
    page = (FIXTURES / "page.md").read_bytes()
    tail = '[site.ghost]\n[site.life]\nrequirements = "l.md"\n'
    result = build_all(load_config(write_site_tree(tmp_path, {"life": page, "alpha": page}, tail=tail)))
    assert [w.key for w in result.warnings] == ["site.ghost", "site.life.requirements"]
    html = render_index(result)
    t = tree(html)
    texts = [text_of(t, i) for i in find(t, "li", within=by_id(t, "warnings"))]
    assert len(texts) == 2
    assert all(text.startswith("the Gantry config: ") for text in texts)
    assert texts[0] == "the Gantry config: [site.ghost] names no project in the devagent config"
    assert "site.life.requirements" in texts[1]
    assert html.index('id="warnings"') < html.index('id="sites"')
    assert str(tmp_path) not in html


def test_index_data_and_stamp(tmp_path):
    result = sample_result(tmp_path)
    page = render_index(result)
    data = assert_page_safe(page, [])
    assert data["stamp"] == result.stamp == "sample data as of 2026-01-20"
    assert data["as_of"] == "2026-01-20"
    harbor = {s["name"]: s for s in data["sites"]}[BUSY]
    assert (harbor["status"], harbor["open_leaves"], harbor["finish_date"]) == ("built", 8, "2026-02-04")
    t = tree(page)
    (stamp,) = find(t, "p", ("stamp",))
    assert text_of(t, stamp) == result.stamp
    assert date.fromisoformat(harbor["finish_date"]).weekday() < 5


INDEX_TEXTS = ["TITLE", "FINISH_LABEL", "OPEN_LEAVES", "OPEN_LEAVES_ONE", "FINISH", "NO_FINISH", "REPORT",
               "REPORT_NONE", "NOT_BUILT"]


def test_index_doc_states_texts():
    doc = (ROOT / "docs" / "worksite-page.md").read_bytes().decode("utf-8")
    missing = [name for name in INDEX_TEXTS if f"`{getattr(index, name)}`" not in doc]
    assert missing == []
    assert OPEN_LEAVES.format(n=2) == "2 open leaves"
