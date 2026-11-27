"""The index: one line per site, the warnings and the empty slots (#11), and
the slots the strategic page fills (#23).

Every literal is hand-derived from samples/ (see test_build's harbor note).
"""

import dataclasses
import json
import re
from datetime import date
from pathlib import Path

from commit_check import path_form_hits
import helpers_portfolio as hp
from gantry.build import build_all, write_all
from gantry.config import load_config
from gantry.render import Markup
from gantry.views import index, worksite
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
from helpers_config import (
    append_bytes,
    copy_samples,
    demo_build,
    replace_bytes,
    sample_build,
    toml_key,
    write_site_tree,
)
from page_safety import HOSTILE, assert_no_external_resources, assert_page_safe
from page_script.harness import open_page
from sample_roles import BUSY, NO_WBS, NO_WBS_REASON, OWN_UNIT, READABLE, SMALL, UNESTIMATED, UNREADABLE, UNREADABLE_REASON
from test_samples import ROOT
from test_worksite_page import by_id, find, text_of, tree


def site_lines(t):
    """{site name: (li index, its text)} in page order, keyed by the line's first span or link."""
    lines = {}
    for li in find(t, "li", within=by_id(t, "sites")):
        (head,) = find(t, "a", within=li) or find(t, "span", ("name",), within=li)
        lines[text_of(t, head)] = (li, text_of(t, li))
    return lines


def figure_of(t, kind, within):
    """The one element under `within` whose data-figure is `kind`."""
    (i,) = [i for i in find(t, "span", within=within) if t.elements[i]["attrs"].get("data-figure") == kind]
    return i


def headline_and_links(page):
    """(the headline's text, [(label, href)] of the view links) on an index page."""
    t = tree(page)
    links = find(t, "a", within=by_id(t, "view-links"))
    return text_of(t, by_id(t, "headline")), [(text_of(t, i), t.elements[i]["attrs"]["href"]) for i in links]


def test_index_label(tmp_path):
    page = render_index(sample_build(tmp_path))
    label = FINISH_LABEL.format(lanes=LANES_DEFAULT, label=SCHEDULE_LABEL)
    assert LANES_DEFAULT == 4
    assert page.count(label) == 1
    assert page.index(label) < page.index('id="sites"')


def test_site_line_fields(tmp_path):
    result = sample_build(tmp_path)
    t = tree(render_index(result))
    lines = site_lines(t)
    assert list(lines) == [s.name for s in result.summaries]
    li, text = lines[BUSY]
    # Hand-derived (test_build.test_summaries_on_samples): 9 open, day 12, 3 report lines, N = 0.
    assert text == f"{BUSY} · 9 open leaves · finishes working day 12, 2026-02-04 · 3 missing or unreadable"
    assert "includes" not in text
    quarry = {s.name: s for s in result.summaries}[UNESTIMATED]
    assert quarry.unestimated_count > 0
    (finish,) = find(t, "span", ("finish",), within=lines[UNESTIMATED][0])
    assert text_of(t, finish).endswith("; " + INCLUDES.format(n=quarry.unestimated_count))
    (link,) = find(t, "a", within=li)
    assert t.elements[link]["attrs"]["href"] == f"sites/{BUSY}.html"


def test_no_finish_line(tmp_path):
    result = build_all(load_config(write_site_tree(tmp_path, {"done": hp.DONE_SITE, "held": hp.HELD_SITE})))
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
    t = tree(render_index(sample_build(tmp_path)))
    lines = site_lines(t)
    for name, reason in ((NO_WBS, NO_WBS_REASON),
                         (UNREADABLE, UNREADABLE_REASON)):
        li, _ = lines[name]
        assert find(t, "a", within=li) == []
        (span,) = find(t, "span", ("reason",), within=li)
        assert text_of(t, span) == NOT_BUILT.format(reason=reason)
    assert len(find(t, "a", within=by_id(t, "sites"))) == len(READABLE)


