"""The trace on the worksite page (#20): the page's `trace` data, the build's
trace line, the chips and panels, and the selection.

The model tests need no Node; the page-script tests take the `node` fixture.
Sample literals are hand-derived from samples/lighthouse/.
"""

import json
import os
import re

import pytest

from commit_check import path_form_hits
from gantry import build
from gantry.build import build_all
from gantry.cli import main
from gantry.config import load_config
from gantry.model import FINISHED_GLYPHS, STATE_GLYPHS, Estimate, Leaf, SiteData
from gantry.render import CLASS_TOKENS
from gantry.sites import config_roots, load_sites
from gantry.trace import (COUNT_FINISHED, COUNT_HELD, COUNT_OPEN, MESSAGES, build_site_trace, counts_line,
                          load_catalog, page_trace)
from gantry.views import worksite
from gantry.views.worksite import BH, BW, ROWH, page_model
from helpers_config import copy_samples, demo_build, page_ids, sample_build, write_site_tree
from page_safety import HOSTILE, _strings, assert_no_external_resources, assert_page_safe
from page_script.harness import Planted, PageScriptError, open_page, run_function
from sample_roles import BUSY, READABLE, SMALL
from test_cli import run_module
from test_samples import GANTRY_SAMPLE, ROOT
from test_worksite_page import STRUCTURAL, find, text_of, tree, visible_text

# One in-trace site whose FR-1 is served only by Issue-1, held out by its
# cycle with Issue-2.
CYCLE_REQS = "# R\n\n## Functional requirements\n\n- FR-1: Served only by a held-out leaf.\n"
CYCLE = {
    "alpha": ("# Alpha\n\n"
              "- [ ] One {issue: Issue-1, depends_on: [Issue-2], satisfies: alpha:FR-1}\n"
              "- [ ] Two {issue: Issue-2, depends_on: [Issue-1]}\n"
              "- [ ] Three {issue: Issue-3}\n"),
}


def installation(tmp_path, wbs, requirements=None, portfolio=None):
    """An installation in tmp_path: `wbs` maps each site name to its WBS.md
    text, in config order; `requirements` maps a site name to its
    requirements text, which puts it in the trace; `portfolio` is the
    portfolio file's business-requirements text. Returns the config path."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    tables = []
    for name, text in (requirements or {}).items():
        (tmp_path / f"req-{name}.md").write_bytes(text.encode("utf-8"))
        tables.append(f'[site.{name}]\nrequirements = "req-{name}.md"\n')
    raw = {}
    if portfolio is not None:
        (tmp_path / "portfolio.md").write_bytes(("# P\n\n## Business requirements\n\n" + portfolio).encode("utf-8"))
        raw["portfolio_file"] = '"portfolio.md"'
    sites = {name: text.encode("utf-8") for name, text in wbs.items()}
    return write_site_tree(tmp_path, sites, tail="".join(tables), **raw)


def built(config_path):
    """build_all over the installation at config_path."""
    return build_all(load_config(config_path))


def trace_of(page):
    """The page's embedded `trace` data, after the static safety check."""
    return assert_page_safe(page, [])["trace"]


def sample(tmp_path):
    """build_all on a samples copy."""
    return sample_build(tmp_path)


def sample_catalog():
    config = load_config(GANTRY_SAMPLE)
    return load_catalog(config_roots(config), config, load_sites(config)[0])


# --- Task 1: the samples ----------------------------------------------------------


def test_sample_trace_shapes(tmp_path):
    result = sample(tmp_path)
    inputs = result.inputs[SMALL]
    trace = build_site_trace(SMALL, inputs.data.leaves, sample_catalog())
    report = trace.report
    assert report.unserved == ("lighthouse:UC-2", "lighthouse:FR-2")
    assert report.untraced == ("Issue-4", "Issue-5", "Issue-9")
    assert len(report.undefined) == 1 and "lighthouse:FR-90" in report.undefined[0].message
    assert [(s.leaf, s.how) for s in trace.serving["lighthouse:SR-1"]] == [
        ("Issue-3", "direct"), ("Issue-6", "indirect"), ("Issue-7", "indirect"), ("Issue-8", "direct"),
        ("Issue-10", "indirect"), ("Issue-11", "direct")]
    leaves = {leaf["key"]: leaf for leaf in page_model(SMALL, inputs.data, default_unit_hours=6,
                                                      inputs=inputs)["leaves"]}
    assert leaves["Issue-7"]["wave_full"]
    assert leaves["Issue-10"]["linked"] and not leaves["Issue-10"]["wave_full"]
    assert not leaves["Issue-11"]["linked"]


# --- Task 2: page_trace and the counts line ----------------------------------------

SAMPLE_LINE = ("trace lighthouse: unreadable 0, malformed 0, duplicate 0, misplaced 0, cycle 0, undefined 1, "
               "unserved 2, untraced 3")


def traced(config_path, site):
    """(page_trace, SiteTrace) of one site, read and joined as the build does."""
    config = load_config(config_path)
    roots = config_roots(config)
    sites = load_sites(config, roots)[0]
    catalog = load_catalog(roots, config, sites)
    inputs, reasons = build.read_sites(config, sites, roots=roots)
    assert site in inputs, reasons
    trace = build_site_trace(site, inputs[site].data.leaves, catalog)
    return page_trace(trace, catalog, inputs[site].data), trace


def row(page, rid):
    (found,) = [r for r in page["requirements"] if r["id"] == rid]
    return found


def leaf_ids_of(page, key):
    (found,) = [ids for k, ids in page["leaves"] if k == key]
    return found


def test_sample_light_sets():
    sr1 = row(traced(GANTRY_SAMPLE, SMALL)[0], "lighthouse:SR-1")
    assert sr1["label"] == "SR-1" and sr1["layer"] == "SR"
    assert sr1["text"] == "The system keeps a log of every lamp check."
    assert sr1["needs"] == [] and sr1["held"] == []
    assert sr1["light"] == [["Issue-7", "indirect"], ["Issue-8", "direct"], ["Issue-10", "indirect"],
                            ["Issue-11", "direct"]]
    assert sr1["finished"] == [["Issue-3", "Issue-3", "Rewire the lamp", "direct"],
                               ["Issue-6", "Issue-6", "Choose the logbook format", "indirect"]]
    assert sr1["counts"] == "4 open, 2 finished"


