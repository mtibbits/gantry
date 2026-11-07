"""The full build: anchors and hrefs, reading and rendering every site, and
writing the pages and the index (#11)."""

import re
from datetime import date

import pytest

from gantry import build
from gantry.build import CLASH, build_all, working_day
from gantry.config import load_config
from gantry.links import leaf_anchor, leaf_href, page_href
from gantry.views import worksite
from helpers_config import copy_samples, sample_projects, snapshot, write_site_tree
from page_safety import HOSTILE
from sample_roles import BUSY, NO_WBS, READABLE, UNESTIMATED, UNREADABLE
from test_samples import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "wbs"

# --- anchors and hrefs ------------------------------------------------------------


def test_leaf_anchor_forms():
    assert leaf_anchor("Issue-7") == "leaf-Issue-7"
    assert leaf_anchor("2.1~9") == "leaf-2.1~9"
    assert leaf_anchor("a b<c>\"&'") == "leaf-a%20b%3Cc%3E%22%26%27"
    assert leaf_anchor("é") == "leaf-%C3%A9"
    assert leaf_anchor("%") == "leaf-%25"


def test_leaf_anchor_hostile():
    for text in HOSTILE:
        assert re.fullmatch(r"leaf-[A-Za-z0-9_.~%-]+", leaf_anchor(text)), text


def test_href_forms():
    assert leaf_href("harbor", "Issue-7", from_top=False) == "harbor.html#leaf-Issue-7"
    assert leaf_href("harbor", "Issue-7", from_top=True) == "sites/harbor.html#leaf-Issue-7"
    assert page_href("harbor", from_top=True) == "sites/harbor.html"
    assert page_href("harbor", from_top=False) == "harbor.html"


# --- reading, then rendering, every site ---------------------------------------------


def test_working_day():
    tue, sat = date(2026, 1, 20), date(2026, 1, 24)
    # Day 1 is the as-of date itself: a plan that fills days 0 to 2 ends on Thursday.
    assert working_day(tue, 0) == tue
    assert working_day(tue, 1) == tue
    assert working_day(tue, 3) == date(2026, 1, 22)
    assert working_day(tue, 4) == date(2026, 1, 23)
    assert working_day(tue, 5) == date(2026, 1, 26)
    assert working_day(tue, 14) == date(2026, 2, 6)
    # No floor: a weekend as-of date stays itself, and the next working day is Monday.
    assert working_day(sat, 0) == sat
    assert working_day(sat, 1) == sat
    assert working_day(sat, 2) == date(2026, 1, 26)


def sample_build(tmp_path, **kw):
    copy = copy_samples(tmp_path)
    return build_all(load_config(copy / "gantry.sample.toml"), samples=copy, **kw)


def test_summaries_on_samples(tmp_path):
    result = sample_build(tmp_path)
    summaries = {s.name: s for s in result.summaries}
    assert [s.name for s in result.summaries] == sample_projects()
    assert {n for n, s in summaries.items() if s.status == "built"} == READABLE
    assert (summaries[NO_WBS].status, summaries[NO_WBS].reason) == ("not_built", "no WBS.md in the planning directory")
    assert (summaries[UNREADABLE].status, summaries[UNREADABLE].reason) == (
        "not_built", "WBS.md is not valid UTF-8 at byte 88")
    assert summaries[NO_WBS].open_leaves is None and summaries[NO_WBS].finish_date is None
    # Hand-derived from samples/harbor (WBS.md and its estimates file, 4 lanes,
    # 6 agent-hours a day): every open leaf has file hours, the plan ends at
    # 70 h, in working day 12 (Wednesday 2026-02-04), and the only report line
    # is Issue-14's missing priority.
    harbor = summaries[BUSY]
    assert (harbor.open_leaves, harbor.finish_day, harbor.finish_date, harbor.report_count,
            harbor.unestimated_count) == (8, 12, date(2026, 2, 4), 1, 0)
    assert summaries[UNESTIMATED].unestimated_count > 0
    for name in READABLE:
        s, inputs = summaries[name], result.inputs[name]
        assert s.reason is None
        assert s.open_leaves == sum(leaf.open for leaf in inputs.data.leaves)
        assert (s.unestimated_count, s.hours, s.non_time) == (
            inputs.totals.unestimated_count, inputs.totals.hours, inputs.totals.non_time)
        assert s.finish_date == working_day(result.config.as_of, s.finish_day)
        assert result.pages[name].startswith("<!doctype html>\n")
    assert set(result.pages) == READABLE


def test_reads_all_before_rendering(tmp_path, monkeypatch):
    calls = []
    real_read, real_render = build.read_site, build.render_site

    def read(site, *args, **kw):
        calls.append(("read", site.name))
        return real_read(site, *args, **kw)

    def render(name, *args, **kw):
        calls.append(("render", name))
        return real_render(name, *args, **kw)

    monkeypatch.setattr(build, "read_site", read)
    monkeypatch.setattr(build, "render_site", render)
    sample_build(tmp_path)
    kinds = [kind for kind, _ in calls]
    assert kinds == ["read"] * len(sample_projects()) + ["render"] * len(READABLE)


def page_tree(tmp_path, names):
    page = (FIXTURES / "page.md").read_bytes()
    return load_config(write_site_tree(tmp_path, {name: page for name in names}))


def test_case_clash_first_built_second_listed(tmp_path):
    result = build_all(page_tree(tmp_path, ["alpha", "Alpha"]))
    summaries = {s.name: s for s in result.summaries}
    assert summaries["alpha"].status == "built"
    assert (summaries["Alpha"].status, summaries["Alpha"].reason) == ("not_built", CLASH.format(earlier="alpha"))
    assert list(result.pages) == ["alpha"]


@pytest.mark.parametrize("error, reason", [
    (ValueError("bad value"), "render error: ValueError: bad value"),
    (ValueError(), "render error: ValueError"),
])
def test_render_error_isolated(tmp_path, monkeypatch, error, reason):
    real = worksite.render_model

    def render_model(m, **kw):
        if m["site"] == "beta":
            raise error
        return real(m, **kw)

    monkeypatch.setattr(worksite, "render_model", render_model)
    result = build_all(page_tree(tmp_path, ["alpha", "beta"]))
    summaries = {s.name: s for s in result.summaries}
    assert summaries["alpha"].status == "built"
    assert (summaries["beta"].status, summaries["beta"].reason) == ("not_built", reason)


def test_render_error_names_no_path(tmp_path, monkeypatch):
    def render_model(m, **kw):
        raise OSError(2, "No such file or directory", str(tmp_path / "secret" / "x.html"))

    monkeypatch.setattr(worksite, "render_model", render_model)
    result = build_all(page_tree(tmp_path, ["alpha"]))
    (summary,) = result.summaries
    assert summary.reason == "render error: FileNotFoundError: No such file or directory"
    # A message that quotes a configured root names it by its key instead.
    monkeypatch.setattr(worksite, "render_model", lambda m, **kw: (_ for _ in ()).throw(
        ValueError(f"cannot use {result.config.output_dir}")))
    (summary,) = build_all(result.config).summaries
    assert summary.reason == "render error: ValueError: cannot use output_dir"
    assert str(tmp_path) not in summary.reason


def test_build_all_writes_nothing(tmp_path):
    copy = copy_samples(tmp_path)
    before = snapshot(tmp_path)
    build_all(load_config(copy / "gantry.sample.toml"), samples=copy)
    assert snapshot(tmp_path) == before
    assert not (copy / "out").exists()