def test_document_statuses_shown_with_their_reasons(tmp_path):
    """Each project status in the portfolio JSON a site agrees with (no WBS.md, no devdoc_dir,
    unreadable) is shown on the index as the document's words and reason (#12)."""
    import helpers_portfolio as hp
    from helpers_config import append_bytes, copy_samples
    from gantry.build import build_all
    from gantry.config import load_config

    copy = copy_samples(tmp_path)
    append_bytes(copy / "devagent" / "config.toml", "\n[project.nodir]\nx = 1\n")
    hp.rewrite_document(copy, lambda doc: hp.append_not_read(doc, "nodir", "no_devdoc_dir", hp.UNSET_REASON))
    t = tree(render_index(build_all(load_config(copy / "gantry.sample.toml"), samples=copy)))
    lines = site_lines(t)
    for name, reason in ((NO_WBS, NO_WBS_REASON), (UNREADABLE, UNREADABLE_REASON),
                         ("nodir", f"no devdoc_dir: {hp.UNSET_REASON}")):
        (span,) = find(t, "span", ("reason",), within=lines[name][0])
        assert text_of(t, span) == NOT_BUILT.format(reason=reason), name
    assert NO_WBS_REASON == f"no WBS.md: {hp.NO_WBS_REASON}"


def test_slots_present_and_empty(tmp_path):
    """render_index(result) with no strategic content supplied: #11's empty slots."""
    result = sample_build(tmp_path)
    t = tree(render_index(result))
    assert t.elements[by_id(t, "headline")]["text"] == []
    assert find(t, None, within=by_id(t, "headline")) == []
    assert find(t, None, within=by_id(t, "view-links")) == []
    extras = find(t, "span", ("extra",), within=by_id(t, "sites"))
    assert len(extras) == len(result.summaries)
    assert all(text_of(t, i) == "" for i in extras)


def test_slots_filled(tmp_path):
    result = sample_build(tmp_path)
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


# --- the strategic page's slots (#23) -----------------------------------------------------

HEADLINE = "Largest share gap: harbor, intended 50%, backlog share 31% (backlog share, not effort)"
PERCENT_RE = re.compile(r"([0-9]+)%")


def test_demo_index_strategic_slots(tmp_path, monkeypatch):
    from test_strategic_page import panel_of

    code, out, _, stderr = demo_build(tmp_path, monkeypatch)
    assert code == 0, stderr
    t = tree((out / "index.html").read_bytes().decode("utf-8"))
    headline = figure_of(t, "headline", by_id(t, "headline"))
    attrs = t.elements[headline]["attrs"]
    assert text_of(t, headline) == HEADLINE
    assert attrs["data-source"] and attrs["data-unestimated"] == "0"
    assert headline_and_links((out / "index.html").read_bytes().decode("utf-8"))[1] == [
        ("Week view", "week.html"), ("Strategic page", "strategic.html")]
    page = tree((out / "strategic.html").read_bytes().decode("utf-8"))
    lines = site_lines(t)
    for name in sorted(READABLE):
        share = figure_of(t, "index-share", lines[name][0])
        backlog = figure_of(page, "backlog", panel_of(page, name))
        assert PERCENT_RE.findall(text_of(t, share)) == PERCENT_RE.findall(text_of(page, backlog))[:1], name
        assert t.elements[share]["attrs"]["data-unestimated"] == page.elements[backlog]["attrs"]["data-unestimated"]
        assert t.elements[share]["attrs"]["data-source"], name
    unestimated = {name: t.elements[figure_of(t, "index-share", lines[name][0])]["attrs"]["data-unestimated"]
                   for name in READABLE}
    assert unestimated == {BUSY: "0", OWN_UNIT: "5", UNESTIMATED: "5", SMALL: "2"}
    for name in (NO_WBS, UNREADABLE):
        (extra,) = find(t, "span", ("extra",), within=lines[name][0])
        assert text_of(t, extra) == ""