def test_sample_use_case_light_set():
    page = traced(GANTRY_SAMPLE, SMALL)[0]
    uc3 = row(page, "lighthouse:UC-3")
    assert uc3["light"] == [["Issue-7", "indirect"], ["Issue-8", "direct"], ["Issue-10", "indirect"],
                            ["Issue-11", "indirect"]]
    assert uc3["needs"] == ["lighthouse:SR-1"]
    assert leaf_ids_of(page, "Issue-10") == [["lighthouse:FR-1", "FR-1"]]


def test_sample_leaf_chips_data():
    page = traced(GANTRY_SAMPLE, SMALL)[0]
    assert leaf_ids_of(page, "Issue-7") == [["lighthouse:FR-1", "FR-1"], ["life:BR-1", "life:BR-1"],
                                            ["lighthouse:QA-1", "QA-1"], ["lighthouse:UC-1", "UC-1"]]
    # Finished and untraced leaves have no element to carry chips.
    assert [k for k, _ in page["leaves"]] == ["Issue-7", "Issue-8", "Issue-10", "Issue-11"]
    br = row(page, "life:BR-1")
    assert (br["label"], br["text"]) == ("life:BR-1", None)
    assert page["in_trace"] is True and page["scope"] == "site"
    assert list(page) == ["in_trace", "scope", "requirements", "leaves", "report"]


def test_sample_rows_by_layer():
    page = traced(GANTRY_SAMPLE, SMALL)[0]
    assert [r["id"] for r in page["requirements"]] == [
        "life:BR-1", "lighthouse:UC-1", "lighthouse:UC-2", "lighthouse:UC-3", "lighthouse:SR-1",
        "lighthouse:FR-1", "lighthouse:FR-2", "lighthouse:QA-1", "lighthouse:CON-1"]


def test_light_sets_partition_serving(tmp_path):
    compared = 0
    for config_path, site in ((GANTRY_SAMPLE, SMALL), (installation(tmp_path, CYCLE, {"alpha": CYCLE_REQS}), "alpha")):
        page, trace = traced(config_path, site)
        for r in page["requirements"]:
            got = {(k, how) for k, how in r["light"]} | {(k, how) for k, _, _, how in r["held"] + r["finished"]}
            want = {(s.leaf, s.how) for s in trace.serving.get(r["id"], ())}
            assert got == want, r["id"]
            assert len(r["light"]) + len(r["held"]) + len(r["finished"]) == len(want), r["id"]
            compared += 1
    assert compared >= 6


def test_held_out_only_server(tmp_path):
    page, _ = traced(installation(tmp_path, CYCLE, {"alpha": CYCLE_REQS}), "alpha")
    fr1 = row(page, "alpha:FR-1")
    assert fr1["light"] == []
    assert fr1["held"] == [["Issue-1", "Issue-1", "One", "direct"]]
    assert fr1["counts"] == "0 open, 1 waiting on a cycle"


COUNTS_REQS = ("# R\n\n## Functional requirements\n\n- FR-1: Nobody serves it.\n- FR-2: Served four ways.\n"
               "- FR-3: Served twice.\n")
COUNTS_WBS = ("# Alpha\n\n"
              "- [ ] A {issue: Issue-1, satisfies: alpha:FR-2}\n"
              "- [ ] B {issue: Issue-2, satisfies: alpha:FR-2}\n"
              "- [ ] C {issue: Issue-3, depends_on: [Issue-4], satisfies: alpha:FR-2}\n"
              "- [ ] D {issue: Issue-4, depends_on: [Issue-3]}\n"
              "- [x] E {issue: Issue-5, satisfies: [alpha:FR-2, alpha:FR-3]}\n"
              "- [ ] F {issue: Issue-6, satisfies: alpha:FR-3}\n")
ONE_LEAF = "# A\n\n- [ ] One {issue: Issue-1}\n"


def test_counts_text(tmp_path):
    page, _ = traced(installation(tmp_path, {"alpha": COUNTS_WBS}, {"alpha": COUNTS_REQS}), "alpha")
    assert [row(page, f"alpha:FR-{n}")["counts"] for n in (1, 2, 3)] == [
        "0 open", "2 open, 1 waiting on a cycle, 1 finished", "1 open, 1 finished"]
    assert (COUNT_OPEN, COUNT_HELD, COUNT_FINISHED) == ("{n} open", "{n} waiting on a cycle", "{n} finished")


def test_counts_line(tmp_path):
    assert counts_line(SMALL, traced(GANTRY_SAMPLE, SMALL)[0]) == SAMPLE_LINE
    wbs = {"alpha": ONE_LEAF, "beta": "# B\n\n- [ ] One {issue: Issue-1, satisfies: beta:FR-9}\n"}
    config_path = installation(tmp_path, wbs)
    assert counts_line("alpha", traced(config_path, "alpha")[0]) is None
    assert counts_line("beta", traced(config_path, "beta")[0]) == (
        "trace beta: unreadable 0, malformed 0, duplicate 0, misplaced 0, cycle 0, undefined 1, unserved 0, "
        "untraced 0")


def test_requirements_file_entry_has_no_path(tmp_path):
    reqs = "# R\n\n## Functional requirements\n\n- FR-1: One.\n- FR-1: The same ID again.\n"
    page, _ = traced(installation(tmp_path, {"alpha": ONE_LEAF}, {"alpha": reqs}), "alpha")
    text = json.dumps(page)
    (duplicate,) = dict(page["report"])["duplicate"]
    assert duplicate[0] == "the requirements file" and duplicate[1] == 6
    assert str(tmp_path) not in text and json.dumps(str(tmp_path))[1:-1] not in text
    assert path_form_hits(text) == []


# --- Task 3: the build carries the trace --------------------------------------------

REPORT_NAMES = ["unreadable", "malformed", "duplicate", "misplaced", "cycle", "undefined"]


def test_every_built_page_carries_trace(tmp_path):
    result = sample(tmp_path)
    assert set(result.pages) == READABLE
    for name in sorted(READABLE):
        trace = trace_of(result.pages[name])
        # Exactly these five keys: #77 and #21 add theirs beside them.
        assert list(trace) == ["in_trace", "scope", "requirements", "leaves", "report"], name
        if name == SMALL:
            assert trace["in_trace"] is True
            continue
        assert trace["in_trace"] is False and trace["requirements"] == [] and trace["leaves"] == [], name
        assert trace["report"] == [[n, []] for n in REPORT_NAMES], name


