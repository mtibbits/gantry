"""The full build: anchors and hrefs, reading and rendering every site, and
writing the pages and the index (#11)."""

import os
import re
from datetime import date

import pytest

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
)
from page_safety import HOSTILE, assert_no_external_resources, assert_page_safe
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


# --- writing: the full build on the command line --------------------------------------


def written_files(out):
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())


def test_demo_build_writes_index_and_pages(tmp_path, monkeypatch):
    code, out, stdout, stderr = demo_build(tmp_path, monkeypatch)
    assert code == 0, stderr
    assert written_files(out) == sorted(["index.html", *(f"sites/{s}.html" for s in READABLE)])
    printed = stdout.splitlines()
    projects = [p for p in sample_projects() if p in READABLE]
    assert printed == [str(out / "sites" / f"{s}.html") for s in projects] + [str(out / "index.html")]
    assert all(os.path.isabs(line) for line in printed)


def test_index_links_every_written_page(tmp_path, monkeypatch):
    _, out, _, _ = demo_build(tmp_path, monkeypatch)
    links = set(page_links((out / "index.html").read_bytes().decode("utf-8")))
    assert links == {f"sites/{p.name}" for p in (out / "sites").iterdir()}
    assert len(links) == 4


def test_not_built_sites_listed_exit_0(tmp_path, monkeypatch):
    code, out, _, stderr = demo_build(tmp_path, monkeypatch)
    assert code == 0
    index = (out / "index.html").read_bytes().decode("utf-8")
    for name, reason in ((NO_WBS, "no WBS.md in the planning directory"),
                         (UNREADABLE, "WBS.md is not valid UTF-8 at byte 88")):
        assert f"not built: {reason}" in index
        assert f"gantry: warning: site {name} is not built: {reason}" in stderr.splitlines()
    assert len(list((out / "sites").iterdir())) == 4


def test_all_fail_writes_index_exit_1(tmp_path):
    config = write_site_tree(tmp_path, {"alpha": None, "beta": None})
    code, stdout, stderr = run_main("--config", str(config))
    assert code == 1
    assert (tmp_path / "out" / "index.html").is_file()
    assert not (tmp_path / "out" / "sites").exists() or not list((tmp_path / "out" / "sites").iterdir())
    assert stdout.splitlines() == [str(tmp_path / "out" / "index.html")]
    assert stderr.splitlines()[-1] == "gantry: error: no site page was written"


def test_case_clash_cli(tmp_path):
    page = (FIXTURES / "page.md").read_bytes()
    config = write_site_tree(tmp_path, {"alpha": page, "Alpha": page})
    code, stdout, stderr = run_main("--config", str(config))
    assert code == 0, stderr
    assert [p.name for p in (tmp_path / "out" / "sites").iterdir()] == ["alpha.html"]
    clash = CLASH.format(earlier="alpha")
    assert f"gantry: warning: site Alpha is not built: {clash}" in stderr.splitlines()
    assert f"not built: {clash}" in (tmp_path / "out" / "index.html").read_bytes().decode("utf-8")


def test_site_on_case_clash_exits_1(tmp_path):
    page = (FIXTURES / "page.md").read_bytes()
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
    config = write_site_tree(tmp_path, {"alpha": (FIXTURES / "page.md").read_bytes()})
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
    config = write_site_tree(tmp_path, {"alpha": (FIXTURES / "page.md").read_bytes()})
    (tmp_path / "out").write_bytes(b"a file, not a folder")
    code, stdout, stderr = run_main("--config", str(config))
    assert (code, stdout) == (1, "")
    lines = stderr.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith(f"gantry: error: cannot write {tmp_path / 'out' / 'index.html'}: ")
    assert (tmp_path / "out").read_bytes() == b"a file, not a folder"


def test_page_write_error_lists_site(tmp_path):
    page = (FIXTURES / "page.md").read_bytes()
    config = write_site_tree(tmp_path, {"alpha": page, "beta": page})
    (tmp_path / "out" / "sites" / "beta.html").mkdir(parents=True)
    code, stdout, stderr = run_main("--config", str(config))
    assert code == 0
    assert stdout.splitlines() == [str(tmp_path / "out" / "sites" / "alpha.html"), str(tmp_path / "out" / "index.html")]
    (line,) = [line for line in stderr.splitlines() if "beta" in line]
    assert line.startswith("gantry: warning: site beta is not built: cannot write sites/beta.html: ")
    index = (tmp_path / "out" / "index.html").read_bytes().decode("utf-8")
    assert "not built: cannot write sites/beta.html: " in index
    assert str(tmp_path) not in index


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes only")
def test_index_mode_follows_umask(tmp_path):
    config = write_site_tree(tmp_path, {"alpha": (FIXTURES / "page.md").read_bytes()})
    old = os.umask(0o022)
    try:
        assert run_main("--config", str(config))[0] == 0
    finally:
        os.umask(old)
    assert (tmp_path / "out" / "index.html").stat().st_mode & 0o777 == 0o644