def test_index_line_with_share(tmp_path, monkeypatch):
    plain = site_lines(tree(render_index(sample_build(tmp_path / "plain"))))[BUSY][1]
    _, out, _, _ = demo_build(tmp_path / "demo", monkeypatch)
    filled = site_lines(tree((out / "index.html").read_bytes().decode("utf-8")))
    assert filled[BUSY][1] == plain + worksite.DOT + "backlog share 31%"
    assert filled[OWN_UNIT][1].endswith(worksite.DOT + "backlog share 21% (includes 5 unestimated leaves)")
    assert filled[NO_WBS][1] == f"{NO_WBS}{worksite.DOT}not built: {NO_WBS_REASON}"


def written_headline_and_source(config):
    """(the written index's headline text, its data-source) for a full build of `config`."""
    result, _, error = write_all(build_all(config))
    assert error is None and result.strategic_error is None
    t = tree(Path(result.config.output_dir, "index.html").read_bytes().decode("utf-8"))
    headline = figure_of(t, "headline", by_id(t, "headline"))
    return text_of(t, headline), t.elements[headline]["attrs"]["data-source"]


def written_headline(config):
    text, source = written_headline_and_source(config)
    assert source
    return text


def test_headline_variants(tmp_path):
    from test_strategic import ALLOCATION, FINISHED, SAME
    from test_planner import install

    early = copy_samples(tmp_path / "early")
    replace_bytes(early / "gantry.sample.toml", "as_of = 2026-01-20", "as_of = 2025-11-30")
    assert written_headline_and_source(load_config(early / "gantry.sample.toml")) == (
        "no intended shares set", "the portfolio file")
    bare = copy_samples(tmp_path / "bare")
    replace_bytes(bare / "gantry.sample.toml", 'portfolio_file = "portfolio.md"\n', "")
    assert written_headline_and_source(load_config(bare / "gantry.sample.toml")) == (
        "no intended shares set", "no portfolio file configured")
    no_gap = ("Largest share gap: none, no built site has both a backlog share and a comparable intended share "
              "(backlog share, not effort)")
    for shares, wbs in (("a = 50, b = 50", FINISHED), ("a = 0, b = 0, ghost = 100", SAME)):
        copy = install(tmp_path, "states", wbs={"a": wbs, "b": wbs}, portfolio=ALLOCATION.format(shares=shares))
        assert written_headline_and_source(load_config(copy / "gantry.toml")) == (
            no_gap, "computed from open work and the portfolio file, allocation from 2026-01-01"), shares


def test_headline_gives_share_as_given_and_unestimated(tmp_path):
    """AC4's fixture through write_all: the built shares sum to 85, so the
    comparable share differs from the share as given. Gaps: harbor 10.52,
    orchard 14.07, quarry 16.45 (|7.08 - 23.53|)."""
    from test_strategic import samples
    from test_strategic_page import panel_of

    copy = samples(tmp_path, "harbor = 35, orchard = 30, quarry = 20, bakery = 10, ferry = 5")
    result, _, error = write_all(build_all(load_config(copy / "gantry.sample.toml"), samples=copy))
    assert error is None and result.strategic_error is None
    t = tree((copy / "out" / "index.html").read_bytes().decode("utf-8"))
    headline = figure_of(t, "headline", by_id(t, "headline"))
    assert text_of(t, headline) == ("Largest share gap: quarry, intended 20%, backlog share 7%, includes 5 unestimated "
                                    "leaves (backlog share, not effort)")
    assert t.elements[headline]["attrs"]["data-unestimated"] == "5"
    page = tree((copy / "out" / "strategic.html").read_bytes().decode("utf-8"))
    harbor = panel_of(page, BUSY)
    assert text_of(page, figure_of(page, "intended", harbor)) == "intended 35% (allocation from 2026-01-05)"
    assert text_of(page, figure_of(page, "gap", harbor)) == "gap 11 points against intended 41% rescaled over built sites"


def test_index_lines_with_no_open_work(tmp_path):
    from test_planner import install
    from test_strategic import ALLOCATION, FINISHED

    copy = install(tmp_path, "states", wbs={"a": FINISHED, "b": FINISHED}, portfolio=ALLOCATION.format(shares="a = 50, b = 50"))
    result, _, error = write_all(build_all(load_config(copy / "gantry.toml")))
    assert error is None and result.strategic_error is None
    t = tree(Path(result.config.output_dir, "index.html").read_bytes().decode("utf-8"))
    lines = site_lines(t)
    for name in ("a", "b"):
        assert lines[name][1].endswith(worksite.DOT + "no open work"), name
        share = figure_of(t, "index-share", lines[name][0])
        assert t.elements[share]["attrs"]["data-unestimated"] == "0"