def test_catalog_loaded_once_per_run(tmp_path, monkeypatch):
    calls = []
    real = build.load_catalog

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(build, "load_catalog", counting)
    result = sample(tmp_path)
    assert set(result.pages) == READABLE
    assert len(calls) == 1


def test_trace_failure_stops_only_its_site(tmp_path, monkeypatch):
    real = build.build_site_trace

    def failing(site, leaves, catalog):
        if site == BUSY:
            raise RuntimeError("trace fault")
        return real(site, leaves, catalog)

    monkeypatch.setattr(build, "build_site_trace", failing)
    result = sample(tmp_path)
    assert set(result.pages) == READABLE - {BUSY}
    (busy,) = [s for s in result.summaries if s.name == BUSY]
    assert busy.status == "not_built" and busy.reason == "render error: RuntimeError: trace fault"


def test_catalog_fault_does_not_end_run(tmp_path):
    config_path = installation(tmp_path, {"alpha": ONE_LEAF}, {"alpha": "unused"})
    (tmp_path / "req-alpha.md").unlink()
    result = built(config_path)
    page = result.pages["alpha"]
    (unreadable,) = dict(trace_of(page)["report"])["unreadable"]
    assert unreadable[0] == "the requirements file" and unreadable[1] is None
    assert str(tmp_path) not in page and path_form_hits(page) == []


# --- Task 4: the stdout line -------------------------------------------------------


def test_full_build_prints_trace_line(tmp_path, monkeypatch):
    code, out, stdout, err = demo_build(tmp_path, monkeypatch)
    assert code == 0, err
    lines = stdout.splitlines()
    assert lines[-1] == SAMPLE_LINE
    assert [line for line in lines if line.startswith("trace ")] == [SAMPLE_LINE]
    assert os.path.samefile(lines[-2], out / "index.html")


def test_site_build_prints_trace_line(tmp_path, monkeypatch):
    copy = copy_samples(tmp_path)
    code, out, stdout, err = demo_build(tmp_path, monkeypatch, "--site", SMALL, copy=copy)
    assert code == 0, err
    path, line = stdout.splitlines()
    assert os.path.samefile(path, out / "sites" / f"{SMALL}.html") and line == SAMPLE_LINE
    code, out, stdout, err = demo_build(tmp_path, monkeypatch, "--site", BUSY, copy=copy)
    assert code == 0, err
    (path,) = stdout.splitlines()
    assert os.path.samefile(path, out / "sites" / f"{BUSY}.html")


