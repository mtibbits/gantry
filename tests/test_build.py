"""The full build: anchors and hrefs, reading and rendering every site, and
writing the pages and the index (#11)."""

import json
import os
import re
from pathlib import Path
from datetime import date, datetime, timezone

import pytest

from helpers_config import matched_sites

import helpers_portfolio as hp
from gantry import build
from gantry.build import CLASH, build_all, working_day
from gantry.config import load_config
from gantry.links import leaf_anchor, leaf_href, page_href
from gantry.views import worksite
from helpers_config import (
    append_bytes,
    copy_samples,
    demo_build,
    lands,
    page_ids,
    page_links,
    run_main,
    sample_build,
    sample_projects,
    snapshot,
    write_site_tree,
    written_bytes,
)
from page_safety import HOSTILE, assert_no_external_resources, assert_page_safe
from sample_roles import BUSY, NO_WBS, NO_WBS_REASON, OWN_UNIT, READABLE, SMALL, UNESTIMATED, UNREADABLE, UNREADABLE_REASON
from test_samples import ROOT
from test_week_page import placed_nodes


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


def test_summaries_on_samples(tmp_path):
    result = sample_build(tmp_path)
    summaries = {s.name: s for s in result.summaries}
    assert [s.name for s in result.summaries] == sample_projects()
    assert {n for n, s in summaries.items() if s.status == "built"} == READABLE
    assert (summaries[NO_WBS].status, summaries[NO_WBS].reason) == ("not_built", NO_WBS_REASON)
    assert (summaries[UNREADABLE].status, summaries[UNREADABLE].reason) == (
        "not_built", UNREADABLE_REASON)
    assert summaries[NO_WBS].open_leaves is None and summaries[NO_WBS].finish_date is None
    # Hand-derived from samples/harbor (WBS.md and its estimates file, 4 lanes,
    # 6 agent-hours a day): every open leaf has file hours but the one with no
    # issue:, which has an est: (the demo week plan's leaf, #61); the plan ends
    # at 70 h, in working day 12 (Wednesday 2026-02-04), and the report lines
    # are that leaf's missing issue:, Issue-14's missing priority and the count
    # of leaves whose tiers came from the estimates file (#14).
    harbor = summaries[BUSY]
    assert (harbor.open_leaves, harbor.finish_day, harbor.finish_date, harbor.report_count,
            harbor.unestimated_count) == (9, 12, date(2026, 2, 4), 3, 0)
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
    page = hp.fixture_source("page")
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


# --- writing: the full build on the command line --------------------------------------


def written_files(out):
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())


def test_demo_build_writes_index_and_pages(tmp_path, monkeypatch):
    code, out, stdout, stderr = demo_build(tmp_path, monkeypatch)
    assert code == 0, stderr
    assert written_files(out) == sorted(["index.html", "strategic.html", "week.html", "week-plan.json",
                                         *(f"sites/{s}.html" for s in READABLE)])
    printed = stdout.splitlines()
    # The fires line is last (#32).
    assert printed[-1] == DEMO_FIRES_LINE
    printed = printed[:-1]
    projects = [p for p in sample_projects() if p in READABLE]
    # The paths (the week view, #38, then strategic.html, #23, before the index; the plan file
    # last, #61), then the in-trace sample site's trace line (#20).
    assert printed[:-1] == [str(out / "sites" / f"{s}.html") for s in projects] + [
        str(out / "week.html"), str(out / "strategic.html"), str(out / "index.html"), str(out / "week-plan.json")]
    assert printed[-1].startswith("trace lighthouse: ")
    printed = printed[:-1]
    assert all(os.path.isabs(line) for line in printed)


def test_index_links_every_written_page(tmp_path, monkeypatch):
    _, out, _, _ = demo_build(tmp_path, monkeypatch)
    links = set(page_links((out / "index.html").read_bytes().decode("utf-8")))
    # Each site page, the week view (#38) and the strategic page (#23).
    assert links == {f"sites/{p.name}" for p in (out / "sites").iterdir()} | {"week.html", "strategic.html"}
    assert len(links) == 6


def test_not_built_sites_listed_exit_0(tmp_path, monkeypatch):
    code, out, _, stderr = demo_build(tmp_path, monkeypatch)
    assert code == 0
    index = (out / "index.html").read_bytes().decode("utf-8")
    for name, reason in ((NO_WBS, NO_WBS_REASON),
                         (UNREADABLE, UNREADABLE_REASON)):
        assert f"not built: {reason}" in index
        assert f"gantry: warning: site {name} is not built: {reason}" in stderr.splitlines()
    assert len(list((out / "sites").iterdir())) == 4


def test_all_fail_writes_index_exit_1(tmp_path):
    config = write_site_tree(tmp_path, {"alpha": None, "beta": None})
    code, stdout, stderr = run_main("--config", str(config))
    assert code == 1
    assert (tmp_path / "out" / "index.html").is_file()
    assert not (tmp_path / "out" / "sites").exists() or not list((tmp_path / "out" / "sites").iterdir())
    assert stdout.splitlines() == [str(tmp_path / "out" / "week.html"), str(tmp_path / "out" / "strategic.html"),
                                   str(tmp_path / "out" / "index.html"),
                                   str(tmp_path / "out" / "week-plan.json"), "fires: 0; sources not fully read: 1"]
    assert stderr.splitlines()[-1] == "gantry: error: no site page was written"


def test_case_clash_cli(tmp_path):
    page = hp.fixture_source("page")
    config = write_site_tree(tmp_path, {"alpha": page, "Alpha": page})
    code, stdout, stderr = run_main("--config", str(config))
    assert code == 0, stderr
    assert [p.name for p in (tmp_path / "out" / "sites").iterdir()] == ["alpha.html"]
    clash = CLASH.format(earlier="alpha")
    assert f"gantry: warning: site Alpha is not built: {clash}" in stderr.splitlines()
    assert f"not built: {clash}" in (tmp_path / "out" / "index.html").read_bytes().decode("utf-8")


def test_site_on_case_clash_exits_1(tmp_path):
    page = hp.fixture_source("page")
    config = write_site_tree(tmp_path, {"alpha": page, "Alpha": page})
    code, stdout, stderr = run_main("--config", str(config), "--site", "Alpha")
    assert (code, stdout) == (1, "")
    assert stderr == f"gantry: error: --site Alpha: cannot build: {CLASH.format(earlier='alpha')}\n"
    assert not (tmp_path / "out").exists()
    assert run_main("--config", str(config), "--site", "alpha")[0] == 0


def test_site_writes_one_page_no_index(tmp_path, monkeypatch):
    code, out, stdout, _ = demo_build(tmp_path, monkeypatch, "--site", BUSY)
    assert code == 0
    assert written_files(out) == [f"sites/{BUSY}.html"]
    assert stdout == f"{out / 'sites' / f'{BUSY}.html'}\n"


def test_site_unconfigured_exits_1(tmp_path):
    config = write_site_tree(tmp_path, {"alpha": hp.fixture_source("page")})
    code, out, err = run_main("--config", str(config), "--site", "nowhere")
    assert (code, out) == (1, "")
    assert err == f"gantry: error: --site nowhere: no such site in {tmp_path / 'devagent.toml'}\n"
    assert not (tmp_path / "out").exists()


def test_full_build_deletes_nothing(tmp_path, monkeypatch):
    copy = copy_samples(tmp_path)
    seeded = {"sites/stale.html": b"old stale page", f"sites/{NO_WBS}.html": b"old page", "keep.txt": b"keep"}
    for rel, data in seeded.items():
        (copy / "out" / rel).parent.mkdir(parents=True, exist_ok=True)
        (copy / "out" / rel).write_bytes(data)
    code, out, _, _ = demo_build(tmp_path, monkeypatch, copy=copy)
    assert code == 0
    assert {rel: (out / rel).read_bytes() for rel in seeded} == seeded


def test_unwritable_output_exits_1(tmp_path):
    config = write_site_tree(tmp_path, {"alpha": hp.fixture_source("page")})
    (tmp_path / "out").write_bytes(b"a file, not a folder")
    code, stdout, stderr = run_main("--config", str(config))
    # build_all returned, so the fires line prints though nothing was written (#32).
    assert (code, stdout) == (1, "fires: 0; sources not fully read: 1\n")
    lines = stderr.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith(f"gantry: error: cannot write {tmp_path / 'out' / 'index.html'}: ")
    assert (tmp_path / "out").read_bytes() == b"a file, not a folder"


def test_page_write_error_lists_site(tmp_path):
    page = hp.fixture_source("page")
    config = write_site_tree(tmp_path, {"alpha": page, "beta": page})
    (tmp_path / "out" / "sites" / "beta.html").mkdir(parents=True)
    code, stdout, stderr = run_main("--config", str(config))
    assert code == 0
    assert stdout.splitlines() == [str(tmp_path / "out" / "sites" / "alpha.html"),
                                   str(tmp_path / "out" / "week.html"), str(tmp_path / "out" / "strategic.html"),
                                   str(tmp_path / "out" / "index.html"), str(tmp_path / "out" / "week-plan.json"),
                                   "fires: 0; sources not fully read: 1"]
    (line,) = [line for line in stderr.splitlines() if "beta" in line]
    assert line.startswith("gantry: warning: site beta is not built: cannot write sites/beta.html: ")
    index = (tmp_path / "out" / "index.html").read_bytes().decode("utf-8")
    assert "not built: cannot write sites/beta.html: " in index
    assert str(tmp_path) not in index
    # The strategic page agrees (I2): beta is not built there either, and its hours are in no total.
    from helpers_strategic import page_data
    from test_index_page import figure_of, site_lines
    from test_worksite_page import find, text_of, tree

    data = page_data((tmp_path / "out" / "strategic.html").read_bytes().decode("utf-8"))
    assert [(g["kind"], g["sites"]) for g in data["groups"]] == [("no_share", ["alpha"]), ("not_built", ["beta"])]
    panels = {p["site"]: p for p in data["panels"]}
    assert panels["beta"]["reason"].startswith("cannot write sites/beta.html: ")
    assert panels["beta"]["hours"] is None and panels["alpha"]["hours"] > 0
    assert data["totals"]["hours"] == panels["alpha"]["hours"]
    t = tree(index)
    lines = site_lines(t)
    assert text_of(t, figure_of(t, "index-share", lines["alpha"][0])) == "backlog share 100% (includes 14 unestimated leaves)"
    assert [text_of(t, i) for i in find(t, "span", ("extra",), within=lines["beta"][0])] == [""]


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes only")
def test_index_mode_follows_umask(tmp_path):
    config = write_site_tree(tmp_path, {"alpha": hp.fixture_source("page")})
    old = os.umask(0o022)
    try:
        assert run_main("--config", str(config))[0] == 0
    finally:
        os.umask(old)
    assert (tmp_path / "out" / "index.html").stat().st_mode & 0o777 == 0o644


def test_added_project_with_its_document_appears(tmp_path, monkeypatch):
    copy = copy_samples(tmp_path)
    before = (copy / "gantry.sample.toml").read_bytes()
    dunes = hp.source(b"# Dunes\n\n- [ ] Plant the grass {issue: Issue-1}\n", hp.node("Plant the grass", issue="Issue-1"),
                      start=3)
    (copy / "dunes").mkdir()
    (copy / "dunes" / "WBS.md").write_bytes(dunes.text)
    append_bytes(copy / "devagent" / "config.toml", '\n[project.dunes]\ndevdoc_dir = "../dunes"\n')
    hp.rewrite_document(copy, lambda doc: hp.append_project(doc, "dunes", dunes))
    code, out, _, _ = demo_build(tmp_path, monkeypatch, copy=copy)
    assert code == 0
    assert "sites/dunes.html" in page_links((out / "index.html").read_bytes().decode("utf-8"))
    assert (out / "sites" / "dunes.html").is_file()
    assert (copy / "gantry.sample.toml").read_bytes() == before


# --- link integrity and the Pages demo ------------------------------------------------