def test_warnings_listed_at_top(tmp_path):
    page = hp.fixture_source("page")
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
    result = sample_build(tmp_path)
    page = render_index(result)
    data = assert_page_safe(page, [])
    assert data["stamp"] == result.stamp == "sample data as of 2026-01-20"
    assert data["as_of"] == "2026-01-20"
    harbor = {s["name"]: s for s in data["sites"]}[BUSY]
    assert (harbor["status"], harbor["open_leaves"], harbor["finish_date"]) == ("built", 9, "2026-02-04")
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


# --- page safety on the index -----------------------------------------------------------


def test_index_hostile(node, tmp_path, monkeypatch):
    page = hp.fixture_source("page")
    tail = f"[site.{toml_key(HOSTILE[0])}]\n"
    config = load_config(write_site_tree(tmp_path, {"alpha": page, HOSTILE[3]: page, "beta": page}, tail=tail))
    render_model = worksite.render_model

    def failing(m, **kw):
        if m["site"] == "beta":
            raise ValueError(HOSTILE[10])
        return render_model(m, **kw)

    monkeypatch.setattr(worksite, "render_model", failing)
    result, _, error = write_all(build_all(config))
    assert error is None
    assert result.strategic_error is None
    summaries = {s.name: s for s in result.summaries}
    reason = summaries["beta"].reason
    assert reason == "render error: ValueError: " + HOSTILE[10]
    assert summaries[HOSTILE[3]].status == "not_built"
    (warning,) = result.warnings
    text = (tmp_path / "out" / "index.html").read_bytes().decode("utf-8")
    shown_warning = f"the Gantry config: [site.{HOSTILE[0]}] names no project in the devagent config"
    assert warning.message.endswith(f"[site.{HOSTILE[0]}] names no project in {config.devagent_config}")
    assert_page_safe(text, [HOSTILE[3], shown_warning, reason])
    assert_no_external_resources(text)
    with open_page(tmp_path / "out" / "index.html") as live:
        planted = live.planted_nodes([HOSTILE[0], HOSTILE[3], HOSTILE[10]])
    assert planted.created == [] and planted.unrendered == []


