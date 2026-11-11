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
from gantry.sites import config_roots, load_sites
from gantry.trace import (COUNT_FINISHED, COUNT_HELD, COUNT_OPEN, build_site_trace, counts_line, load_catalog,
                          page_trace)
from gantry.views import worksite
from gantry.views.worksite import BH, BW, ROWH, page_model
from helpers_config import copy_samples, demo_build, page_ids, sample_build, write_site_tree
from page_safety import assert_no_external_resources, assert_page_safe
from page_script.harness import Planted, PageScriptError, open_page, run_function
from sample_roles import BUSY, READABLE, SMALL
from test_cli import run_module
from test_samples import GANTRY_SAMPLE, ROOT
from test_worksite_page import find, text_of, tree

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