def written_pages(out):
    return {p.relative_to(out).as_posix(): p.read_bytes().decode("utf-8") for p in sorted(out.rglob("*.html"))}


def test_every_link_lands(tmp_path, monkeypatch):
    _, out, _, _ = demo_build(tmp_path, monkeypatch)
    pages = written_pages(out)
    counts = {}
    for rel, text in pages.items():
        links = page_links(text)
        counts[rel] = len(links)
        assert all(lands(out, rel, href) for href in links), rel
    # The subject set cannot quietly empty: the index links 4 pages and the
    # week view (#38) and the strategic page, the week view links each leaf
    # its plan names, the strategic page its 4 built sites' pages (harbor
    # twice, #23), the worksite pages link their boxes on another site (#68),
    # and the trace links across sites (#21): lighthouse its panel's quarry
    # leaf, quarry its lighthouse:FR-2 row.
    assert counts.pop("index.html") == 6
    assert counts.pop("week.html") > 0  # counted off the plan file in test_week_page_links_land
    assert counts.pop("strategic.html") == 5
    assert counts == {f"sites/{UNESTIMATED}.html": 2, f"sites/{OWN_UNIT}.html": 4, f"sites/{BUSY}.html": 1,
                      f"sites/{SMALL}.html": 1}


def test_encoded_key_anchor_and_href_land(tmp_path):
    hostile = "a b<c>&\u00e9"
    wbs = hp.source(f"- [x] Done {{issue: {hostile}}}\n- [ ] Open {{issue: x y}}\n".encode("utf-8"),
                    hp.node("Done", glyph="x", issue=hostile), hp.node("Open", issue="x y"),
                    report=((1, "issue_not_id", f"issue '{hostile}' is not an issue id; its issue dir is not read"),
                            (2, "issue_not_id", "issue 'x y' is not an issue id; its issue dir is not read")))
    other = hp.fixture_source("page")
    config = write_site_tree(tmp_path, {"alpha": wbs, "other": other})
    code, _, stderr = run_main("--config", str(config))
    assert code == 0, stderr
    out = tmp_path / "out"
    ids = page_ids((out / "sites" / "alpha.html").read_bytes().decode("utf-8"))
    for key in (hostile, "x y"):
        anchor = leaf_anchor(key)
        assert ids.count(anchor) == 1, key
        assert re.fullmatch(r"leaf-[A-Za-z0-9_.~%-]+", anchor)
        assert lands(out, "index.html", leaf_href("alpha", key, from_top=True))
        assert lands(out, "sites/other.html", leaf_href("alpha", key, from_top=False))
    # A link to a leaf that is not there does not land.
    assert not lands(out, "index.html", leaf_href("alpha", "nowhere", from_top=True))
    assert not lands(out, "index.html", leaf_href("absent", "x y", from_top=True))


def test_demo_pages_load_nothing_external(tmp_path, monkeypatch):
    _, out, _, _ = demo_build(tmp_path, monkeypatch)
    pages = written_pages(out)
    assert len(pages) == 7
    for rel, text in pages.items():
        assert_no_external_resources(text)
        assert_page_safe(text, [])


# --- the docs ---------------------------------------------------------------------------

STDERR_FORMS = (
    "gantry: warning: site <name> is not built: <reason>",
    "gantry: warning: devagent's capacity declaration is invalid (<count>); it is not used",
    "gantry: error: cannot write <index path>: <reason>",
    "gantry: error: cannot write week-plan.json: <reason>",
    "gantry: error: cannot render week.html: <type>: <message>",
    "gantry: error: cannot write week.html: <reason>",
    "gantry: error: cannot render strategic.html: <type>: <message>",
    "gantry: error: cannot write strategic.html: <reason>",
    "gantry: error: no site page was written",
)
LISTING_TEXTS = {
    "docs/worksite-page.md": "lists the sites and writes nothing",
    "docs/config.md": "The listing writes nothing",
    "commands/build.md": "List the worksites",
    "README.md": "every site at once, the index and",
}