def data_strings(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from data_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from data_strings(value)
    elif isinstance(node, str):
        yield node


def test_index_names_no_absolute_path(tmp_path):
    copy = copy_samples(tmp_path)
    config = copy / "gantry.sample.toml"
    # A site-list warning, and a trace warning: the namespace is a site that has requirements.
    append_bytes(config, '[site.ghost]\n[requirements]\nbr_namespace = "lighthouse"\n')
    result = build_all(load_config(config), samples=copy)
    assert {w.key for w in result.warnings} == {"site.ghost", "site.lighthouse.requirements"}
    assert {s.name for s in result.summaries if s.status != "built"} == {NO_WBS, UNREADABLE}
    page = render_index(result)
    data = assert_page_safe(page, [])
    texts = [page, *data_strings(data)]
    for text in texts:
        assert path_form_hits(text) == [], text[:80]
        assert str(tmp_path) not in text and tmp_path.as_posix() not in text
    assert len(data["warnings"]) == 2 and json.dumps(data)


def test_index_counts_cross_site_entries(tmp_path):
    from test_crosssite import model_of, stripped_build

    with_foreign, without = sample_build(tmp_path / "a"), stripped_build(tmp_path / "b")
    counts = {s.name: s.report_count for s in with_foreign.summaries}
    before = {s.name: s.report_count for s in without.summaries}
    for site, added in ((OWN_UNIT, 4), (UNESTIMATED, 1)):
        assert counts[site] == before[site] + added, site
        assert counts[site] == len(model_of(with_foreign, site)["panel"]), site


# --- the week view link (#38) -------------------------------------------------------------


def index_view_links(out):
    t = tree((out / "index.html").read_bytes().decode("utf-8"))
    return [(text_of(t, i), t.elements[i]["attrs"]["href"]) for i in find(t, "a", within=by_id(t, "view-links"))]


def test_demo_index_links_week_view(tmp_path, monkeypatch):
    from helpers_config import demo_build, lands

    code, out, _, stderr = demo_build(tmp_path, monkeypatch)
    assert code == 0, stderr
    assert index_view_links(out) == [("Week view", "week.html"), ("Strategic page", "strategic.html")]
    assert lands(out, "index.html", "week.html")


def test_no_plan_index_still_links_week(tmp_path):
    from helpers_config import lands, run_main
    from test_planner import install

    copy = install(tmp_path, "binds_lanes", config={"portfolio_file": None})
    code, _, stderr = run_main("--config", str(copy / "gantry.toml"))
    assert code == 0, stderr
    out = copy / "out"
    assert index_view_links(out) == [("Week view", "week.html"), ("Strategic page", "strategic.html")]
    assert lands(out, "index.html", "week.html")
    t = tree((out / "week.html").read_bytes().decode("utf-8"))
    assert text_of(t, by_id(t, "no-plan")) == "no week plan: no portfolio_file is configured"


# --- devagent's entries about a project with no page (#12) --------------------------------------


def _with_entries(folder, *entries):
    import json

    import helpers_portfolio as hp

    path = folder / "portfolio.json"
    doc = json.loads(path.read_bytes())
    doc["parse_report"] += [{"project": p, "source_line": line, "kind": kind, "message": message}
                            for p, line, kind, message in entries]
    path.write_bytes(hp.sp.dumps(doc))


def test_unplaced_devagent_entries_are_on_the_index(tmp_path):
    import helpers_portfolio as hp
    from helpers_config import run_main, write_site_tree
    from gantry.views.index import UNPLACED_KEY

    folder = tmp_path / "tree"
    config = write_site_tree(folder, {"alpha": hp.fixture_source("page"), "beta": None})
    _with_entries(folder, ("beta", None, "parser_note", "beta has no page"), ("alpha", 1, "parser_note", "on alpha"))
    code, _, err = run_main("--config", str(config))
    assert code == 0
    index = (folder / "out" / "index.html").read_bytes().decode("utf-8")
    t = tree(index)
    warnings = [text_of(t, li) for li in find(t, "li", within=by_id(t, "warnings"))]
    assert warnings == ["beta: devagent parser_note: beta has no page"]
    data = assert_page_safe(index, [])
    assert {"key": UNPLACED_KEY, "text": "beta: devagent parser_note: beta has no page"} in data["warnings"]
    assert ("gantry: warning: portfolio JSON: 1 devagent report entry names a project with no page; "
            "the index lists them") in err.splitlines()


def test_entry_for_a_site_that_loses_its_page_is_on_the_index(tmp_path, monkeypatch):
    """A site whose page fails to write loses its page after the render: its entries move to the index."""
    import helpers_portfolio as hp
    from gantry import build
    from gantry.build import build_all, write_all
    from gantry.config import load_config
    from helpers_config import write_site_tree

    folder = tmp_path / "tree"
    config = load_config(write_site_tree(folder, {"alpha": hp.fixture_source("page"), "beta": hp.fixture_source("page")}))
    _with_entries(folder, ("beta", 1, "parser_note", "on beta"))
    real = build.write_page

    def write_page(roots, where, name, text):
        if name == "beta":
            raise PermissionError(13, "Permission denied")
        return real(roots, where, name, text)

    monkeypatch.setattr(build, "write_page", write_page)
    result = build_all(config)
    assert "devagent parser_note: WBS.md line 1: on beta" in result.page_models["beta"]["panel"]
    result, _, error = write_all(result)
    assert error is None and "beta" not in result.pages
    t = tree((folder / "out" / "index.html").read_bytes().decode("utf-8"))
    assert [text_of(t, li) for li in find(t, "li", within=by_id(t, "warnings"))] == [
        "beta: devagent parser_note: WBS.md line 1: on beta"]
