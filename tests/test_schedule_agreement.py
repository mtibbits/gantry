"""The Python scheduler and its JavaScript twin agree (#9).

The JavaScript side runs from a built page, through #53's run_function, so the
copy under test is the one a page carries. Each Node test takes the `node`
fixture: it skips without Node and jsdom, and fails instead under CI.
"""

import importlib.util
import math
import random
import subprocess
import sys

import pytest

from gantry.config import load_config
from gantry.roots import directory_roots
from gantry.schedule import chain_hours, limit, plan
from gantry.views.worksite import render_page
from gantry.wbs_stopgap import parse_wbs, read_wbs
from helpers_config import write_site_tree
from helpers_gate import gate_run
from page_safety import assert_page_safe
from page_script.gate import CI_SUFFIX
from page_script.harness import NODE_MISSING, PageScriptError, run_function
from sample_roles import READABLE
from test_samples import ROOT, SAMPLES
from test_schedule import REFUSALS

LANES = range(1, 9)
TOOL = ROOT / "tools" / "check_schedulers.py"


def it(key, hours, preds=(), eff=3, ix=0):
    return {"key": key, "hours": hours, "preds": list(preds), "eff_priority": eff, "wbs_index": ix}


UNEQUAL = [it("a", 8, eff=2, ix=0), it("b", 5, ["a"], ix=1), it("c", 3, eff=1, ix=2), it("d", 2, ["c"], ix=3),
           it("e", 1, ["b", "d"], ix=4), it("f", 5, eff=4, ix=5), it("g", 3, ["f"], ix=6)]
FRACTIONAL = [it("a", 1 / 3, ix=0), it("b", 0.1, ["a"], ix=1), it("c", 7.3, eff=2, ix=2),
              it("d", 2.25, ["b", "c"], ix=3), it("e", 0.1, ix=4), it("f", 1 / 3, ["e"], ix=5)]
# Equal priority and WBS index everywhere: input order breaks every tie.
EQUAL = [it(k, 6, preds, eff=2, ix=0) for k, preds in
         (("a", ()), ("b", ()), ("c", ("a",)), ("d", ()), ("e", ("b", "c")), ("f", ()))]
NAMED = {"unequal": UNEQUAL, "fractional": FRACTIONAL, "equal": EQUAL}
HOURS = (1 / 3, 0.1, 0.7, 2.25, 6, 7.3)


def random_dags(count=32, seed=9):
    """(items, lanes) for `count` seeded DAGs; DAG i runs on 1 + i % 8 lanes."""
    rng = random.Random(seed)
    out = []
    for i in range(count):
        n = rng.randint(3, 14)
        items = [it(f"k{j}", rng.choice(HOURS), [f"k{p}" for p in range(j) if rng.random() < 0.25],
                    rng.randint(1, 4), rng.randint(0, n)) for j in range(n)]
        out.append((items, 1 + i % 8))
    return out


def constructed():
    """Every constructed (name, items, lanes) case."""
    cases = [(name, items, lanes) for name, items in NAMED.items() for lanes in LANES]
    return cases + [(f"random {i}", items, lanes) for i, (items, lanes) in enumerate(random_dags())]


def js_plan(path, items, lanes):
    return {key: tuple(v) for key, v in run_function(path, "gantrySchedule.plan", [items, lanes])}


def write_page(tmp_path, text, name):
    path = tmp_path / f"{name}.html"
    path.write_bytes(text.encode("utf-8"))
    return path


@pytest.fixture
def page_path(tmp_path):
    text = (ROOT / "tests" / "fixtures" / "wbs" / "page.md").read_bytes().decode("utf-8")
    return write_page(tmp_path, render_page("alpha", parse_wbs(text, "alpha"), default_unit_hours=6), "page")


def test_agreement_on_sample_sites(node, tmp_path):
    for site in sorted(READABLE):
        for unit in ("6", "2.7"):
            folder = tmp_path / f"{site}-{unit}"
            config = load_config(write_site_tree(folder, {site: (SAMPLES / site / "WBS.md").read_bytes()},
                                                 default_unit_hours=unit))
            data = read_wbs(directory_roots([folder]), folder / "0" / "WBS.md", site)
            path = write_page(folder, render_page(site, data, default_unit_hours=config.default_unit_hours), site)
            items = assert_page_safe(path.read_bytes().decode("utf-8"), [])["schedule"]["items"]
            assert items, site
            # The unit round-trips TOML, Python, JSON and JavaScript unchanged.
            assert {i["hours"] for i in items} == {float(unit)}
            for lanes in LANES:
                assert js_plan(path, items, lanes) == plan(items, lanes), (site, unit, lanes)