def test_added_project_appears(tmp_path, monkeypatch):
    copy = copy_samples(tmp_path)
    before = (copy / "gantry.sample.toml").read_bytes()
    (copy / "dunes").mkdir()
    (copy / "dunes" / "WBS.md").write_bytes(b"# Dunes\n\n- [ ] Plant the grass {issue: Issue-1}\n")
    append_bytes(copy / "devagent" / "config.toml", '\n[project.dunes]\ndevdoc_dir = "../dunes"\n')
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
    # The subject set cannot quietly empty: the index links 4 pages, and a
    # worksite page links nothing until #68.
    assert counts.pop("index.html") == 4
    assert len(counts) == 4 and set(counts.values()) == {0}


def test_encoded_key_anchor_and_href_land(tmp_path):
    hostile = "a b<c>&\u00e9"
    wbs = f"- [x] Done {{issue: {hostile}}}\n- [ ] Open {{issue: x y}}\n".encode("utf-8")
    other = (FIXTURES / "page.md").read_bytes()
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
    assert len(pages) == 5
    for rel, text in pages.items():
        assert_no_external_resources(text)
        assert_page_safe(text, [])


# --- the docs ---------------------------------------------------------------------------

STDERR_FORMS = (
    "gantry: warning: site <name> is not built: <reason>",
    "gantry: error: cannot write <index path>: <reason>",
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
    assert (code, stdout) == (1, "")
    lines = stderr.splitlines()
    assert f"gantry: warning: site {NO_WBS} is not built: no WBS.md in the planning directory" in lines
    assert f"gantry: warning: site {UNREADABLE} is not built: WBS.md is not valid UTF-8 at byte 88" in lines
    assert lines[-1].startswith(f"gantry: error: cannot write {out / 'index.html'}: ")
    assert sum("gantry: error:" in line for line in lines) == 1
    assert out.read_bytes() == b"a file, not a folder"


def test_unwritable_sites_folder_still_writes_index(tmp_path, monkeypatch):
    copy = copy_samples(tmp_path)
    (copy / "out").mkdir()
    (copy / "out" / "sites").write_bytes(b"a file, not a folder")
    code, out, stdout, stderr = demo_build(tmp_path, monkeypatch, copy=copy)
    assert (code, stdout) == (1, f"{out / 'index.html'}\n")
    lines = stderr.splitlines()
    for name in READABLE:
        assert any(line.startswith(f"gantry: warning: site {name} is not built: cannot write sites/{name}.html: ")
                   for line in lines)
    assert lines[-1] == "gantry: error: no site page was written"
    assert "index.html" not in stderr
    assert (out / "sites").read_bytes() == b"a file, not a folder"


def test_parse_error_isolated(tmp_path, monkeypatch):
    real = build.read_wbs

    def read_wbs(path, name):
        if name == "beta":
            raise RuntimeError("parser broke")
        return real(path, name)

    monkeypatch.setattr(build, "read_wbs", read_wbs)
    result = build_all(page_tree(tmp_path, ["alpha", "beta"]))
    summaries = {s.name: s for s in result.summaries}
    assert summaries["alpha"].status == "built"
    assert (summaries["beta"].status, summaries["beta"].reason) == ("not_built", "render error: RuntimeError: parser broke")


def test_site_mode_parse_error_is_one_line(tmp_path, monkeypatch):
    def read_wbs(path, name):
        raise RuntimeError("parser broke")

    monkeypatch.setattr(build, "read_wbs", read_wbs)
    config = write_site_tree(tmp_path, {"alpha": (FIXTURES / "page.md").read_bytes()})
    code, stdout, stderr = run_main("--config", str(config), "--site", "alpha")
    assert (code, stdout) == (1, "")
    assert stderr.endswith("gantry: error: --site alpha: cannot build: render error: RuntimeError: parser broke\n")
    assert not (tmp_path / "out" / "sites" / "alpha.html").exists()


@pytest.mark.parametrize("est, day, when", [
    ("8h", 2, date(2026, 1, 21)),   # 1.33 days: the work runs into the second working day
    ("2h", 1, date(2026, 1, 20)),   # under half a day: day 1, the as-of date, never day 0
    ("12h", 2, date(2026, 1, 21)),  # exactly two days
])
def test_finish_day_is_the_day_the_plan_ends_in(tmp_path, est, day, when):
    wbs = f"- [ ] A {{issue: Issue-1, est: {est}}}\n".encode("utf-8")
    config = load_config(write_site_tree(tmp_path, {"alpha": wbs}, as_of="2026-01-20"))
    (summary,) = build_all(config).summaries
    assert (summary.finish_day, summary.finish_date) == (day, when)