def test_untraced_site_with_entry_prints_line(tmp_path, capsys):
    wbs = {"alpha": "# A\n\n- [ ] One {issue: Issue-1, satisfies: alpha:FR-9}\n", "beta": ONE_LEAF}
    config_path = installation(tmp_path, wbs)
    assert main(["build", "--config", str(config_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line for line in lines if line.startswith("trace ")] == [
        "trace alpha: unreadable 0, malformed 0, duplicate 0, misplaced 0, cycle 0, undefined 1, unserved 0, "
        "untraced 0"]
    assert lines[-1].startswith("trace alpha: ") and len(lines) == 4


def test_trace_line_bytes(tmp_path):
    copy = copy_samples(tmp_path)
    result = run_module("build", "--config", str(copy / "gantry.sample.toml"))
    assert result.returncode == 0, result.stderr
    assert b"\r" not in result.stdout
    assert result.stdout.decode("utf-8").splitlines()[-1] == SAMPLE_LINE


# --- Task 5: chips and the two panels ----------------------------------------------

GOLDEN = ROOT / "tests" / "fixtures" / "trace_page" / "untraced_main.txt"
MAIN_RE = re.compile(r"<main\b.*?</main>", re.S)
FOUR_REQS = "# R\n\n## Functional requirements\n\n- FR-1: One.\n- FR-2: Two.\n- FR-3: Three.\n- FR-4: Four.\n"


def node_of(t, key):
    (i,) = [i for i in find(t, "g", ["node"]) if t.elements[i]["attrs"].get("data-item") == key]
    return i


def chips(t, within):
    return [(text_of(t, find(t, "text", ["chip-t"], within=c)[0]), t.elements[c]["attrs"]["data-req"])
            for c in find(t, "g", ["chip"], within=within)]


def more_of(t, within):
    return [text_of(t, i) for i in find(t, "text", ["chip-more"], within=within)]


def by_id(t, ident):
    (i,) = [i for i, el in enumerate(t.elements) if el["attrs"].get("id") == ident]
    return i


def req_li(t, rid):
    (i,) = [i for i in find(t, "li", ["req"]) if t.elements[i]["attrs"].get("data-req") == rid]
    return i


def detail_lines(t, li):
    (ul,) = find(t, "ul", ["req-detail"], within=li)
    return [text_of(t, i) for i in find(t, "li", within=ul)]


def page_of(config_path, site):
    result = built(config_path)
    assert site in result.pages, result.summaries
    return result.pages[site]


def test_sample_box_chips(tmp_path):
    t = tree(sample(tmp_path).pages[SMALL])
    seven = node_of(t, "Issue-7")
    assert chips(t, seven) == [("FR-1", "lighthouse:FR-1"), ("life:BR-1", "life:BR-1"), ("QA-1", "lighthouse:QA-1")]
    assert more_of(t, seven) == ["+1"]
    eight = node_of(t, "Issue-8")
    assert chips(t, eight) == [("SR-1", "lighthouse:SR-1"), ("UC-3", "lighthouse:UC-3")]
    assert more_of(t, eight) == []


def test_chip_count_extremes(tmp_path):
    wbs = {"alpha": ("# A\n\n"
                     "- [ ] Three {issue: Issue-1, satisfies: [alpha:FR-1, alpha:FR-2, alpha:FR-3]}\n"
                     "- [ ] Four {issue: Issue-2, depends_on: [Issue-1], "
                     "satisfies: [alpha:FR-1, alpha:FR-2, alpha:FR-3, alpha:FR-4]}\n"
                     "- [ ] None {issue: Issue-3, depends_on: [Issue-2]}\n")}
    t = tree(page_of(installation(tmp_path, wbs, {"alpha": FOUR_REQS}), "alpha"))
    three, four, none = (node_of(t, f"Issue-{n}") for n in (1, 2, 3))
    assert [label for label, _ in chips(t, three)] == ["FR-1", "FR-2", "FR-3"] and more_of(t, three) == []
    assert [label for label, _ in chips(t, four)] == ["FR-1", "FR-2", "FR-3"] and more_of(t, four) == ["+1"]
    assert chips(t, none) == [] and more_of(t, none) == []


def test_sample_panel_row(tmp_path):
    t = tree(sample(tmp_path).pages[SMALL])
    panel = by_id(t, "requirements")
    assert [text_of(t, i) for i in find(t, "h3", within=panel)] == [
        worksite.LAYER_HEADINGS[k] for k in ("BR", "UC", "SR", "FR", "QA", "CON")]
    sr1 = req_li(t, "lighthouse:SR-1")
    assert [text_of(t, i) for i in find(t, "span", ["req-count"], within=sr1)] == ["4 open, 2 finished"]
    assert detail_lines(t, sr1) == ["Issue-3 · Issue-3 · Rewire the lamp · finished",
                                    "Issue-6 · Issue-6 · Choose the logbook format · finished"]
    fr2 = req_li(t, "lighthouse:FR-2")
    assert [text_of(t, i) for i in find(t, "span", ["req-count"], within=fr2)] == ["0 open"]
    assert find(t, "button", ["req-more"], within=fr2) == [] and find(t, "ul", ["req-detail"], within=fr2) == []


def test_held_out_row_rendered(tmp_path):
    t = tree(page_of(installation(tmp_path, CYCLE, {"alpha": CYCLE_REQS}), "alpha"))
    fr1 = req_li(t, "alpha:FR-1")
    assert [text_of(t, i) for i in find(t, "span", ["req-count"], within=fr1)] == ["0 open, 1 waiting on a cycle"]
    assert detail_lines(t, fr1) == ["Issue-1 · Issue-1 · One · waiting on a cycle"]


def report_items(t):
    """{category label: (count text, [item texts])} of the #trace panel, in order."""
    panel = by_id(t, "trace")
    out = {}
    for li in find(t, "li", ["trace-cat"], within=panel):
        (k,) = find(t, "span", ["k"], within=li)
        out[text_of(t, k).rsplit(":", 1)[0]] = (text_of(t, k), [text_of(t, i) for i in find(t, "li", within=li)])
    return out


def test_sample_report_panel(tmp_path):
    page = sample(tmp_path).pages[SMALL]
    t = tree(page)
    items = report_items(t)
    unserved = "Unserved requirements (served only within this site)"
    assert list(items) == [worksite.TRACE_LABELS[k] for k in ("unreadable", "malformed", "duplicate", "misplaced", "cycle",
                                                     "undefined")] + [unserved, "Untraced open leaves"]
    assert items["Undefined IDs"][0] == "Undefined IDs: 1"
    (undefined,) = items["Undefined IDs"][1]
    assert undefined.startswith("WBS.md line 13: satisfies entry lighthouse:FR-90 names no requirement")
    assert items[unserved] == (f"{unserved}: 2", ["UC-2", "FR-2"])
    assert items["Untraced open leaves"] == ("Untraced open leaves: 3", [
        'Issue-4 · Render <b>this</b> & "that" as text', "Issue-5 · Repaint the keeper's door",
        "Issue-9 · Train the relief keeper"])
    for name in ("unreadable", "malformed", "duplicate", "misplaced", "cycle"):
        assert items[worksite.TRACE_LABELS[name]] == (f"{worksite.TRACE_LABELS[name]}: 0", [])
        (li,) = [i for i in find(t, "li", ["trace-cat"]) if text_of(t, i).startswith(worksite.TRACE_LABELS[name] + ":")]
        assert text_of(t, li) == f"{worksite.TRACE_LABELS[name]}: 0 none"
    for ident in ("gantt", "panel", "detail"):
        by_id(t, ident)
    assert find(t, "svg", ["net"]) and find(t, "ul", ["finished"])
    assert_page_safe(page, [])
    assert_no_external_resources(page)


def test_demo_page_carries_trace_element(tmp_path, monkeypatch):
    code, out, _, err = demo_build(tmp_path, monkeypatch)
    assert code == 0, err
    small = page_ids((out / "sites" / f"{SMALL}.html").read_bytes().decode("utf-8"))
    busy = page_ids((out / "sites" / f"{BUSY}.html").read_bytes().decode("utf-8"))
    assert "trace" in small and "requirements" in small
    assert "trace" not in busy and "requirements" not in busy


def render_traced(config_path, site, trace=True):
    """(html, model) of one site through build.render_site, stamp "", as the golden was captured."""
    config = load_config(config_path)
    roots = config_roots(config)
    sites = load_sites(config, roots)[0]
    inputs = build.read_sites(config, sites, roots=roots)[0][site]
    if trace:
        return build.render_site(site, inputs, config, "", catalog=load_catalog(roots, config, sites))
    m = page_model(site, inputs.data, default_unit_hours=config.default_unit_hours, inputs=inputs)
    return worksite.render_model(m, stamp=""), m


def test_untraced_site_renders_as_before(tmp_path):
    wbs = (ROOT / "tests" / "fixtures" / "wbs" / "page.md").read_bytes().decode("utf-8")
    config_path = installation(tmp_path, {"alpha": wbs})
    page, _ = render_traced(config_path, "alpha")
    assert find(tree(page), "g", ["chip"]) == []
    assert "requirements" not in page_ids(page) and "trace" not in page_ids(page)
    assert MAIN_RE.search(page).group(0) + "\n" == GOLDEN.read_bytes().decode("utf-8")
    plain, _ = render_traced(config_path, "alpha", trace=False)
    with_trace, without = assert_page_safe(page, []), assert_page_safe(plain, [])
    assert with_trace.pop("trace")["in_trace"] is False and without.pop("trace") is None
    assert with_trace == without


def test_untraced_site_with_resolvable_id(tmp_path):
    wbs = {"alpha": ONE_LEAF,
           "beta": ("# B\n\n- [ ] One {issue: Issue-1, satisfies: alpha:FR-1}\n"
                    "- [ ] Two {issue: Issue-2, depends_on: [Issue-1]}\n")}
    page = page_of(installation(tmp_path, wbs, {"alpha": FOUR_REQS}), "beta")
    t = tree(page)
    assert chips(t, node_of(t, "Issue-1")) == [("alpha:FR-1", "alpha:FR-1")]
    req_li(t, "alpha:FR-1")
    assert "trace" not in page_ids(page)


def test_business_requirement_has_no_text(tmp_path):
    secret = "Grow the keeper guild across the coast"
    wbs = {"alpha": ("# A\n\n- [ ] One {issue: Issue-1, satisfies: life:BR-1}\n"
                     "- [ ] Two {issue: Issue-2, depends_on: [Issue-1]}\n")}
    page = page_of(installation(tmp_path, wbs, portfolio=f"- BR-1: {secret}.\n"), "alpha")
    t = tree(page)
    assert chips(t, node_of(t, "Issue-1")) == [("life:BR-1", "life:BR-1")]
    req_li(t, "life:BR-1")
    (br,) = trace_of(page)["requirements"]
    assert br["text"] is None
    assert secret not in page


def test_empty_report_draws_no_trace_element(tmp_path):
    wbs = {"alpha": "# A\n\n- [ ] One {issue: Issue-1, satisfies: alpha:FR-1}\n"}
    page = page_of(installation(tmp_path, wbs, {"alpha": "# R\n\n## Functional requirements\n\n- FR-1: One.\n"}),
                   "alpha")
    assert "requirements" in page_ids(page) and "trace" not in page_ids(page)


def test_unknown_scope_label_fallback():
    assert worksite.unserved_label("site") == "Unserved requirements (served only within this site)"
    assert worksite.unserved_label("installation") == "Unserved requirements (scope: installation)"
    assert worksite.UNSERVED_SCOPES == {"site": "served only within this site"}


def chip_boxes(t, node):
    """[(x, width, y, height)] of each chip box under a node, and each chip-more text's x."""
    boxes = []
    for i in find(t, "rect", ["chip-box"], within=node):
        a = t.elements[i]["attrs"]
        boxes.append((float(a["x"]), float(a["width"]), float(a["y"]), float(a["height"])))
    more = [float(t.elements[i]["attrs"]["x"]) for i in find(t, "text", ["chip-more"], within=node)]
    return boxes, more


def box_of(t, node):
    (i,) = find(t, "rect", ["box"], within=node)
    a = t.elements[i]["attrs"]
    return float(a["x"]), float(a["y"])


LONG = ("namespace-aaaaa", "namespace-bbbbb", "namespace-ccccc")


def test_chip_row_width_bounded(tmp_path):
    reqs = {name: "# R\n\n## Functional requirements\n\n- FR-1: One.\n" for name in LONG}
    ids = ", ".join(f"{name}:FR-1" for name in LONG)
    assert all(len(f"{name}:FR-1") >= 17 for name in LONG)
    wbs = {"alpha": (f"# A\n\n- [ ] Long {{issue: Issue-1, satisfies: [{ids}]}}\n"
                     "- [ ] Two {issue: Issue-2, depends_on: [Issue-1]}\n"),
           **{name: ONE_LEAF for name in LONG}}
    t = tree(page_of(installation(tmp_path, wbs, reqs), "alpha"))
    node = node_of(t, "Issue-1")
    x, y = box_of(t, node)
    boxes, more = chip_boxes(t, node)
    shown = chips(t, node)
    assert boxes and len(shown) + int(more_of(t, node)[0][1:]) == 3
    for bx, bw, by, bh in boxes:
        assert x <= bx and bx + bw <= x + BW
        assert y + BH < by and by + bh < y + ROWH
    assert all(m + worksite.CHIP_CHAR_W * 2 <= x + BW for m in more)
    assert all(label.endswith("…") and len(label) <= worksite.CHIP_CHARS for label, _ in shown)


def test_chip_row_clears_large_circle(tmp_path):
    # Issue-3 (90 h) is a circle in the row under Issue-2, in the same column.
    wbs = {"alpha": ("# A\n\n- [ ] Root {issue: Issue-1, est: 3w}\n"
                     "- [ ] Box {issue: Issue-2, est: 1d, priority: P1, depends_on: [Issue-1], "
                     "satisfies: [alpha:FR-1, alpha:FR-2]}\n"
                     "- [ ] Big {issue: Issue-3, est: 3w, depends_on: [Issue-1]}\n"
                     "- [ ] Tail {issue: Issue-4, est: 3w, depends_on: [Issue-2]}\n"
                     "- [ ] Tail2 {issue: Issue-5, est: 3w, depends_on: [Issue-4]}\n")}
    page = page_of(installation(tmp_path, wbs, {"alpha": FOUR_REQS}), "alpha")
    t = tree(page)
    leaves = {leaf["key"]: leaf for leaf in assert_page_safe(page, [])["leaves"]}
    assert leaves["Issue-3"]["hours"] > 81 and not leaves["Issue-3"]["wave_full"] and leaves["Issue-2"]["wave_full"]
    box = node_of(t, "Issue-2")
    x, y = box_of(t, box)
    (c,) = find(t, "circle", ["dot"], within=node_of(t, "Issue-3"))
    a = t.elements[c]["attrs"]
    cx, cy, r = float(a["cx"]), float(a["cy"]), float(a["r"])
    assert cx == x + BW / 2 and cy > y + BH  # the same column, the row below
    boxes, _ = chip_boxes(t, box)
    assert boxes
    for bx, bw, by, bh in boxes:
        assert by + bh < cy - r  # the chip row ends above the circle's top


def test_chip_size_pair():
    css = (ROOT / "gantry" / "templates" / "worksite.html").read_bytes().decode("utf-8")
    (rule,) = [r for r in re.findall(r"\.chip-t \{[^}]*\}", css) if "font-size" in r]
    assert f"font-size: {worksite.CHIP_FONT}px" in rule
    assert worksite.CHIP_CHAR_W == pytest.approx(0.6 * worksite.CHIP_FONT)


# --- Task 5: hostile strings in the trace (AC 7) ------------------------------------


def hostile_leaf(key, issue, line, title="Plain", glyph=" ", hours=6, satisfies=None):
    meta = {"issue": issue} if issue is not None else {}
    if satisfies is not None:
        meta["satisfies"] = satisfies
    return Leaf(key, issue, str(line), title, glyph, STATE_GLYPHS[glyph], glyph not in FINISHED_GLYPHS, None, (),
                meta, None, line, estimate=Estimate(hours, hours, "est", "time", None))


def hostile_trace_site(tmp_path):
    """(page, trace, planted, held): site alpha, in the trace, with a HOSTILE value
    in every trace field a page shows. The requirements file is on disk; the
    leaves are built in memory, as hostile_site() does."""
    h = HOSTILE
    lines = "".join(f"- FR-{i + 1}: {text}\n" for i, text in enumerate(h))
    reqs = "# R\n\n## Functional requirements\n\n" + lines + "- FR-90: Served only by a leaf keyed __proto__.\n"
    config_path = installation(tmp_path, {"alpha": ONE_LEAF}, {"alpha": reqs})
    config = load_config(config_path)
    roots = config_roots(config)
    catalog = load_catalog(roots, config, load_sites(config, roots)[0])
    kept = [r for r in catalog.requirements.values() if r.text in h]
    assert len(kept) >= 8, [r.text for r in catalog.requirements.values()]
    first = str(kept[0].id)
    leaves = (
        hostile_leaf("Issue-1", "Issue-1", 1, hours=90, satisfies=first),
        hostile_leaf("Issue-2", "Issue-2", 2, satisfies=first),
        hostile_leaf("Issue-3", "Issue-3", 3),
        hostile_leaf(h[1], h[1], 4, satisfies=first),  # an open linked leaf: a circle
        hostile_leaf(h[2], h[2], 5, satisfies=first),  # an open unlinked leaf: schedule only
        hostile_leaf("Issue-6", "Issue-6", 6, title=h[0], glyph="x", satisfies=first),  # finished
        hostile_leaf("Issue-7", "Issue-7", 7, title=h[10]),  # open and untraced: a report item
        hostile_leaf("Issue-8", "Issue-8", 8, satisfies=f"[{first}, {h[3]}]"),  # a hostile entry in a list
        hostile_leaf("Issue-9", "Issue-9", 9, satisfies=h[5]),  # a hostile scalar entry
        hostile_leaf("__proto__", "__proto__", 10, satisfies="alpha:FR-90"),
    )
    links = (("Issue-1", "Issue-2"), ("Issue-2", "Issue-3"), ("Issue-1", h[1]))
    data = SiteData(leaves, (), links, (), ())
    trace = page_trace(build_site_trace("alpha", leaves, catalog), catalog, data)
    page = worksite.render_page("alpha", data, default_unit_hours=6, trace=trace)
    planted = [r.text for r in kept] + [h[1], h[2], h[0], h[10], h[5], "__proto__"]
    return page, trace, planted, first


def test_hostile_trace_page_is_safe(tmp_path):
    page, trace, planted, first = hostile_trace_site(tmp_path)
    data = assert_page_safe(page, planted)
    assert_no_external_resources(page)
    leaves = {leaf["key"]: leaf for leaf in data["leaves"]}
    assert leaves[HOSTILE[1]]["linked"] and not leaves[HOSTILE[1]]["wave_full"]  # a circle
    assert not leaves[HOSTILE[2]]["linked"]
    # Each planted value reaches the trace data whole; the scalar satisfies:
    # entry is also whole in its leaf's meta, and its malformed entry is text.
    strings = set(_strings(trace))
    assert [p for p in planted if p not in strings] == []
    assert ["satisfies", HOSTILE[5]] in leaves["Issue-9"]["meta"]
    malformed = MESSAGES["malformed_satisfies"].format(entry=HOSTILE[5])
    assert malformed in visible_text(page)
    # The hostile requirement lights its serving leaves; FR-90 only the __proto__ leaf.
    assert [k for k, _ in row(trace, first)["light"]] == ["Issue-1", "Issue-2", HOSTILE[1], HOSTILE[2], "Issue-8"]
    assert row(trace, "alpha:FR-90")["light"] == [["__proto__", "direct"]]
    used = {c for el in tree(page).elements for c in el["classes"]}
    assert used - CLASS_TOKENS - STRUCTURAL == set()


# --- Task 6: the selection function (Node) -----------------------------------------

START = {"selected": None, "hover": None}
NO_VIEW = {"lane_of": [], "collapsed": []}
EMPTY_MARKS = {"selected": None, "selecting": False, "leaves": [], "needs": [], "lanes": [], "tracing": None}
SR1_LIGHT = [["Issue-7", "indirect"], ["Issue-8", "direct"], ["Issue-10", "indirect"], ["Issue-11", "direct"]]


@pytest.fixture
def sample_page(tmp_path):
    """(path, trace, data) of the sample in-trace page, written from the sample build."""
    page = sample(tmp_path).pages[SMALL]
    path = tmp_path / "lighthouse.html"
    path.write_bytes(page.encode("utf-8"))
    data = assert_page_safe(page, [])
    return path, data["trace"], data


def stepped(path, events, state=START):
    for event in events:
        state = run_function(path, "gantryTrace.step", [state, event])
    return state


def marks_after(path, trace, events, view=NO_VIEW):
    return run_function(path, "gantryTrace.marks", [trace, stepped(path, events), view])


def select(rid):
    return {"type": "select", "id": rid}


def test_select_marks_direct_and_indirect(node, sample_page):
    path, trace, _ = sample_page
    marks = marks_after(path, trace, [select("lighthouse:SR-1")])
    assert marks["leaves"] == SR1_LIGHT
    assert marks["selected"] == "lighthouse:SR-1" and marks["selecting"] is True


def test_reselect_and_escape_clear(node, sample_page):
    path, trace, _ = sample_page
    assert marks_after(path, trace, [select("lighthouse:SR-1"), select("lighthouse:SR-1")]) == EMPTY_MARKS
    escape = {"type": "key", "key": "Escape", "id": None}
    assert marks_after(path, trace, [select("lighthouse:SR-1"), escape]) == EMPTY_MARKS


def test_second_selection_replaces(node, sample_page):
    path, trace, _ = sample_page
    marks = marks_after(path, trace, [select("lighthouse:SR-1"), select("lighthouse:FR-1")])
    assert marks["selected"] == "lighthouse:FR-1"
    assert marks["leaves"] == [k for k in row(trace, "lighthouse:FR-1")["light"]]


def test_enter_and_space_select(node, sample_page):
    path, trace, _ = sample_page
    for key in ("Enter", " "):
        marks = marks_after(path, trace, [{"type": "key", "key": key, "id": "lighthouse:SR-1"}])
        assert marks["leaves"] == SR1_LIGHT, key
    assert marks_after(path, trace, [{"type": "key", "key": "a", "id": "lighthouse:SR-1"}]) == EMPTY_MARKS


def test_use_case_marks_needs(node, sample_page):
    path, trace, _ = sample_page
    marks = marks_after(path, trace, [select("lighthouse:UC-3")])
    assert marks["needs"] == ["lighthouse:SR-1"]
    assert marks["leaves"] == [["Issue-7", "indirect"], ["Issue-8", "direct"], ["Issue-10", "indirect"],
                               ["Issue-11", "indirect"]]


def test_hover_keeps_selection(node, sample_page):
    path, trace, _ = sample_page
    hovered = marks_after(path, trace, [select("lighthouse:SR-1"), {"type": "hover", "leaf": "Issue-9"}])
    assert hovered["tracing"] == "Issue-9" and hovered["leaves"] == SR1_LIGHT
    left = marks_after(path, trace, [select("lighthouse:SR-1"), {"type": "hover", "leaf": "Issue-9"},
                                     {"type": "leave"}])
    assert left["tracing"] is None and left["leaves"] == SR1_LIGHT


def test_collapsed_lane_count(node, sample_page):
    path, trace, data = sample_page
    lane_of = [[leaf["key"], leaf["lane"]] for leaf in data["leaves"]]
    lanes = list(range(len(data["lanes"])))
    view = {"lane_of": lane_of, "collapsed": lanes}
    assert marks_after(path, trace, [select("lighthouse:SR-1")], view)["lanes"] == [[1, 4]]
    view = {"lane_of": lane_of, "collapsed": [i for i in lanes if i != 1]}
    assert marks_after(path, trace, [select("lighthouse:SR-1")], view)["lanes"] == []


def hand_trace(light):
    return {"in_trace": True, "scope": "site", "leaves": [], "report": [],
            "requirements": [{"id": "alpha:FR-1", "label": "FR-1", "layer": "FR", "text": "t", "needs": [],
                              "light": light, "held": [], "finished": [], "counts": "1 open"}]}


def test_proto_key_lights_exactly_that_leaf(node, sample_page):
    path, _, _ = sample_page
    trace = hand_trace([["__proto__", "direct"]])
    view = {"lane_of": [["__proto__", 0], ["constructor", 1]], "collapsed": [0, 1]}
    marks = marks_after(path, trace, [select("alpha:FR-1")], view)
    assert marks["leaves"] == [["__proto__", "direct"]]
    assert marks["lanes"] == [[0, 1]]


def test_unknown_id_marks_nothing(node, sample_page):
    path, trace, _ = sample_page
    for rid in ("constructor", "lighthouse:FR-99", "__proto__"):
        assert marks_after(path, trace, [select(rid)]) == EMPTY_MARKS, rid


def test_null_trace_marks_nothing(node, sample_page):
    path, _, _ = sample_page
    assert marks_after(path, None, [select("lighthouse:SR-1")]) == EMPTY_MARKS


def test_step_unknown_type_unchanged(node, sample_page):
    path, _, _ = sample_page
    state = {"selected": "lighthouse:SR-1", "hover": "Issue-7"}
    assert stepped(path, [{"type": "bogus", "id": "lighthouse:FR-1"}], state) == state


def test_step_key_without_id_unchanged(node, sample_page):
    path, _, _ = sample_page
    state = {"selected": "lighthouse:SR-1", "hover": None}
    assert stepped(path, [{"type": "key", "key": "Enter", "id": None}], state) == state


def test_step_bad_selected_unchanged(node, sample_page):
    path, _, _ = sample_page
    state = {"selected": 5, "hover": None}
    assert stepped(path, [select("lighthouse:SR-1")], state) == state


def test_empty_light_set_not_selecting(node, sample_page):
    path, trace, _ = sample_page
    marks = marks_after(path, trace, [select("lighthouse:FR-2")])
    assert marks == {**EMPTY_MARKS, "selected": "lighthouse:FR-2"}


# --- Task 8: the thin layer, under the harness ---------------------------------------

MARK_SELECTOR = ".req-direct, .req-indirect, .req-on, .req-need"


def marked(page, selector, cls):
    return {e.attrs["data-item"] for e in page.query(selector) if cls in e.classes}


def assert_sr1_marks(page):
    assert marked(page, "rect.seg", "req-direct") == {"Issue-8", "Issue-11"}
    assert marked(page, "g.node", "req-direct") == {"Issue-8"}
    assert marked(page, "rect.seg", "req-indirect") == {"Issue-7", "Issue-10"}
    assert marked(page, "g.node", "req-indirect") == {"Issue-7", "Issue-10"}


def pressed(page, rid):
    (button,) = [b for b in page.query('button[data-control="requirement"]') if b.attrs["data-value"] == rid]
    return button.attrs["aria-pressed"]


def lit_counts(page):
    """{lane index: its g-lit text} of the schedule's collapsed epic rows that show one."""
    return {int(e.attrs["data-lane"]): e.text for e in page.query(".g-toggle .g-lit")}


def test_selection_survives_hover_and_leave(node, sample_page):
    path, _, _ = sample_page
    with open_page(path) as page:
        page.drive("requirement", "lighthouse:SR-1")
        assert_sr1_marks(page)
        page.drive("network-detail", "Issue-9")
        (net,) = page.query("svg.net")
        assert "tracing" in net.classes
        page.leave("network-detail", "Issue-9")
        assert_sr1_marks(page)
        (net,) = page.query("svg.net")
        assert "tracing" not in net.classes and "selecting" in net.classes
        assert pressed(page, "lighthouse:SR-1") == "true"


def test_collapsed_lane_shows_lit_count(node, sample_page):
    path, _, _ = sample_page
    with open_page(path) as page:
        page.drive("requirement", "lighthouse:SR-1")
        assert lit_counts(page) == {1: "4 lit"}
        page.drive("expand", "all")
        assert page.query(".g-lit") == []
        page.drive("expand", "none")
        assert lit_counts(page) == {1: "4 lit"}


def test_marks_survive_schedule_redraw(node, sample_page):
    path, _, _ = sample_page
    with open_page(path) as page:
        page.drive("requirement", "lighthouse:SR-1")
        lit = marked(page, "rect.seg", "req-direct") | marked(page, "rect.seg", "req-indirect")
        assert lit == {"Issue-7", "Issue-8", "Issue-10", "Issue-11"}
        page.drive("lanes", "2")
        page.drive("group", "lane")
        assert marked(page, "rect.seg", "req-direct") | marked(page, "rect.seg", "req-indirect") == lit
        page.drive("requirement", "lighthouse:SR-1")
        assert page.query(MARK_SELECTOR) == []
        assert pressed(page, "lighthouse:SR-1") == "false"


def test_use_case_marks_rows_and_chips(node, sample_page):
    path, _, _ = sample_page
    with open_page(path) as page:
        page.drive("requirement", "lighthouse:UC-3")
        needed = [(e.tag, e.attrs.get("data-req")) for e in page.query(".req-need")]
        assert needed == [("g", "lighthouse:SR-1"), ("li", "lighthouse:SR-1")]


def test_detail_lists_satisfies(node, sample_page):
    path, _, _ = sample_page
    with open_page(path) as page:
        page.drive("network-detail", "Issue-10")
        (detail,) = page.query("#detail")
        assert "Satisfies: FR-1" in detail.text
        page.drive("schedule-detail", "Issue-11")
        (gdetail,) = page.query("#gdetail")
        assert "Satisfies: SR-1" in gdetail.text
        page.drive("schedule-detail", "Issue-4")
        (gdetail,) = page.query("#gdetail")
        assert "Satisfies:" not in gdetail.text and "Issue-4" in gdetail.text


def test_row_detail_toggles(node, sample_page):
    path, _, _ = sample_page

    def state(page):
        (li,) = [e for e in page.query("li.req") if e.attrs["data-req"] == "lighthouse:SR-1"]
        (more,) = [b for b in page.query("button.req-more") if b.attrs["data-value"] == "lighthouse:SR-1"]
        # A test's own selector, from a constant: the page never builds one.
        (mine,) = page.query('li.req[data-req="lighthouse:SR-1"] ul.req-detail')
        return "gone" in mine.classes, more.attrs["aria-expanded"]

    with open_page(path) as page:
        assert state(page) == (True, "false")
        page.drive("requirement-detail", "lighthouse:SR-1")
        assert state(page) == (False, "true")
        page.drive("requirement-detail", "lighthouse:SR-1")
        assert state(page) == (True, "false")
    # jsdom does no layout, so the CSS text: no rule gives .req-detail a display.
    css = re.sub(r"/\*.*?\*/", "", (ROOT / "gantry" / "templates" / "worksite.html").read_bytes().decode("utf-8"),
                 flags=re.S)
    rules = re.findall(r"[^{}]*\.req-detail[^{}]*\{[^}]*\}", css)
    assert rules
    for rule in rules:
        assert "display" not in rule, rule


def test_chip_click_selects(node, sample_page):
    path, _, _ = sample_page
    with open_page(path) as page:
        page.chip("Issue-8", "lighthouse:SR-1")
        assert pressed(page, "lighthouse:SR-1") == "true"
        assert_sr1_marks(page)
        chip = [e for e in page.query("g.chip") if e.attrs["data-req"] == "lighthouse:SR-1"]
        assert [e.attrs["aria-pressed"] for e in chip] == ["true"]


def test_chip_enter_space_select(node, sample_page):
    path, _, _ = sample_page
    with open_page(path) as page:
        page.chip("Issue-8", "lighthouse:SR-1", "focus")
        (net,) = page.query("svg.net")
        assert "tracing" not in net.classes  # a chip is not its leaf
        page.key("Enter")
        assert pressed(page, "lighthouse:SR-1") == "true"
        assert_sr1_marks(page)
        page.key(" ")
        assert pressed(page, "lighthouse:SR-1") == "false"
        page.key(" ")
        assert pressed(page, "lighthouse:SR-1") == "true"


def test_escape_clears_through_listener(node, sample_page):
    path, _, _ = sample_page
    with open_page(path) as page:
        page.drive("requirement", "lighthouse:UC-3")
        assert page.query(MARK_SELECTOR) != []
        page.key("Escape")
        assert page.query(MARK_SELECTOR) == []
        assert pressed(page, "lighthouse:UC-3") == "false"


def test_hostile_trace_under_harness(node, tmp_path):
    page_text, trace, planted, first = hostile_trace_site(tmp_path)
    path = tmp_path / "hostile.html"
    path.write_bytes(page_text.encode("utf-8"))
    with open_page(path) as page:
        assert page.planted_nodes(planted) == Planted([], [], [])
        for control, value in (("network-detail", HOSTILE[1]), ("schedule-detail", HOSTILE[2]),
                               ("requirement-detail", first), ("requirement", first)):
            page.drive(control, value)
            assert page.planted_nodes(planted) == Planted([], [], []), (control, value)
        lit = marked(page, "rect.seg", "req-direct") | marked(page, "rect.seg", "req-indirect")
        assert lit == {"Issue-1", "Issue-2", HOSTILE[1], HOSTILE[2], "Issue-8"}
        used = {c for e in page.query("[class]") for c in e.classes}
        assert used - CLASS_TOKENS - STRUCTURAL == set()
        page.drive("requirement", "alpha:FR-90")
        assert marked(page, "rect.seg", "req-direct") == {"__proto__"}
        assert marked(page, "rect.seg", "req-indirect") == set()
        assert page.planted_nodes(planted) == Planted([], [], [])


def test_requirements_panel_states_the_marks(tmp_path):
    t = tree(sample(tmp_path).pages[SMALL])
    panel = by_id(t, "requirements")
    assert [text_of(t, i) for i in find(t, "p", ["label"], within=panel)] == [worksite.SELECT_NOTE]