def test_agreement_on_constructed_sets(node, page_path):
    for name, items, lanes in constructed():
        assert js_plan(page_path, items, lanes) == plan(items, lanes), (name, lanes)


def test_limit_and_chain_agree(node, page_path):
    for name, items in NAMED.items():
        assert run_function(page_path, "gantrySchedule.chainHours", [items]) == chain_hours(items), name
        for lanes in LANES:
            assert run_function(page_path, "gantrySchedule.limit", [items, lanes]) == limit(items, lanes), (name, lanes)


# JSON carries no inf or nan, and a whole float lane count reaches JavaScript
# as an integer, so those three refusals stay Python-only.
JSON_REFUSALS = [case for case in REFUSALS
                 if not isinstance(case[1], float)
                 and all(not isinstance(i["hours"], float) or math.isfinite(i["hours"]) for i in case[0])]


def test_refusals_agree(node, page_path):
    assert len(JSON_REFUSALS) == len(REFUSALS) - 3
    for items, lanes, message in JSON_REFUSALS:
        with pytest.raises(ValueError, match="^plan: "):
            plan(items, lanes)
        with pytest.raises(PageScriptError, match="plan: "):
            run_function(page_path, "gantrySchedule.plan", [items, lanes])


def test_constructed_sets_plan_in_python():
    for name, items, lanes in constructed():
        p = plan(items, lanes)
        assert set(p) == {i["key"] for i in items}, name
        for item in items:
            start = p[item["key"]][0]
            assert all(p[pred][1] <= start for pred in item["preds"]), (name, item["key"])
            assert 0 <= p[item["key"]][2] < lanes


@pytest.mark.parametrize("ci", [None, "true"], ids=["unset", "true"])
def test_agreement_gate_end_to_end(tmp_path, ci):
    code, cases = gate_run(tmp_path, ci, module="tests/test_schedule_agreement.py")
    passed = [kind for kind, _ in cases].count("passed")
    others = [(kind, message) for kind, message in cases if kind != "passed"]
    if ci:
        assert all(kind in ("failure", "error") and NODE_MISSING + CI_SUFFIX in message for kind, message in others), others
        assert code == 1
    else:
        assert all(kind == "skipped" and message == NODE_MISSING for kind, message in others), others
        assert code == 0
    assert len(others) >= 5
    assert passed >= 2


# --- tools/check_schedulers.py -------------------------------------------------------


def run_tool(*args):
    return subprocess.run([sys.executable, str(TOOL), *args], cwd=ROOT, capture_output=True)


def test_check_schedulers_refuses_out_in_repo():
    for out in (ROOT / "check-out", ROOT):
        before = sorted(p.name for p in ROOT.iterdir())
        result = run_tool("--config", "samples/gantry.sample.toml", "--out", str(out))
        assert result.returncode == 2, result.stderr
        assert b"check_schedulers: --out must be outside the repository" in result.stderr
        assert sorted(p.name for p in ROOT.iterdir()) == before


def test_check_schedulers_build_error_names_type_only(tmp_path, capsys, monkeypatch):
    spec = importlib.util.spec_from_file_location("check_schedulers", TOOL)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    def boom(*args, **kwargs):
        raise KeyError("Issue-1 secret title")

    monkeypatch.setattr(tool, "page_model", boom)
    code = tool.main(["--config", str(ROOT / "samples" / "gantry.sample.toml"), "--out", str(tmp_path / "out")])
    out, err = capsys.readouterr()
    assert (code, out) == (2, "")
    assert err.startswith("check_schedulers: ") and err.endswith(": cannot build its page: KeyError\n")
    assert "secret" not in err and "Traceback" not in err


def test_check_schedulers_agrees_on_samples(node, tmp_path):
    result = run_tool("--config", "samples/gantry.sample.toml", "--out", str(tmp_path / "out"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode("utf-8").splitlines() == ["sites 4, skipped 2, leaves 26, lanes 1-8: agreed"]
    assert sorted(p.name for p in (tmp_path / "out" / "sites").iterdir()) == sorted(f"{s}.html" for s in READABLE)