def heading_slugs(text):
    """GitHub's anchor for each heading: lower case, punctuation dropped, spaces to hyphens."""
    slugs = set()
    for line in text.splitlines():
        if line.startswith("#"):
            title = line.lstrip("#").strip().replace("`", "").lower()
            slugs.add(re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", title)))
    return slugs


def test_build_doc_pins():
    doc = (ROOT / "docs" / "worksite-page.md").read_bytes().decode("utf-8")
    for text in (build.CLASH, build.WRITE_ERROR, build.RENDER_ERROR, *STDERR_FORMS, "leaf-<key>",
                 "<name>.html#leaf-<key>", "sites/<name>.html#leaf-<key>"):
        assert f"`{text}`" in doc, text
    for code in ("0", "1", "2"):
        assert re.search(rf"^\| {code} \|", doc, re.M), code
    for rel, text in LISTING_TEXTS.items():
        assert text not in (ROOT / rel).read_bytes().decode("utf-8"), rel
    # Every in-document link to a heading of this doc, or of config.md, resolves.
    docs = {name: (ROOT / "docs" / name).read_bytes().decode("utf-8") for name in ("worksite-page.md", "config.md")}
    for name, text in docs.items():
        for target, anchor in re.findall(r"\]\(([\w.-]*)#([\w-]+)\)", text):
            if target in ("", *docs):
                assert anchor in heading_slugs(docs[target or name]), (name, target, anchor)


# --- final-review fixes ----------------------------------------------------------------


def test_unwritable_output_still_reports_not_built(tmp_path, monkeypatch):
    copy = copy_samples(tmp_path)
    (copy / "out").write_bytes(b"a file, not a folder")
    code, out, stdout, stderr = demo_build(tmp_path, monkeypatch, copy=copy)
    assert (code, stdout) == (1, DEMO_FIRES_LINE + "\n")
    lines = stderr.splitlines()
    assert f"gantry: warning: site {NO_WBS} is not built: {NO_WBS_REASON}" in lines
    assert f"gantry: warning: site {UNREADABLE} is not built: {UNREADABLE_REASON}" in lines
    assert lines[-1] == f"gantry: error: cannot write {out / 'index.html'}: output_dir is not a folder"
    assert sum("gantry: error:" in line for line in lines) == 1
    assert out.read_bytes() == b"a file, not a folder"


def test_unwritable_sites_folder_still_writes_index(tmp_path, monkeypatch):
    copy = copy_samples(tmp_path)
    (copy / "out").mkdir()
    (copy / "out" / "sites").write_bytes(b"a file, not a folder")
    code, out, stdout, stderr = demo_build(tmp_path, monkeypatch, copy=copy)
    assert (code, stdout) == (1, f"{out / 'week.html'}\n{out / 'strategic.html'}\n{out / 'index.html'}\n"
                                 f"{out / 'week-plan.json'}\n{DEMO_FIRES_LINE}\n")
    lines = stderr.splitlines()
    for name in READABLE:
        assert f"gantry: warning: site {name} is not built: cannot write sites/{name}.html: sites/ is not a folder" in lines
    assert lines[-1] == "gantry: error: no site page was written"
    assert "index.html" not in stderr
    assert (out / "sites").read_bytes() == b"a file, not a folder"


def test_read_step_error_isolated(tmp_path, monkeypatch):
    """A fault in one site's read step (its inputs, after the document's nodes) stops only that site (#12)."""
    real = build.apply_inputs

    def apply_inputs(roots, site, data, config):
        if site.name == "beta":
            raise RuntimeError("read step broke")
        return real(roots, site, data, config)

    monkeypatch.setattr(build, "apply_inputs", apply_inputs)
    result = build_all(page_tree(tmp_path, ["alpha", "beta"]))
    summaries = {s.name: s for s in result.summaries}
    assert summaries["alpha"].status == "built"
    assert (summaries["beta"].status, summaries["beta"].reason) == ("not_built",
                                                                    "render error: RuntimeError: read step broke")


def test_site_mode_read_error_is_one_line(tmp_path, monkeypatch):
    def apply_inputs(roots, site, data, config):
        raise RuntimeError("read step broke")

    monkeypatch.setattr(build, "apply_inputs", apply_inputs)
    config = write_site_tree(tmp_path, {"alpha": hp.fixture_source("page")})
    code, stdout, stderr = run_main("--config", str(config), "--site", "alpha")
    assert (code, stdout) == (1, "")
    assert stderr.endswith("gantry: error: --site alpha: cannot build: render error: RuntimeError: read step broke\n")
    assert not (tmp_path / "out" / "sites" / "alpha.html").exists()


@pytest.mark.parametrize("est, day, when", [
    ("8h", 2, date(2026, 1, 21)),   # 1.33 days: the work runs into the second working day
    ("2h", 1, date(2026, 1, 20)),   # under half a day: day 1, the as-of date, never day 0
    ("12h", 2, date(2026, 1, 21)),  # exactly two days
])
def test_finish_day_is_the_day_the_plan_ends_in(tmp_path, est, day, when):
    wbs = hp.source(f"- [ ] A {{issue: Issue-1, est: {est}}}\n".encode("utf-8"), hp.node("A", issue="Issue-1", est=est))
    config = load_config(write_site_tree(tmp_path, {"alpha": wbs}, as_of="2026-01-20"))
    (summary,) = build_all(config).summaries
    assert (summary.finish_day, summary.finish_date) == (day, when)


# --- the week plan (#61) ------------------------------------------------------------------


def plan_install(tmp_path, base="states", **changes):
    from test_planner import install

    return install(tmp_path, base, **changes)


def spy_on(monkeypatch, module, name, before=None, after=None):
    """Wrap module.name: each call's (args, result) is recorded. `before` runs
    on the arguments first; `after` runs on the result before it is returned."""
    real, calls = getattr(module, name), []

    def spy(*args, **kwargs):
        if before is not None:
            before(*args, **kwargs)
        result = real(*args, **kwargs)
        calls.append((args, result))
        if after is not None:
            after(result)
        return result

    monkeypatch.setattr(module, name, spy)
    return calls


def test_full_build_reads_portfolio_once(tmp_path, monkeypatch):
    from gantry import roots, trace
    from gantry.planner import NO_PLAN

    copy = plan_install(tmp_path)
    reads = spy_on(monkeypatch, trace, "read_portfolio_file")
    opens = spy_on(monkeypatch, roots, "open_confined")
    result = build_all(load_config(copy / "gantry.toml"))
    assert len(reads) == 1
    assert result.portfolio is reads[0][1]
    assert result.portfolio.settings_read and result.plan.status == "planned"
    # At the one door every read goes through: the file is opened once per run.
    assert [str(args[1]) for args, _ in opens].count(str(copy / "portfolio.md")) == 1
    del reads[:], opens[:]
    assert run_main("--config", str(copy / "gantry.toml"))[0] == 0
    assert len(reads) == 1
    assert [str(args[1]) for args, _ in opens].count(str(copy / "portfolio.md")) == 1

    del reads[:]
    bare = plan_install(tmp_path, config={"portfolio_file": None})
    result = build_all(load_config(bare / "gantry.toml"))
    assert reads == [] and result.portfolio is None
    assert (result.plan.status, result.plan.reason) == ("no_plan", NO_PLAN["no_portfolio"])


def _names(tree):
    import ast

    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    return names | {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}


LIGHTHOUSE_ISSUES = frozenset(f"Issue-{n}" for n in range(1, 13))  # samples/lighthouse/WBS.md, finished leaves too


def test_build_checks_portfolio_once(tmp_path, monkeypatch):
    import ast

    from gantry import portfolio_file

    calls = spy_on(monkeypatch, portfolio_file, "check_portfolio")
    code, _, _, stderr = demo_build(tmp_path, monkeypatch)
    assert code == 0, stderr
    ((portfolio, site_names, issues), _), = calls
    assert list(site_names) == sample_projects()
    assert list(issues) == sample_projects()
    assert {name for name, value in issues.items() if value is None} == {NO_WBS, UNREADABLE}
    assert all(isinstance(issues[name], frozenset) for name in READABLE)
    assert issues[SMALL] == LIGHTHOUSE_ISSUES
    # The --site build checks nothing (I7).
    del calls[:]
    copy = plan_install(tmp_path)
    assert run_main("--config", str(copy / "gantry.toml"), "--site", "a")[0] == 0
    assert calls == []
    # No name in the planner or the --site build reaches the check; the full build runs report_entries.
    for name in ("planner.py", "cli.py"):
        tree = ast.parse((ROOT / "gantry" / name).read_bytes().decode("utf-8"))
        if name == "cli.py":
            tree = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_build_site")
        assert not {"check_portfolio", "report_entries"} & _names(tree), name
    assert "report_entries" in _names(ast.parse((ROOT / "gantry" / "build.py").read_bytes().decode("utf-8")))


def test_portfolio_entries_on_result(tmp_path, monkeypatch):
    from helpers_config import copy_samples, replace_bytes

    assert sample_build(tmp_path / "plain").portfolio_entries == ()
    copy = copy_samples(tmp_path / "issue99")
    replace_bytes(copy / "portfolio.md", 'issue = "Issue-5"', 'issue = "Issue-99"')
    result = build_all(load_config(copy / "gantry.sample.toml"), samples=copy)
    assert [(e.kind, e.message) for e in result.portfolio_entries] == [
        ("milestone_no_leaf", "no leaf on lighthouse has issue 'Issue-99'")]
    bare = plan_install(tmp_path, config={"portfolio_file": None})
    result = build_all(load_config(bare / "gantry.toml"))
    assert result.portfolio is None and result.portfolio_entries == ()
    # A site read but not rendered counts as not read (D2): #22's "could not be read" text.
    render_model = worksite.render_model

    def failing(m, **kw):
        if m["site"] == SMALL:
            raise ValueError("x")
        return render_model(m, **kw)

    monkeypatch.setattr(worksite, "render_model", failing)
    result = sample_build(tmp_path / "failed")
    assert [(e.kind, e.message) for e in result.portfolio_entries] == [
        ("milestone_no_leaf", f"site {SMALL} could not be read, so the milestone leaf cannot be found")]


# --- the strategic page (#23) -------------------------------------------------------------


def index_shows_no_shares(page):
    """True when the index shows no backlog share: each site line's extra slot is empty and no
    index-share figure is on the page."""
    from test_worksite_page import by_id, find, text_of, tree

    t = tree(page)
    extras = find(t, "span", ("extra",), within=by_id(t, "sites"))
    shares = [i for i in find(t, "span") if t.elements[i]["attrs"].get("data-figure") == "index-share"]
    return bool(extras) and all(text_of(t, i) == "" for i in extras) and shares == []


def test_strategic_on_full_build_not_site(tmp_path):
    copy = plan_install(tmp_path)
    code, stdout, stderr = run_main("--config", str(copy / "gantry.toml"))
    out = copy / "out"
    assert code == 0, stderr
    assert (out / "strategic.html").is_file()
    assert not index_shows_no_shares((out / "index.html").read_bytes().decode("utf-8"))  # the control
    assert stdout.splitlines() == [str(out / "sites" / "a.html"), str(out / "sites" / "b.html"),
                                   str(out / "week.html"), str(out / "strategic.html"), str(out / "index.html"),
                                   str(out / "week-plan.json"), "fires: 0; sources not fully read: 1"]
    single = plan_install(tmp_path)
    assert run_main("--config", str(single / "gantry.toml"), "--site", "a")[0] == 0
    assert written_files(single / "out") == ["sites/a.html"]


def test_strategic_write_failure(tmp_path):
    from test_index_page import headline_and_links

    copy = plan_install(tmp_path)
    out = copy / "out"
    (out / "strategic.html").mkdir(parents=True)
    code, stdout, stderr = run_main("--config", str(copy / "gantry.toml"))
    assert code == 1
    assert stderr.splitlines()[-1].startswith("gantry: error: cannot write strategic.html: ")
    assert str(copy) not in stderr
    assert stdout.splitlines() == [str(out / "sites" / "a.html"), str(out / "sites" / "b.html"),
                                   str(out / "week.html"), str(out / "index.html"), str(out / "week-plan.json"),
                                   "fires: 0; sources not fully read: 1"]
    # The index never links a page that was not written: no headline, no shares, and only the week view's link.
    index = (out / "index.html").read_bytes().decode("utf-8")
    assert headline_and_links(index) == ("", [("Week view", "week.html")])
    assert index_shows_no_shares(index)
    assert (out / "week-plan.json").is_file() and (out / "strategic.html").is_dir()


def test_strategic_render_failure(tmp_path, monkeypatch):
    from gantry import strategic

    from test_index_page import headline_and_links

    def broken(*args, **kwargs):
        raise ValueError("x")

    copy = plan_install(tmp_path)
    monkeypatch.setattr(strategic, "strategic_model", broken)
    result, written, error = build.write_all(build_all(load_config(copy / "gantry.toml")))
    # The week view's form (#38): a render fault is "cannot render", not "cannot write".
    assert error is None and result.strategic_error == "cannot render strategic.html: ValueError: x"
    assert copy / "out" / "strategic.html" not in written and not (copy / "out" / "strategic.html").exists()
    index = (copy / "out" / "index.html").read_bytes().decode("utf-8")
    assert headline_and_links(index) == ("", [("Week view", "week.html")])
    assert index_shows_no_shares(index)
    code, _, stderr = run_main("--config", str(copy / "gantry.toml"))
    assert code == 1
    assert stderr.splitlines()[-1] == "gantry: error: cannot render strategic.html: ValueError: x"


def test_strategic_render_failure_without_message(tmp_path, monkeypatch):
    from gantry import views

    def broken(*args, **kwargs):
        raise ValueError()

    copy = plan_install(tmp_path)
    monkeypatch.setattr(views.strategic, "render_strategic", broken)
    result, _, error = build.write_all(build_all(load_config(copy / "gantry.toml")))
    assert error is None and result.strategic_error == "cannot render strategic.html: ValueError"


def test_strategic_error_prints_between_plan_and_index(tmp_path):
    copy = plan_install(tmp_path)
    out = copy / "out"
    for name in ("strategic.html", "index.html", "week-plan.json"):
        (out / name).mkdir(parents=True)
    code, _, stderr = run_main("--config", str(copy / "gantry.toml"))
    assert code == 1
    lines = stderr.splitlines()
    assert [line.split(": ")[2] for line in lines] == ["cannot write week-plan.json", "cannot write strategic.html",
                                                       f"cannot write {out / 'index.html'}"]


def test_every_output_failing_prints_in_order(tmp_path):
    """The week view (#38) and the strategic page (#23) both fail with the plan file and the index:
    one line each, week, plan file, strategic, index, and exit 1."""
    copy = plan_install(tmp_path)
    out = copy / "out"
    for name in ("week.html", "strategic.html", "index.html", "week-plan.json"):
        (out / name).mkdir(parents=True)
    code, stdout, stderr = run_main("--config", str(copy / "gantry.toml"))
    assert code == 1
    assert [line.split(": ")[2] for line in stderr.splitlines()] == [
        "cannot write week.html", "cannot write week-plan.json", "cannot write strategic.html",
        f"cannot write {out / 'index.html'}"]
    # The fires line (#32) still prints: it counts the build, whether or not anything was written.
    assert stdout.splitlines() == [str(out / "sites" / "a.html"), str(out / "sites" / "b.html"), "fires: 0; sources not fully read: 1"]


def test_full_build_hands_same_plan_to_views_and_writes_it(tmp_path, monkeypatch):
    from gantry import cli
    from gantry.planner import WeekPlan, plan_to_json

    copy = plan_install(tmp_path)
    texts = []
    # The plan's text as build_all returned it, before any view has seen it.
    built = spy_on(monkeypatch, cli, "build_all", after=lambda result: texts.append(plan_to_json(result.plan)))
    # A view that changes the plan it was handed cannot change the file: the
    # text is made before any view runs.
    indexed = spy_on(monkeypatch, build, "render_index",
                     before=lambda result, **kw: result.plan.leaves.append("view"))
    code, stdout, stderr = run_main("--config", str(copy / "gantry.toml"))
    assert code == 0, stderr
    (_, result), = built
    assert isinstance(result.plan, WeekPlan)
    ((seen,), _), = indexed
    assert seen.plan is result.plan
    assert (copy / "out" / "week-plan.json").read_bytes() == texts[0].encode("utf-8")
    assert result.plan.leaves[-1] == "view" and '"view"' not in texts[0]
    out = copy / "out"
    assert stdout.splitlines() == [str(out / "sites" / "a.html"), str(out / "sites" / "b.html"),
                                   str(out / "week.html"), str(out / "strategic.html"), str(out / "index.html"),
                                   str(out / "week-plan.json"), "fires: 0; sources not fully read: 1"]


def test_site_writes_no_plan_file(tmp_path):
    copy = plan_install(tmp_path)
    assert run_main("--config", str(copy / "gantry.toml"), "--site", "a")[0] == 0
    assert written_files(copy / "out") == ["sites/a.html"]
    (copy / "out" / "week-plan.json").write_bytes(b"an older plan\n")
    before = snapshot(copy / "out")["week-plan.json"]
    assert run_main("--config", str(copy / "gantry.toml"), "--site", "b")[0] == 0
    assert written_files(copy / "out") == ["sites/a.html", "sites/b.html", "week-plan.json"]
    assert (copy / "out" / "week-plan.json").read_bytes() == b"an older plan\n"
    assert snapshot(copy / "out")["week-plan.json"] == before


def test_unwritable_output_skips_plan_write(tmp_path, monkeypatch):
    from gantry import cli
    from gantry.planner import WeekPlan

    copy = plan_install(tmp_path)
    (copy / "out").write_bytes(b"a file, not a folder")
    built = spy_on(monkeypatch, cli, "build_all")
    writes = spy_on(monkeypatch, cli, "write_plan")
    code, stdout, stderr = run_main("--config", str(copy / "gantry.toml"))
    assert (code, stdout) == (1, "fires: 0; sources not fully read: 1\n")
    assert writes == []
    assert isinstance(built[0][1].plan, WeekPlan) and built[0][1].plan.status == "planned"
    assert stderr.splitlines() == [f"gantry: error: cannot write {copy / 'out' / 'index.html'}: output_dir is not a folder"]
    assert "week-plan.json" not in stderr
    assert (copy / "out").read_bytes() == b"a file, not a folder"


def test_index_failure_still_writes_plan(tmp_path):
    from gantry.planner import plan_to_json

    copy = plan_install(tmp_path)
    out = copy / "out"
    (out / "index.html").mkdir(parents=True)
    code, stdout, stderr = run_main("--config", str(copy / "gantry.toml"))
    assert code == 1
    # An older plan never stands in: the plan file is written though the index is not.
    assert stdout.splitlines() == [str(out / "sites" / "a.html"), str(out / "sites" / "b.html"),
                                   str(out / "week.html"), str(out / "strategic.html"), str(out / "week-plan.json"),
                                   "fires: 0; sources not fully read: 1"]
    assert (out / "week-plan.json").read_bytes() == plan_to_json(
        build_all(load_config(copy / "gantry.toml")).plan).encode("utf-8")
    (line,) = stderr.splitlines()
    assert line.startswith(f"gantry: error: cannot write {out / 'index.html'}: ")
    assert (out / "index.html").is_dir()


def test_demo_plan_as_of_day_contents(tmp_path):
    result = sample_build(tmp_path)
    plan = result.plan
    assert plan.status == "planned"
    first = plan.days[0]
    assert first["date"] == plan.as_of == "2026-01-20"
    assert len(first["blocks"]) >= 1 and first["lanes_row"]
    on_day = [(e["site"], e["key"]) for block in first["blocks"] for e in block["leaves"]]
    on_day += [(e["site"], e["key"]) for e in first["lanes_row"]]
    unestimated = {(e["site"], e["key"]) for e in plan.leaves if e["unestimated"]}
    assert unestimated & set(on_day)
    assert first["unestimated"] == len(unestimated & set(on_day))
    # A leaf with no issue:, looked up in the build's own data, never read off the key's spelling.
    leaves = {(site, leaf.key): leaf for site, inputs in result.inputs.items() for leaf in inputs.data.leaves}
    assert any(leaves[key].issue is None for key in on_day), on_day
    # The lanes row holds the h: 0 leaf, whose rate is 0 under the sample mapping.
    rate = {(e["site"], e["key"]): e["rate"] for e in plan.leaves}
    assert all(rate[e["site"], e["key"]] == 0 for e in first["lanes_row"])


def test_demo_plan_as_of_day_has_blocks_for_two_sites(tmp_path):
    # Fair share (#37): the demo's as-of day divides its attention between sites.
    plan = sample_build(tmp_path).plan
    first = plan.days[0]
    assert first["date"] == "2026-01-20"
    assert len({block["site"] for block in first["blocks"]}) >= 2
    assert plan.allocation_rule == "fair share by intended shares"


BUILD_HOMES = ("docs/worksite-page.md", "docs/config.md", "commands/build.md", "README.md", "gantry/cli.py")


def test_build_homes_name_the_plan_file(capsys):
    # Every place that says what the build writes, prints or exits 1 on names the plan file (#61).
    # And the strategic page (#23).
    for rel in BUILD_HOMES:
        text = " ".join((ROOT / rel).read_bytes().decode("utf-8").split())
        assert "week-plan.json" in text, rel
        assert "strategic.html" in text, rel
    for rel in ("docs/worksite-page.md", "docs/config.md", "commands/build.md", "gantry/cli.py"):
        text = " ".join((ROOT / rel).read_bytes().decode("utf-8").split())
        assert "plan file cannot be written" in text, rel
        assert "strategic.html cannot be made or written" in text, rel
    readme = " ".join((ROOT / "README.md").read_bytes().decode("utf-8").split())
    assert "its page paths (`index.html`, `strategic.html`, `sites/<name>.html`, `week.html`)" in readme
    doc = " ".join((ROOT / "docs" / "worksite-page.md").read_bytes().decode("utf-8").split())
    assert "Seven more lines can follow on stderr" in doc
    for rel in ("docs/worksite-page.md", "docs/config.md", "commands/build.md", "gantry/cli.py"):
        text = " ".join((ROOT / rel).read_bytes().decode("utf-8").split())
        assert "the week page cannot be rendered or written" in text, rel
    assert "not attempted when the output folder itself cannot be made" in doc
    # The sentence the output-folder case rests on stays as it was.
    assert ("When the output folder itself cannot be made, this is the only error line (the not-built lines "
            "still come first), and nothing is written") in doc
    from gantry.cli import main

    with pytest.raises(SystemExit):
        main(["build", "--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "<output_dir>/week-plan.json" in help_text and "<output_dir>/strategic.html" in help_text


def assert_no_stale(stale, paths):
    for path in sorted(paths):
        text = " ".join(path.read_bytes().decode("utf-8").split())
        assert [s for s in stale if s in text] == [], path.name


def swept_files():
    return [*ROOT.glob("gantry/**/*.py"), *ROOT.glob("docs/*.md"), *ROOT.glob("commands/*.md"), ROOT / "README.md"]


def test_no_home_defers_to_this_issue():
    # The repo's own deferrals to #61 are reworded: #23 alone adds the rest.
    # Split, so this test is not itself a hit of the sweep's grep.
    stale = ("#23 or " + "#61", "only its business " + "requirements", "only the file's " + "business")
    assert_no_stale(stale, [*swept_files(), ROOT / "tests" / "test_cli.py"])


def test_no_doc_defers_to_62():
    # The deferrals to #62 are gone now that it plans cross-site entries; a citation
    # of #62 for what it added stays. Split, so this test is not itself a hit.
    assert_no_stale(("until " + "#62", "is " + "#62.", "(for " + "#62)"), swept_files())


def test_demo_plan_holds_a_cross_site_leaf(tmp_path):
    # The demo week plan holds a leaf of one site on another site's open leaf (#62),
    # and the hold binds inside the 10 days.
    from gantry.portfolio_file import intended_shares

    result = sample_build(tmp_path)
    plan = result.plan
    waiter = next(leaf for leaf in result.inputs[BUSY].data.leaves if leaf.key == "Issue-6")
    held_on = [(o.target_site, o.target_key) for o in waiter.foreign_targets or () if o.status == "open"]
    assert held_on == [(UNESTIMATED, "Issue-3")]
    records = {(entry["site"], entry["key"]): entry for entry in plan.leaves}
    blocker, held = records[UNESTIMATED, "Issue-3"], records[BUSY, "Issue-6"]
    assert blocker["hours_after_horizon"] == 0
    assert held["days"][0] in plan.horizon_days
    assert plan.horizon_days.index(held["days"][0]) >= plan.horizon_days.index(blocker["days"][-1]) + 1
    # Both sites hold an intended share on the as-of date.
    assert {BUSY, UNESTIMATED} <= set(intended_shares(result.portfolio, result.config.as_of).shares)


# --- the fires of a full build (#32) ------------------------------------------------------

DEMO_FIRES_LINE = "fires: 8; sources not fully read: 1"


def fires_install(tmp_path, name="samples", fires_list=None):
    """A samples copy under tmp_path/name, its fires_list repointed when given."""
    copy = copy_samples(tmp_path / name)
    if fires_list is not None:
        config = copy / "gantry.sample.toml"
        text = config.read_bytes().decode("utf-8")
        assert text.count('fires_list = "fires.md"') == 1
        config.write_bytes(text.replace('fires_list = "fires.md"', f'fires_list = "{fires_list}"').encode("utf-8"))
    return copy


def test_demo_build_collects_fires(tmp_path, monkeypatch):
    from gantry import fires_list

    seen = spy_on(monkeypatch, build, "collect_fires")
    result = sample_build(tmp_path)
    report = result.fires
    assert report is seen[0][1]
    assert result.fires_list is seen[0][0][2]
    assert result.fires_list.status == "read" and len(result.fires_list.entries) == 6
    assert set(result.records) == set(sample_projects())
    for name, data in ((n, i.data) for n, i in result.inputs.items()):
        assert not [e for e in data.report if e.kind in fires_list.REPORT_KINDS], name
    assert [(f.kinds, f.site, f.issue) for f in report.fires if f.kinds != ("fires_list",)] == [
        (("p0_leaf",), "quarry", "Issue-8"), (("stuck",), "harbor", "Issue-13"), (("stuck",), "harbor", "Issue-14"),
        (("stuck",), "harbor", "Issue-16")]
    stuck = [f for f in report.fires if f.kinds == ("stuck",)]
    # Every devagent-written STUCK carries the fixtures' one fixed Created: time.
    assert len(stuck) == 3 and stuck[0].since == stuck[1].since == stuck[2].since
    assert {f.why[0] for f in report.fires if f.kinds == ("fires_list",)} == {
        "the east crane failed its morning check", "return the surveyor's call about the boundary",
        "the weighbridge has read low since last week", "a delivery arrived with no paperwork"}
    assert report.stuck_no_open_leaf == {"quarry": ("Issue-20",)}
    assert report.stuck_parked == {"quarry": ("Issue-9",)}
    p0 = {s.site: s.status for s in report.sources if s.source == "p0_leaf"}
    assert (p0[NO_WBS], p0[UNREADABLE]) == ("absent", "not_read")
    assert report.confirmed_none is False
    assert (len(report.fires), report.not_fully_read) == (8, 1)


def test_demo_build_prints_fires_line(tmp_path, monkeypatch):
    first = demo_build(tmp_path / "a", monkeypatch)
    second = demo_build(tmp_path / "b", monkeypatch)
    assert first[0] == second[0] == 0
    assert first[2].splitlines()[-1] == second[2].splitlines()[-1] == DEMO_FIRES_LINE
    assert written(first[1]) == written(second[1])


def test_bare_build_result_has_no_fires():
    bare = build.BuildResult(None, (), (), {}, {}, (), "", None)
    assert (bare.fires_list, bare.fires, bare.records) == (None, None, {})


def test_full_build_reads_fires_and_records_once(tmp_path, monkeypatch):
    from gantry.fires import naive_reference

    lists = spy_on(monkeypatch, build, "read_fires_list")
    sites_named = []
    records = spy_on(monkeypatch, build, "read_site_records",
                     before=lambda roots, planning_dir, *, site=None: sites_named.append(site))
    collected = spy_on(monkeypatch, build, "collect_fires")
    result = sample_build(tmp_path)
    assert len(lists) == 1 and len(collected) == 1
    assert lists[0][0][2] == sample_projects()
    assert lists[0][0][3] == naive_reference(collected[0][0][4])
    with_planning = [s.name for s in result.sites if s.planning_dir is not None]
    assert len(with_planning) == 6
    assert len(records) == len(with_planning)
    assert sorted(sites_named) == sorted(with_planning)


def written(out):
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def without_strategic(pages):
    """The written pages but strategic.html, which shows fires since #29 (the unplanned-work thief)."""
    return {name: data for name, data in pages.items() if name != "strategic.html"}


def test_fires_never_change_exit_status(tmp_path, monkeypatch):
    plain = fires_install(tmp_path, "plain")
    missing = fires_install(tmp_path, "missing", fires_list="nope.md")
    code, out, stdout, _ = demo_build(tmp_path, monkeypatch, copy=plain)
    code2, out2, stdout2, _ = demo_build(tmp_path, monkeypatch, copy=missing)
    assert code == code2 == 0
    assert without_strategic(written(out)) == without_strategic(written(out2))
    assert b"incomplete: fires list not read" in written(out2)["strategic.html"]
    assert stdout.splitlines()[-1] == DEMO_FIRES_LINE
    assert stdout2.splitlines()[-1] == "fires: 4; sources not fully read: 2"
    result = build_all(load_config(missing / "gantry.sample.toml"), samples=missing)
    assert result.fires_list.status == "not_read"
    assert build_all(load_config(plain / "gantry.sample.toml"), samples=plain).fires_list.status == "read"
    unset = fires_install(tmp_path, "unset")
    config = unset / "gantry.sample.toml"
    config.write_bytes(config.read_bytes().replace(b'fires_list = "fires.md"\n', b""))
    code3, out3, stdout3, _ = demo_build(tmp_path, monkeypatch, copy=unset)
    assert code3 == 0 and without_strategic(written(out3)) == without_strategic(written(out))
    assert b"no data: no fires list configured" in written(out3)["strategic.html"]
    assert stdout3.splitlines()[-1] == "fires: 4; sources not fully read: 2"
    result = build_all(load_config(config), samples=unset)
    assert (result.fires_list.status, result.fires_list.reason) == ("not_configured", "fires_list is not set")


def test_collector_failure_is_isolated(tmp_path, monkeypatch):
    real = build.read_site_records

    def refuse(roots, planning_dir, *, site=None):
        if site == BUSY:
            raise RuntimeError("no records")
        return real(roots, planning_dir, site=site)

    monkeypatch.setattr(build, "read_site_records", refuse)
    code, out, stdout, stderr = demo_build(tmp_path, monkeypatch)
    assert code == 0, stderr
    assert (out / "sites" / f"{BUSY}.html").is_file()
    assert stdout.splitlines()[-1] == "fires: 5; sources not fully read: 2"  # DEMO_FIRES_LINE less BUSY's STUCK fires
    result = sample_build(tmp_path / "again")
    (status,) = [s for s in result.fires.sources if s.source == "stuck" and s.site == BUSY]
    assert (status.status, status.reason) == ("not_read", "records read failed: RuntimeError")
    assert result.records[BUSY].status == "not_read"


def test_build_fires_fault_degrades(tmp_path, monkeypatch):
    from gantry.fires import SourceStatus

    plain = fires_install(tmp_path, "plain")
    faulty = fires_install(tmp_path, "faulty")
    _, out, _, _ = demo_build(tmp_path, monkeypatch, copy=plain)

    def fail(*args, **kwargs):
        raise RuntimeError("collector")

    monkeypatch.setattr(build, "collect_fires", fail)
    code, out2, stdout, stderr = demo_build(tmp_path, monkeypatch, copy=faulty)
    assert code == 0, stderr
    assert without_strategic(written(out)) == without_strategic(written(out2))
    assert b"incomplete: fires step not read" in written(out2)["strategic.html"]
    assert stdout.splitlines()[-1] == "fires: 0; sources not fully read: 1"
    assert "gantry: warning: fires not collected: fires step failed: RuntimeError" in stderr.splitlines()
    result = build_all(load_config(faulty / "gantry.sample.toml"), samples=faulty)
    assert result.fires.sources == (SourceStatus("fires", None, "not_read", "fires step failed: RuntimeError"),)
    assert result.fires.fires == ()


def test_fires_step_fault_before_any_read(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("fires list")

    monkeypatch.setattr(build, "read_fires_list", fail)
    code, _, stdout, stderr = demo_build(tmp_path, monkeypatch)
    assert code == 0, stderr
    assert stdout.splitlines()[-1] == "fires: 0; sources not fully read: 1"
    assert "gantry: warning: fires not collected: fires step failed: RuntimeError" in stderr.splitlines()
    result = sample_build(tmp_path / "again")
    # The records are read before the pages (#173), so a fires-step fault keeps them.
    assert result.fires_list is None
    assert set(result.records) == {site.name for site in result.sites if site.planning_dir is not None}
    assert [s.reason for s in result.fires.sources] == ["fires step failed: RuntimeError"]


def test_no_home_defers_to_32():
    # The homes that deferred to #32 say what the build does now. Split, so
    # this test is not itself a hit of the sweep's grep.
    stale = ("yet (#" + "32)", "(#" + "32) will", "No build reads the " + "records yet", "does not read " + "it yet",
             "#" + "32's build will", "#" + "32 adds the read", "no build reads the fires " + "list yet",
             "will " + "read each site once", "(#" + "32) merges", "does not read " + "the file yet")
    assert_no_stale(stale, [*swept_files(), ROOT / "samples" / "README.md", ROOT / "tests" / "test_cli.py",
                            ROOT / "CHANGELOG.md"])
    for rel in BUILD_HOMES:
        text = " ".join((ROOT / rel).read_bytes().decode("utf-8").split())
        assert "fires:" in text, rel
    # The changelog names both stdout changes: a new last line, and a line on a failed build.
    # Read CHANGELOG.md: a release compiles changelog.d/32.added.md into it and deletes the file.
    entry = " ".join((ROOT / "CHANGELOG.md").read_bytes().decode("utf-8").split())
    assert "last stdout line" in entry and "even when the build exits 1" in entry


# --- the week view page (#38) ------------------------------------------------------------


def week_link_count(plan):
    """The links the week view holds, counted off the plan file: each placement,
    then each site's P0 and unplanned entries."""
    n = sum(len(block["leaves"]) for day in plan["days"] for block in day["blocks"])
    n += sum(len(day["lanes_row"]) for day in plan["days"])
    return n + sum(len(site["p0"]) + len(site["unplanned"]) for site in plan["sites"])


def test_week_page_links_land(tmp_path, monkeypatch):
    import json

    code, out, _, stderr = demo_build(tmp_path, monkeypatch)
    assert code == 0, stderr
    text = (out / "week.html").read_bytes().decode("utf-8")
    links = page_links(text)
    n = week_link_count(json.loads((out / "week-plan.json").read_bytes()))
    assert n > 0 and len(links) == n
    assert all(lands(out, "week.html", href) for href in links)
    assert f"bakery: not built: {NO_WBS_REASON}" in text
    assert f"marsh: not built: {UNREADABLE_REASON}" in text


def test_week_hostile_key_href_lands(tmp_path):
    from test_planner import Leaf, wbs_md

    key = "a b<c>&é"
    hostile = wbs_md(Leaf(f"  - [ ] Hostile {{issue: {key}, est: 2h}}\n", hp.node("Hostile", issue=key, est="2h")),
                     report=((4, "issue_not_id", f"issue '{key}' is not an issue id; its issue dir is not read"),))
    copy = plan_install(tmp_path, "binds_lanes", wbs={"alpha": hostile})
    code, _, stderr = run_main("--config", str(copy / "gantry.toml"))
    assert code == 0, stderr
    out = copy / "out"
    from test_worksite_page import tree

    t = tree((out / "week.html").read_bytes().decode("utf-8"))
    (line,) = [i for i, el in enumerate(t.elements) if el["tag"] == "li" and el["attrs"].get("data-leaf") == key
               and "leaf" in el["classes"]]
    # The link sits in the box's title paragraph (#170).
    (href,) = [el["attrs"]["href"] for el in t.elements if el["tag"] == "a"
               and "ttl" in t.elements[el["parent"]]["classes"] and t.elements[el["parent"]]["parent"] == line]
    assert href == leaf_href("alpha", key, from_top=True)
    assert lands(out, "week.html", href)
    assert page_ids((out / "sites" / "alpha.html").read_bytes().decode("utf-8")).count(leaf_anchor(key)) == 1


def test_site_writes_no_week_page(tmp_path, monkeypatch):
    copy = copy_samples(tmp_path)
    (copy / "out").mkdir(exist_ok=True)
    (copy / "out" / "week.html").write_bytes(b"an older week page")
    code, out, stdout, _ = demo_build(tmp_path, monkeypatch, "--site", BUSY, copy=copy)
    assert code == 0
    assert stdout == f"{out / 'sites' / f'{BUSY}.html'}\n"
    assert (out / "week.html").read_bytes() == b"an older week page"


def test_demo_writes_week_page(tmp_path, monkeypatch):
    code, out, stdout, stderr = demo_build(tmp_path, monkeypatch)
    assert code == 0, stderr
    text = (out / "week.html").read_bytes().decode("utf-8")
    assert '<p class="label stamp">sample data as of 2026-01-20</p>' in text
    printed = stdout.splitlines()
    # The strategic page (#23) is written between the week view and the index.
    assert printed.index(str(out / "week.html")) + 2 == printed.index(str(out / "index.html"))


def view_links(out):
    from test_worksite_page import find, text_of, tree

    t = tree((out / "index.html").read_bytes().decode("utf-8"))
    (box,) = [i for i, el in enumerate(t.elements) if el["attrs"].get("id") == "view-links"]
    return [(text_of(t, i), t.elements[i]["attrs"]["href"]) for i in find(t, "a", within=box)]


def test_week_write_failure_exits_1(tmp_path):
    copy = plan_install(tmp_path)
    out = copy / "out"
    (out / "week.html").mkdir(parents=True)
    code, stdout, stderr = run_main("--config", str(copy / "gantry.toml"))
    assert code == 1
    (line,) = stderr.splitlines()
    prefix = "gantry: error: cannot write week.html: "
    assert line.startswith(prefix) and line[len(prefix):].strip()
    assert str(tmp_path) not in line and "/" not in line
    assert stdout.splitlines() == [str(out / "sites" / "a.html"), str(out / "sites" / "b.html"),
                                   str(out / "strategic.html"), str(out / "index.html"), str(out / "week-plan.json"),
                                   "fires: 0; sources not fully read: 1"]  # no fires_list (#32)
    assert view_links(out) == [("Strategic page", "strategic.html")]
    assert (out / "week.html").is_dir() and list((out / "week.html").iterdir()) == []


def test_week_write_refused_outside_roots(tmp_path, monkeypatch):
    from gantry.roots import OutsideRoots

    real = build.write_page

    def refuse(roots, folder, name, text):
        if name == "week":
            raise OutsideRoots("output_dir", "write")
        return real(roots, folder, name, text)

    monkeypatch.setattr(build, "write_page", refuse)
    copy = plan_install(tmp_path)
    code, _, stderr = run_main("--config", str(copy / "gantry.toml"))
    assert code == 1
    assert stderr.splitlines() == ["gantry: error: cannot write week.html: " + str(OutsideRoots("output_dir", "write"))]
    assert view_links(copy / "out") == [("Strategic page", "strategic.html")]


@pytest.mark.parametrize(("error", "line"), [
    (KeyError("x"), "gantry: error: cannot render week.html: KeyError: 'x'"),
    (ValueError(""), "gantry: error: cannot render week.html: ValueError"),
])
def test_week_render_failure_exits_1(tmp_path, monkeypatch, error, line):
    from gantry.views import week

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(week, "render_week", fail)
    copy = plan_install(tmp_path)
    out = copy / "out"
    code, stdout, stderr = run_main("--config", str(copy / "gantry.toml"))
    assert code == 1
    assert stderr.splitlines() == [line]
    assert stdout.splitlines() == [str(out / "sites" / "a.html"), str(out / "sites" / "b.html"),
                                   str(out / "strategic.html"), str(out / "index.html"), str(out / "week-plan.json"),
                                   "fires: 0; sources not fully read: 1"]  # no fires_list (#32)
    assert not (out / "week.html").exists()
    assert view_links(out) == [("Strategic page", "strategic.html")]


def test_stale_week_page_survives_render_failure(tmp_path, monkeypatch):
    from gantry.views import week

    monkeypatch.setattr(week, "render_week", lambda *a, **k: (_ for _ in ()).throw(KeyError("x")))
    copy = plan_install(tmp_path)
    out = copy / "out"
    out.mkdir()
    (out / "week.html").write_bytes(b"an older week page")
    code, _, _ = run_main("--config", str(copy / "gantry.toml"))
    assert code == 1
    assert (out / "week.html").read_bytes() == b"an older week page"
    assert view_links(out) == [("Strategic page", "strategic.html")]


def test_week_and_plan_errors_order(tmp_path):
    copy = plan_install(tmp_path)
    out = copy / "out"
    (out / "week.html").mkdir(parents=True)
    (out / "week-plan.json").mkdir()
    code, _, stderr = run_main("--config", str(copy / "gantry.toml"))
    assert code == 1
    lines = stderr.splitlines()
    assert [line.split(": ")[2] for line in lines] == ["cannot write week.html", "cannot write week-plan.json"]


def test_build_homes_name_the_week_page(capsys):
    # Every place that says what the build writes or prints names the week view (#38).
    for rel in BUILD_HOMES:
        text = " ".join((ROOT / rel).read_bytes().decode("utf-8").split())
        assert "week.html" in text, rel
    for rel in ("gantry/views/index.py", "gantry/build.py"):
        assert "week.html" in (ROOT / rel).read_bytes().decode("utf-8"), rel
    from gantry.cli import main

    with pytest.raises(SystemExit):
        main(["build", "--help"])
    assert "<output_dir>/week.html" in " ".join(capsys.readouterr().out.split())


def test_no_home_defers_to_38():
    # The repo's deferrals to the week view are reworded now that it reads the plan.
    # Split, so this test is not itself a hit.
    stale = ("is " + "#38 and does not exist", "the week view (" + "#38) will", "provisional until " + "#38",
             "the first view reads it (" + "#38)", "view reads them (" + "#38)", "#24 or " + "#33, the first to read it")
    assert_no_stale(stale, [*swept_files(), ROOT / "tests" / "test_plan_file.py"])


# --- the site page models the week view reads (#170) --------------------------------------


def test_page_models_cover_rendered_sites(tmp_path):
    from gantry import planner

    result = sample_build(tmp_path)
    assert set(result.page_models) == set(result.pages)
    assert list(result.page_models) == planner._built_sites(result)


def test_page_models_survive_write_failure(tmp_path, monkeypatch):
    """A site page that fails to write keeps its slack on the week view."""
    result = sample_build(tmp_path)
    placed = set(placed_nodes(result.plan))
    slack = {(site, leaf["key"]): leaf["sclass"] for site, m in result.page_models.items() for leaf in m["leaves"]
             if leaf["sclass"] is not None and (site, leaf["key"]) in placed}
    site = next(s for s, _ in slack)
    real = build.write_page

    def refuse(roots, folder, name, text):
        if name == site:
            raise OSError("refused")
        return real(roots, folder, name, text)

    monkeypatch.setattr(build, "write_page", refuse)
    written, _, _ = build.write_all(result)
    assert site not in written.pages and site in written.page_models
    from test_worksite_page import tree

    t = tree(Path(result.config.output_dir, "week.html").read_bytes().decode("utf-8"))
    boxes = [el for el in t.elements if el["tag"] == "li" and "leaf" in el["classes"]
             and el["attrs"].get("data-site") == site]
    expect = {key: cls for (s, key), cls in slack.items() if s == site}
    assert expect and {el["attrs"]["data-leaf"] for el in boxes} >= set(expect)
    for el in boxes:
        if el["attrs"]["data-leaf"] in expect:
            assert expect[el["attrs"]["data-leaf"]] in el["classes"]


# --- the completion marker (#35) ------------------------------------------------------


@pytest.mark.parametrize("data", [
    b"",
    b"null",
    b"[]",
    b"{}",
    b'{"build_version": 2, "exit": 0}',
    b'{"build_version": true, "exit": 0}',
    b'{"build_version": "1", "exit": 0}',
    b'{"build_version": 1, "exit": true}',
    b'{"build_version": 1, "exit": 0.0}',
    b'{"build_version": 1, "exit": null}',
    b'{"build_version": 1, "exit": 0, "extra": 1}',
    b'{"build_version": 1}',
    b'{"exit": 0}',
    b'{"build_version": 1, "exit": 0, "exit": 1}',
    b'{"build_version": 1, "exit": NaN}',
    b'{"build_version": 1, "exit": 0',
    b"\xff",
    b"\xef\xbb\xbf" + build.marker_bytes(0),
    b"[" * 100_000,
], ids=lambda data: repr(data[:48]))
def test_marker_exit_refuses(data):
    assert build.marker_exit(data) is None


def test_marker_exit_table():
    assert build.marker_bytes(0) == b'{"build_version": 1, "exit": 0}\n'
    assert build.marker_exit(build.marker_bytes(0)) == 0
    assert build.marker_exit(build.marker_bytes(1)) == 1
    assert build.MARKER_FILE == "build.json"


@pytest.mark.parametrize("data", [b"\xff", b'{"a": 1, "a": 2}', b"NaN", b"[Infinity]", b"1e400",
                                  b"9" * 5000, b"[" * 100_000, b"{"], ids=lambda data: repr(data[:48]))
def test_strict_json_loads_raises_only_value_error(data):
    with pytest.raises(ValueError):
        build.strict_json_loads(data)


# --- one records read, before the pages (#173) ----------------------------------------------

from gantry import devagent_records as dr  # noqa: E402
from gantry import phases  # noqa: E402

UNKNOWN5 = ["unknown"] * 5


def test_full_build_reads_records_before_pages(tmp_path, monkeypatch):
    log = []
    spy_on(monkeypatch, build, "read_site_records", before=lambda *a, site=None, **k: log.append(("read", site)))
    models = spy_on(monkeypatch, worksite, "page_model",
                    before=lambda site, *a, records=None, **k: log.append(("model", site, records)))
    collected = spy_on(monkeypatch, build, "collect_fires")
    result = sample_build(tmp_path)
    first_model = next(i for i, entry in enumerate(log) if entry[0] == "model")
    reads = [i for i, entry in enumerate(log) if entry[0] == "read"]
    assert len(reads) == 6 and max(reads) < first_model
    assert len(models) >= len(result.pages) >= 1
    for entry in log[first_model:]:
        assert entry[0] == "model" and entry[2] is result.records[entry[1]], entry[1]
    assert collected[0][0][3] is result.records


def harbor_failure(monkeypatch, leg):
    real = build.read_site_records
    reason = "planning directory cannot be listed: Permission denied"

    def patched(roots, planning_dir, *, site=None):
        if site == BUSY:
            if leg == "raises":
                raise RuntimeError("no records")
            return dr.SiteRecords(dr.NOT_READ, reason, {}, {})
        return real(roots, planning_dir, site=site)

    monkeypatch.setattr(build, "read_site_records", patched)
    return "records read failed: RuntimeError" if leg == "raises" else reason


@pytest.mark.parametrize("leg", ["raises", "fails"])
def test_records_read_failure_keeps_every_page(tmp_path, monkeypatch, leg):
    plain = sample_build(tmp_path / "plain")
    reason = harbor_failure(monkeypatch, leg)
    code, out, _, stderr = demo_build(tmp_path / "demo", monkeypatch)
    assert code == 0, stderr
    assert sorted(p.name for p in (out / "sites").iterdir()) == sorted(f"{name}.html" for name in plain.pages)
    result = sample_build(tmp_path / "patched")
    m = result.page_models[BUSY]
    with_issue = [leaf for leaf in m["leaves"] if leaf["issue"] is not None]
    assert len(with_issue) >= 5
    for leaf in with_issue:
        assert leaf["phases"] == UNKNOWN5, leaf["key"]
        assert leaf["phases_why"] == f"devagent records not read: {reason}"
    for leaf in m["leaves"]:
        if leaf["issue"] is None:
            assert leaf["phases"] is None and leaf["phases_why"] is None
    assert m["panel"][-1] == worksite.RECORDS_LINE.format(reason=reason)
    counts = {s.name: s.report_count for s in result.summaries}
    before = {s.name: s.report_count for s in plain.summaries}
    assert counts == {**before, BUSY: before[BUSY] + 1}
    assert worksite.RECORDS_LINE.format(reason=reason) not in plain.page_models[BUSY]["panel"]
    # The page (B2, #173): each issue leaf's full box states the reason in its tooltip.
    from test_worksite_page import find, text_of, tree

    t = tree((out / "sites" / f"{BUSY}.html").read_bytes().decode("utf-8"))
    tips = [text_of(t, kids[0]) for i in find(t, "g", ["full-form"])
            if (kids := [j for j, el in enumerate(t.elements) if el["parent"] == i])
            and t.elements[kids[0]]["tag"] == "title"]
    drawn = [leaf for leaf in with_issue if leaf["key"] in m["network"]["nodes"]]
    assert len(tips) == len(drawn) >= 1
    assert all(tip.endswith(f" \u00b7 phases unknown: devagent records not read: {reason}") for tip in tips)


@pytest.mark.parametrize("leg", ["raises", "fails"])
def test_site_build_records_read_failure_keeps_page(tmp_path, monkeypatch, leg):
    reason = harbor_failure(monkeypatch, leg)
    code, out, _, stderr = demo_build(tmp_path, monkeypatch, "--site", BUSY)
    assert code == 0, stderr
    page = (out / "sites" / f"{BUSY}.html").read_bytes().decode("utf-8")
    assert f"phases unknown: devagent records not read: {reason}" in page
    assert worksite.RECORDS_LINE.format(reason=reason) in page
    assert "no records" not in page  # the exception's message stays off the page


def test_page_model_records_switch():
    data = page_tree_site()
    plain = worksite.page_model("alpha", data, default_unit_hours=6)
    assert all(leaf["phases"] is None and leaf["phases_why"] is None for leaf in plain["leaves"])
    assert plain == worksite.page_model("alpha", data, default_unit_hours=6, records=phases.NO_RECORDS)
    for bad in (None, {}, "records"):
        with pytest.raises(TypeError):
            worksite.page_model("alpha", data, default_unit_hours=6, records=bad)
    unread = worksite.page_model("alpha", data, default_unit_hours=6,
                                 records=dr.SiteRecords(dr.NOT_READ, "planning directory does not exist", {}, {}))
    assert unread["panel"][-1] == ("Phase rows show unknown: this site's devagent records were not read "
                                   "(planning directory does not exist)")
    assert len(unread["panel"]) == len(plain["panel"]) + 1
    read = worksite.page_model("alpha", data, default_unit_hours=6, records=dr.SiteRecords(dr.READ, None, {}, {}))
    assert read["panel"] == plain["panel"]
    assert all(leaf["phases"] is None for leaf in read["leaves"])


def page_tree_site():
    import helpers_portfolio as hp

    return hp.site(hp.node("A", issue="Issue-1"), hp.node("B"))


def test_render_sites_missing_key_fails_closed(tmp_path):
    from gantry.sites import config_roots
    from gantry.trace import load_catalog

    copy = copy_samples(tmp_path)
    config = load_config(copy / "gantry.sample.toml")
    roots = config_roots(config)
    sites, projects = matched_sites(config, roots)
    inputs = build.read_sites(config, sites, roots=roots, projects=projects)[0]
    _, models, failed = build.render_sites(config, {BUSY: inputs[BUSY]}, "", roots=roots,
                                           catalog=load_catalog(roots, config, sites), records={}, devagent={})
    assert failed == {}
    leaves = [leaf for leaf in models[BUSY]["leaves"] if leaf["issue"] is not None]
    assert leaves and all(leaf["phases"] == UNKNOWN5 for leaf in leaves)
    assert {leaf["phases_why"] for leaf in leaves} == {"devagent records not read: records not read for this site"}


def test_records_reasons_name_no_path(tmp_path, monkeypatch):
    # Every NOT_READ constructor in read_site_records, then a reason that holds the path.
    from gantry.sites import config_roots, load_sites

    copy = copy_samples(tmp_path)
    config = load_config(copy / "gantry.sample.toml")
    roots = config_roots(config)
    sites = [s for s in load_sites(config, roots)[0] if s.planning_dir is not None]
    site = next(s for s in sites if s.name == BUSY)
    paths = [str(r.given) for r in roots.roots] + [str(r.path) for r in roots.roots] + [str(site.planning_dir)]
    real = build.read_site_records
    planted = {"value": None}

    def patched(roots, planning_dir, *, site=None):
        return planted["value"] if site == BUSY and planted["value"] is not None else real(roots, planning_dir,
                                                                                          site=site)

    monkeypatch.setattr(build, "read_site_records", patched)
    cases = [dr.SiteRecords(dr.NOT_READ, dr.PLANNING_MISSING, {}, {}),
             dr.SiteRecords(dr.NOT_READ, dr.PLANNING_NOT_DIR, {}, {}),
             dr.SiteRecords(dr.NOT_READ, dr.PLANNING_CANNOT_LIST.format(error=f"refused: {site.planning_dir}"), {},
                            {})]
    for case in cases:
        planted["value"] = case
        got = build.read_records(roots, sites)[BUSY]
        assert got.status == dr.NOT_READ
        assert not any(path in got.reason for path in paths), got.reason
    assert "refused: " in got.reason


def test_site_build_reads_one_sites_records(tmp_path, monkeypatch):
    lists = spy_on(monkeypatch, build, "read_fires_list")
    named = []
    spy_on(monkeypatch, build, "read_site_records", before=lambda *a, site=None, **k: named.append(site))
    code, _, stdout, stderr = demo_build(tmp_path, monkeypatch, "--site", "quarry")
    assert code == 0, stderr
    assert (lists, named) == ([], ["quarry"])
    assert not [line for line in stdout.splitlines() if line.startswith("fires:")]


# --- the time thieves' history reads and wiring (#29) ------------------------------------------


def spy_attach(monkeypatch):
    """Record each model thieves.attach returns: the one the strategic page is rendered from."""
    from gantry import thieves

    models = []
    real = thieves.attach

    def spy(*args, **kwargs):
        models.append(real(*args, **kwargs))
        return models[-1]

    monkeypatch.setattr(thieves, "attach", spy)
    return models


def test_strategic_reads_history_once_per_built_site(tmp_path, monkeypatch):
    from gantry import wbs_history

    copy = copy_samples(tmp_path)
    portfolio = copy / "portfolio.md"
    text = portfolio.read_bytes().decode("utf-8")
    portfolio.write_bytes(text.replace("portfolio_version = 1\n", "portfolio_version = 1\n\n[thresholds]\n"
                                       "neglect_weeks = 2\n", 1).encode("utf-8"))
    calls = []
    real = wbs_history.last_state_change

    def spy(roots, site, config, as_of, weeks, cache):
        calls.append((site.name, as_of, weeks, cache))
        return real(roots, site, config, as_of, weeks, cache)

    monkeypatch.setattr(wbs_history, "last_state_change", spy)
    real_write = build.write_page

    def refuse(roots, folder, name, text):
        if name == OWN_UNIT:
            raise OSError(13, "refused")
        return real_write(roots, folder, name, text)

    monkeypatch.setattr(build, "write_page", refuse)
    config = load_config(copy / "gantry.sample.toml")
    result = build_all(config, samples=copy)
    built = [s.name for s in result.summaries if s.status == "built"]
    assert OWN_UNIT in built and len(built) == 4
    result, _, error = build.write_all(result)
    assert error is None and result.strategic_error is None
    assert [c[0] for c in calls] == [name for name in built if name != OWN_UNIT]
    assert all(c[1] == config.as_of and c[2] == 2 for c in calls)
    assert len({id(c[3]) for c in calls}) == 1  # one cache for the build


DOING_A = hp.source(b"# A\n\n- [~] Doing {issue: Issue-1}\n", hp.node("Doing", glyph="~", issue="Issue-1"), start=3)
TODO_B = hp.source(b"# B\n\n- [ ] Todo {issue: Issue-2}\n", hp.node("Todo", issue="Issue-2"), start=3)


def test_build_without_git(tmp_path, monkeypatch):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    config_path = write_site_tree(tmp_path / "tree", {"a": DOING_A, "b": TODO_B})
    models = spy_attach(monkeypatch)
    monkeypatch.setenv("PATH", str(empty))
    result, _, error = build.write_all(build_all(load_config(config_path)))
    assert error is None and result.strategic_error is None
    (model,) = models
    for name in ("a", "b"):
        got = model.panels[name].thieves
        assert got.neglect.text == "no data: git not found"
        assert "parked, not counted: no data: git not found" in [p.text for p in got.wip.parts]
    page = (config_path.parent / "out" / "strategic.html").read_bytes().decode("utf-8")
    assert page.count('data-figure="thief-neglect"') == 2
    assert page.count(">no data: git not found</span>") == 2
    assert page.count("parked, not counted: no data: git not found") >= 2


@pytest.mark.parametrize("no_data", ["shallow history", "git failed: bad object"])
def test_build_with_shallow_or_failed_history(tmp_path, monkeypatch, no_data):
    from gantry import wbs_history

    def answer(roots, site, config, as_of, weeks, cache):
        return wbs_history.no_data_result(wbs_history.NoData(no_data), wbs_history.NoData(no_data))

    monkeypatch.setattr(wbs_history, "last_state_change", answer)
    models = spy_attach(monkeypatch)
    config_path = write_site_tree(tmp_path / "tree", {"a": DOING_A})
    result, _, error = build.write_all(build_all(load_config(config_path)))
    assert error is None and result.strategic_error is None
    assert models[0].panels["a"].thieves.neglect.text == f"no data: {no_data}"
    page = (config_path.parent / "out" / "strategic.html").read_bytes().decode("utf-8")
    assert f'data-source="WBS.md history in the planning repository">no data: {no_data}</span>' in page


def test_history_read_that_raises_is_no_data(tmp_path, monkeypatch):
    from gantry import wbs_history

    def boom(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(wbs_history, "last_state_change", boom)
    models = spy_attach(monkeypatch)
    config_path = write_site_tree(tmp_path / "tree", {"a": DOING_A})
    result, _, error = build.write_all(build_all(load_config(config_path)))
    assert error is None and result.strategic_error is None
    assert models[0].panels["a"].thieves.neglect.text == "no data: history read failed: RuntimeError"


def test_build_prints_failed_thieves(tmp_path, monkeypatch):
    from gantry import thieves

    config_path = write_site_tree(tmp_path / "tree", {"a": DOING_A, "b": TODO_B})
    code, _, before = run_main("--config", str(config_path))
    real = thieves.wip_thief

    def wip(data, history, records, threshold):
        if data.leaves[0].issue == "Issue-2":
            raise RuntimeError("boom")
        return real(data, history, records, threshold)

    monkeypatch.setattr(thieves, "wip_thief", wip)
    again, _, after = run_main("--config", str(config_path))
    assert again == code
    added = [line for line in after.splitlines() if line not in before.splitlines()]
    assert added == ["gantry: warning: site b: thieves not derived: RuntimeError"]


# --- devagent's declared capacity (#39) ----------------------------------------------------------


def capacity_install(tmp_path, **changes):
    """A copy of tests/fixtures/capacity/install (sites a, b and c), with test_planner's install changes."""
    from test_planner import CAPACITY_INSTALL, install

    return install(tmp_path, CAPACITY_INSTALL, **changes)


def test_build_passes_declared_capacity(tmp_path, monkeypatch):
    from gantry import capacity
    from gantry.planner import CAPACITY_SOURCE, CAPACITY_SOURCE_DEVAGENT
    from test_planner import capacity_config, declares, plan_from

    # The demo: the sample devagent config declares capacity, and the build plans from it,
    # not from the default.
    plan = sample_build(tmp_path / "demo").plan
    assert (plan.status, plan.capacity["declaration"]) == ("planned", "used")
    assert [pool["capacity_source"] for pool in plan.pools] == [CAPACITY_SOURCE_DEVAGENT] * 2
    assert CAPACITY_SOURCE_DEVAGENT != CAPACITY_SOURCE

    # Whatever the config declares, the build's plan is the planner's of the reader's result.
    for devagent, declaration in ((declares(), "used"), (capacity_config("not_declared"), "not_declared"),
                                  (capacity_config("order"), "invalid")):
        copy = capacity_install(tmp_path, devagent=devagent)
        plan = build_all(load_config(copy / "gantry.toml")).plan
        assert (plan.status, plan.capacity["declaration"]) == ("planned", declaration)
        assert plan == plan_from(copy)
    assert [entry["kind"] for entry in plan.report] == ["capacity_invalid"] * 9 + ["capacity_unknown_key"] * 2

    # A full build reads the declaration once and hands the planner what it read.
    copy = capacity_install(tmp_path, devagent=declares())
    read = spy_on(monkeypatch, capacity, "resolve_capacity")
    passed = []
    spy_on(monkeypatch, build, "plan_week", before=lambda *args, **kw: passed.append(kw.get("capacity")))
    result = build_all(load_config(copy / "gantry.toml"))
    assert len(read) == 1 and len(passed) == 1 and passed[0] is read[0][1]
    assert passed[0].usable and result.plan.capacity["declaration"] == "used"
    # The command line's full build too, and its plan file holds it.
    del read[:], passed[:]
    code, _, stderr = run_main("--config", str(copy / "gantry.toml"))
    assert code == 0, stderr
    assert len(read) == 1 and len(passed) == 1 and passed[0] is read[0][1]
    document = json.loads((copy / "out" / "week-plan.json").read_bytes().decode("utf-8"))
    assert document["capacity"]["declaration"] == "used"
    assert [pool["capacity_source"] for pool in document["pools"]] == [CAPACITY_SOURCE_DEVAGENT] * 2
    # A --site build plans no week: it reads no declaration.
    del read[:], passed[:]
    code, _, stderr = run_main("--config", str(copy / "gantry.toml"), "--site", "a")
    assert code == 0, stderr
    assert read == [] and passed == []


def test_invalid_declaration_warns(tmp_path):
    # An invalid declaration is not used, its pauses included, so the full build says so on
    # stderr; the exit code is unchanged. A used one prints no capacity line.
    from gantry.cli import CAPACITY_INVALID_LINE
    from test_planner import PAUSED_C, capacity_config, declares

    def build_lines(devagent):
        copy = capacity_install(tmp_path, devagent=devagent)
        code, _, stderr = run_main("--config", str(copy / "gantry.toml"))
        assert code == 0, stderr
        document = json.loads((copy / "out" / "week-plan.json").read_bytes().decode("utf-8"))
        return [line for line in stderr.splitlines() if "capacity declaration" in line], document["capacity"]

    lines, capacity = build_lines(declares(tail=PAUSED_C))
    assert (lines, capacity["declaration"], capacity["paused"]) == ([], "used", ["c"])
    # One broken rule anywhere drops the whole declaration: c is no longer paused.
    lines, capacity = build_lines(declares(total=0, tail=PAUSED_C))
    assert (capacity["declaration"], capacity["paused"]) == ("invalid", [])
    assert lines == [CAPACITY_INVALID_LINE.format(count="1 violation")]
    lines, capacity = build_lines(capacity_config("order"))
    assert lines == [CAPACITY_INVALID_LINE.format(count="9 violations")]
    assert lines[0] == "gantry: warning: devagent's capacity declaration is invalid (9 violations); it is not used"


def test_capacity_read_failure_stops_the_build(tmp_path, monkeypatch):
    # The devagent config is read again for the declaration. One that can no longer be read is
    # the ConfigError the site list's own read of it is: the build stops and writes nothing. It
    # is never a capacity violation, and never a plan made as if nothing were declared.
    from gantry import cli
    from gantry.config import ConfigError
    from test_planner import declares

    copy = capacity_install(tmp_path, devagent=declares())
    devagent = copy / "devagent.toml"
    text = devagent.read_bytes()

    def gone(_):
        devagent.unlink()

    spy_on(monkeypatch, build, "site_list", after=gone)
    config = load_config(copy / "gantry.toml")
    with pytest.raises(ConfigError) as refused:
        build_all(config)
    assert str(refused.value).startswith(f"{config.devagent_config}: cannot read the devagent config: ")
    assert not devagent.exists()
    devagent.write_bytes(text)
    code, stdout, stderr = run_main("--config", str(copy / "gantry.toml"))
    assert (code, stdout) == (1, "")
    (line,) = stderr.splitlines()
    assert line.startswith(f"gantry: error: {config.devagent_config}: cannot read the devagent config: ")
    assert not devagent.exists() and not (copy / "out").exists()
    # A --site build reads no declaration, so the same loss after its site list does not stop it.
    devagent.write_bytes(text)
    spy_on(monkeypatch, cli, "site_list", after=gone)
    code, _, stderr = run_main("--config", str(copy / "gantry.toml"), "--site", "a")
    assert code == 0, stderr
    assert not devagent.exists() and sorted(written_bytes(copy / "out")) == ["sites/a.html"]


def test_capacity_missing_line_is_true_beside_a_declared_plan(tmp_path):
    # The portfolio file sets no attention_hours, so its own check reports capacity_missing, and a
    # full build lists that line on the strategic page. devagent's declaration is used, so the same
    # build plans the week: what the line says of the planner must be true beside that plan.
    from test_planner import capacity_portfolio, declares
    from test_worksite_page import by_id, find, text_of, tree

    copy = capacity_install(tmp_path, devagent=declares(total=30), portfolio=capacity_portfolio("no_hours"))
    code, _, stderr = run_main("--config", str(copy / "gantry.toml"))
    assert code == 0, stderr
    document = json.loads((copy / "out" / "week-plan.json").read_bytes().decode("utf-8"))
    assert (document["status"], document["capacity"]["attention"]["rule"]) == ("planned", "even_spread")
    t = tree((copy / "out" / "strategic.html").read_bytes().decode("utf-8"))
    lines = [text_of(t, li) for li in find(t, "li", within=by_id(t, "report"))]
    missing = [line for line in lines if "capacity_missing" in line]
    assert missing == [
        "settings: line 18: capacity_missing (advisory): capacity.attention_hours: no capacity.attention_hours, so "
        "the week planner has no capacity to plan into unless devagent's capacity declaration is used"]
    # Nothing declared, the same file gives no plan, and the same line is as true.
    bare = capacity_install(tmp_path, portfolio=capacity_portfolio("no_hours"))
    code, _, stderr = run_main("--config", str(bare / "gantry.toml"))
    assert code == 0, stderr
    document = json.loads((bare / "out" / "week-plan.json").read_bytes().decode("utf-8"))
    assert (document["status"], document["capacity"]["declaration"]) == ("no_plan", "not_declared")
    t = tree((bare / "out" / "strategic.html").read_bytes().decode("utf-8"))
    assert [line for line in (text_of(t, li) for li in find(t, "li", within=by_id(t, "report")))
            if "capacity_missing" in line] == missing


def test_declared_plan_writes_byte_identical_files(tmp_path, monkeypatch):
    from gantry import cli
    from test_planner import PAUSED_C, RATIO, declares

    # A used declaration that leaves a mark wherever one can: hours scaled onto the portfolio
    # file's weekdays, a ratio, an unknown key, a paused site and a token budget.
    devagent = declares(more=RATIO % 0.25 + "max_lane = 3\n", tail=PAUSED_C)
    # The stamp holds the build's minute, so both builds read one clock.
    now = datetime(2026, 1, 19, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(cli, "build_all", lambda config: build_all(config, now=now))
    runs = []
    for _ in range(2):
        copy = capacity_install(tmp_path, devagent=devagent)
        code, _, stderr = run_main("--config", str(copy / "gantry.toml"))
        assert code == 0, stderr
        runs.append(written_bytes(copy / "out"))
    document = json.loads(runs[0]["week-plan.json"].decode("utf-8"))
    assert document["capacity"]["declaration"] == "used"
    assert document["status"] == "planned" and document["capacity"]["attention"]["rule"] == "scaled"
    assert document["capacity"]["attention"]["factor"] != 1 and document["capacity"]["paused"] == ["c"]
    assert document["capacity"]["declared_ratio"]["used"] and document["capacity"]["token_budget"] is not None
    assert [entry["kind"] for entry in document["report"]] == ["capacity_unknown_key"]
    assert sorted(runs[0]) == ["index.html", "sites/a.html", "sites/b.html", "sites/c.html", "strategic.html",
                               "week-plan.json", "week.html"]
    assert runs[0] == runs[1]
    assert b"built 2026-01-19 09:00 UTC" in runs[0]["week.html"]
    assert all(str(tmp_path).encode("utf-8") not in data for data in runs[0].values())


# --- the build reads devagent's portfolio JSON (#12) ------------------------------------------

import builtins  # noqa: E402
import io  # noqa: E402

import helpers_devagent as hd  # noqa: E402
import sample_portfolio as sp  # noqa: E402
from gantry.build import (  # noqa: E402
    AGREES,
    CONFIG_ONLY,
    DISAGREE,
    DOCUMENT_ONLY,
    OUT_OF_DATE,
    own_status,
)
from gantry.trace import sites_not_read  # noqa: E402

PAGE = hp.fixture_source("page")


def edit_document(folder, fn):
    """Replace the write_site_tree document beside folder/devagent.toml with fn(doc), unchecked:
    a test of a document that disagrees with the tree on purpose."""
    path = Path(folder) / "portfolio.json"
    path.write_bytes(sp.dumps(fn(json.loads(path.read_bytes()))))


def without(doc, name):
    doc["projects"] = [p for p in doc["projects"] if p["project"] != name]
    doc["parse_report"] = [e for e in doc["parse_report"] if e["project"] != name]
    return doc


def test_config_only_and_document_only_projects_are_out_of_date(tmp_path):
    folder = tmp_path / "tree"
    config = load_config(write_site_tree(folder, {"alpha": PAGE, "beta": PAGE}))
    edit_document(folder, lambda doc: hp.append_project(without(doc, "beta"), "gamma", PAGE))
    result = build_all(config)
    summaries = {s.name: s for s in result.summaries}
    assert summaries["alpha"].status == "built"
    assert (summaries["beta"].status, summaries["beta"].reason) == ("not_built", CONFIG_ONLY)
    assert CONFIG_ONLY.startswith(OUT_OF_DATE)
    assert "gamma" not in summaries and list(result.pages) == ["alpha"]
    assert [w.message for w in result.warnings if w.key == "portfolio_json"] == [DOCUMENT_ONLY.format(
        config=config.path, name="gamma", devagent_config=config.devagent_config)]
    # The out-of-date site is not read: the trace names it, and the CLI prints both lines.
    assert sites_not_read(result.sites, {"beta": CONFIG_ONLY}) == ("beta",)
    code, _, err = run_main("--config", str(config.path))
    assert code == 0
    assert f"gantry: warning: site beta is not built: {CONFIG_ONLY}" in err.splitlines()
    assert f"gantry: warning: {DOCUMENT_ONLY.format(config=config.path, name='gamma', devagent_config=config.devagent_config)}" in err.splitlines()


def test_status_disagreement_lists_both_statuses(tmp_path):
    folder = tmp_path / "tree"
    config = load_config(write_site_tree(folder, {"alpha": PAGE, "beta": None, "gamma": PAGE}))

    # devagent read the tree otherwise: no WBS.md for alpha, one for beta.
    disagree = lambda doc: hp.site_document({"alpha": hp.NotRead("no_wbs", hp.NO_WBS_REASON), "beta": PAGE,
                                             "gamma": PAGE})  # noqa: E731
    edit_document(folder, disagree)
    summaries = {s.name: s for s in build_all(config).summaries}
    assert summaries["gamma"].status == "built"
    assert summaries["alpha"].reason == DISAGREE.format(doc="no WBS.md", own="a WBS.md")
    assert summaries["beta"].reason == DISAGREE.format(doc="WBS.md read", own="no WBS.md")
    # Most often the document is only stale (a WBS.md made or deleted since the aggregator ran): the
    # text says out of date, and the first remedy is a re-run (final review, item 1).
    assert summaries["alpha"].reason == ("portfolio JSON out of date or disagreeing with Gantry: the document says "
                                         "no WBS.md; Gantry finds a WBS.md; re-run devagent's aggregator, and if "
                                         "they still disagree, use an absolute devdoc_dir and the same devagent "
                                         "config for both")
    assert summaries["alpha"].reason.startswith(OUT_OF_DATE.split(" with ")[0])
    assert (summaries["alpha"].status, summaries["beta"].status) == ("not_built", "not_built")


def test_not_compared_status_keeps_gantrys_reason(tmp_path):
    """A problem own_status does not class (a devdoc_dir that is not a string) keeps Gantry's own reason,
    also for a project the document does not hold."""
    folder = tmp_path / "tree"
    config = load_config(write_site_tree(folder, {"alpha": PAGE}))
    append_bytes(folder / "devagent.toml", "\n[project.delta]\ndevdoc_dir = 3\n")
    site = next(s for s in build.site_list(config)[0] if s.name == "delta")
    assert own_status(site) is None
    summaries = {s.name: s for s in build_all(config).summaries}
    assert (summaries["delta"].status, summaries["delta"].reason) == ("not_built", "devdoc_dir is not a string")
    assert summaries["alpha"].status == "built"


def test_agreeing_not_read_site_keeps_its_problem(tmp_path):
    """bakery agrees (no WBS.md on both sides): its SiteEntry keeps Gantry's problem, which the fires
    and the trace read, and its not-built reason is the document's words."""
    from gantry.fires import site_views
    from gantry.sites import NO_WBS as NO_WBS_PROBLEM

    result = sample_build(tmp_path)
    entry = next(s for s in result.sites if s.name == NO_WBS)
    assert (entry.problem, entry.document_reason) == (NO_WBS_PROBLEM, NO_WBS_REASON)
    assert next(s for s in result.summaries if s.name == NO_WBS).reason == NO_WBS_REASON
    view = site_views(result)[NO_WBS]
    assert (view.wbs, view.reason) == ("absent", NO_WBS_PROBLEM)
    assert NO_WBS not in sites_not_read(result.sites, {NO_WBS: NO_WBS_REASON, UNREADABLE: UNREADABLE_REASON})


def test_sample_statuses_agree_with_site_resolution():
    """On the samples, devagent's statuses match Gantry's own site resolution: no site is out of date."""
    from gantry.sites import config_roots, load_sites

    config = load_config(ROOT / "samples" / "gantry.sample.toml")
    roots = config_roots(config)
    sites = load_sites(config, roots)[0]
    document = json.loads(sp.SAMPLES_WRITTEN.read_bytes())
    statuses = {p["project"]: p["status"] for p in document["projects"]}
    assert list(statuses) == [s.name for s in sites]
    for site in sites:
        assert statuses[site.name] in AGREES[own_status(site)], site.name
    matched, projects = matched_sites(config, roots, sites)
    assert [s.problem for s in matched] == [s.problem for s in sites]
    assert set(projects) == set(statuses)


def test_added_project_appears(tmp_path):
    """D1's new form: a project added to devagent's config is out of date until the aggregator
    re-runs, then built, with no edit to the Gantry config."""
    devagent = hd.require_devagent()
    ferry = "ferry"  # a name the samples do not have

    copy = copy_samples(tmp_path)
    gantry_config = copy / "gantry.sample.toml"
    before = gantry_config.read_bytes()
    append_bytes(copy / "devagent" / "config.toml", f'\n[project.{ferry}]\ndevdoc_dir = "../{SMALL}"\n')
    config = load_config(gantry_config)
    summaries = {s.name: s for s in build_all(config).summaries}
    assert (summaries[ferry].status, summaries[ferry].reason) == ("not_built", CONFIG_ONLY)
    home = copy / "devagent" / "out"
    (home / "config.toml").write_bytes((copy / "devagent" / "config.toml").read_bytes())
    (home / "home").mkdir(exist_ok=True)
    sp.run_aggregator(devagent, copy / "devagent", home)
    summaries = {s.name: s for s in build_all(load_config(gantry_config)).summaries}
    assert summaries[ferry].status == "built"
    assert gantry_config.read_bytes() == before


def test_build_opens_no_working_tree_wbs(tmp_path, monkeypatch):
    """Every open of a file named WBS.md fails, and the sample build still succeeds (#12, AC 10)."""
    copy = copy_samples(tmp_path)
    wbs = copy / SMALL / "WBS.md"
    real_os_open, real_open = os.open, builtins.open

    def refuse(path):
        if os.path.basename(os.fspath(path)) == "WBS.md":
            raise PermissionError(13, "a WBS.md open in the build", os.fspath(path))

    def os_open(path, *args, **kwargs):
        refuse(path)
        return real_os_open(path, *args, **kwargs)

    def open_(file, *args, **kwargs):
        if not isinstance(file, int):
            refuse(file)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(os, "open", os_open)
    monkeypatch.setattr(builtins, "open", open_)
    monkeypatch.setattr(io, "open", open_)
    # The control: an open of a WBS.md fails, by each route.
    for route in (lambda: os.open(wbs, os.O_RDONLY), lambda: open(wbs, "rb"), lambda: wbs.read_bytes()):
        with pytest.raises(PermissionError):
            route()
    code, out, stdout, stderr = demo_build(tmp_path, monkeypatch, copy=copy)
    assert code == 0, stderr
    assert {p.stem for p in (out / "sites").glob("*.html")} == READABLE


def test_check_schedulers_reads_the_document(tmp_path, monkeypatch):
    """tools/check_schedulers.py builds each site from the portfolio JSON, matched as the build matches it."""
    import check_schedulers
    from gantry.schedule import plan

    copy = copy_samples(tmp_path)
    seen = []
    real = check_schedulers.read_document
    monkeypatch.setattr(check_schedulers, "read_document",
                        lambda config, sites, roots: seen.append(config.portfolio_json) or real(config, sites, roots))
    monkeypatch.setattr(check_schedulers, "run_function",
                        lambda page, name, args: [[key, list(v)] for key, v in plan(*args).items()])
    out = tmp_path / "pages"
    assert check_schedulers.main(["--config", str(copy / "gantry.sample.toml"), "--out", str(out)]) == 0
    assert seen == [load_config(copy / "gantry.sample.toml").portfolio_json]
    assert {p.stem for p in (out / "sites").glob("*.html")} == READABLE
