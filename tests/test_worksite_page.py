"""The worksite page: its model, its rendering and its script (#9).

The model tests need no Node; the page-script tests take the `node` fixture.
Every literal is hand-derived from tests/fixtures/wbs/.
"""

import dataclasses
import fnmatch
import re
import shutil
import tomllib
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest

from commit_check import path_form_hits
from gantry.config import SiteOverride
from gantry.inputs import apply_inputs
from gantry.model import Estimate, Leaf, Models, Row, SiteData
from gantry.sites import SiteEntry
from gantry.render import CLASS_TOKENS
from gantry.schedule import limit, plan
from gantry.views import worksite
from gantry.views.worksite import (
    DATES,
    DATES_ONE,
    ESTIMATES_LINE,
    ESTIMATES_UNDATED,
    INCLUDES,
    INCLUDES_ONE,
    NO_LEAVES,
    NO_OPEN,
    NO_TIERS,
    SCHEDULE_LABEL,
    page_model,
    render_page,
)
from gantry.wbs_stopgap import parse_wbs, read_wbs
from page_safety import CSP_CONTENT, HOSTILE, assert_no_external_resources, assert_page_safe
from page_script.harness import Planted, open_page, run_function
from sample_roles import BUSY, READABLE, UNESTIMATED
from test_samples import ROOT, SAMPLES
from test_source_scan import code_files

FIXTURES = ROOT / "tests" / "fixtures" / "wbs"
INPUTS = ROOT / "tests" / "fixtures" / "inputs"
PAGE_ASSETS = ("gantry/templates/worksite.html", "gantry/static/schedule.js", "gantry/static/worksite.js")


def fixture(name):
    return parse_wbs((FIXTURES / f"{name}.md").read_bytes().decode("utf-8"), "alpha")


def model(name="page", unit=24, data=None):
    return page_model("alpha", fixture(name) if data is None else data, default_unit_hours=unit)


def planned(m, lanes):
    return plan(m["schedule"]["items"], lanes)


def by_key(m):
    return {leaf["key"]: leaf for leaf in m["leaves"]}


def with_leaf(data, key, **changes):
    leaves = tuple(dataclasses.replace(leaf, **changes) if leaf.key == key else leaf for leaf in data.leaves)
    return dataclasses.replace(data, leaves=leaves)


# --- the page model ------------------------------------------------------------


def test_page_fixture_plan_at_4_lanes():
    m = model()
    assert planned(m, 4) == {
        "Issue-2": (0, 24, 0), "Issue-11": (0, 24, 1), "2.6": (0, 24, 2), "Issue-15": (0, 24, 3),
        "Issue-3": (24, 48, 0), "Issue-12": (24, 48, 1), "Issue-20": (24, 48, 2),
        "Issue-4": (48, 72, 0), "Issue-13": (72, 96, 0), "Issue-5": (72, 96, 1),
        "Issue-7": (96, 120, 0), "Issue-9": (96, 120, 1), "Issue-14": (96, 120, 2),
        "1.7": (120, 144, 0),
    }
    assert limit(m["schedule"]["items"], 4) == "chain"
    assert limit(m["schedule"]["items"], 1) == "capacity"


def test_rolling_wave_set():
    leaves = by_key(model())
    boxes = {k for k, leaf in leaves.items() if leaf["linked"] and leaf["wave_full"]}
    circles = {k for k, leaf in leaves.items() if leaf["linked"] and not leaf["wave_full"]}
    assert boxes == {"Issue-2", "Issue-3", "Issue-4", "Issue-5", "Issue-9", "1.7",
                     "Issue-7", "Issue-14", "Issue-11", "Issue-12"}
    assert circles == {"Issue-13"}
    # Each rule on its own: slack, own P1, ~ and the horizon.
    assert all(leaves[k]["slack"] == 0 for k in ("Issue-5", "Issue-9", "1.7"))
    plan4 = planned(model(), 4)
    assert all(plan4[k][0] >= 60 for k in ("Issue-5", "Issue-9", "1.7", "Issue-7", "Issue-14"))
    assert leaves["Issue-7"]["priority"] == "P1" and leaves["Issue-7"]["slack"] == 1
    assert leaves["Issue-14"]["state"] == "in_progress" and leaves["Issue-14"]["slack"] == 1
    assert leaves["Issue-11"]["slack"] == 4 and plan4["Issue-12"][0] < 60
    # Issue-13's P1 is effective (from Issue-7), not its own.
    assert leaves["Issue-13"]["eff"] == 1 and leaves["Issue-13"]["priority"] is None


def test_no_epic_lane():
    m = model()
    assert [lane["key"] for lane in m["lanes"]] == ["Issue-1", "Issue-10", None]
    assert [lane["name"] for lane in m["lanes"]] == ["Ground works", "Frame", "no epic"]
    assert by_key(m)["Issue-20"]["lane"] == 2


def test_panel_lines():
    assert model()["panel"] == [
        "2 leaves have no issue: id and are named by outline position",
        "WBS.md line 15: depends_on entry 'Issue-99' names no issue: id in this file",
    ]


def test_foreign_entries():
    leaves = by_key(model())
    assert leaves["Issue-15"]["foreign"] == ["beta:Issue-3 (on another site)", "gamma:Issue-1 (on another site)"]
    assert leaves["Issue-11"]["foreign"] == ["gamma:Issue-1 (on another site)"]
    ground = [leaf for leaf in leaves.values() if leaf["lane"] == 0]
    assert len(ground) == 6
    assert all(leaf["foreign"] == [] for leaf in ground)


def test_stuck_blocked_parked_are_open_work():
    m = model("states", unit=6)
    leaves = by_key(m)
    assert {k: leaves[k]["sc"] for k in ("Issue-2", "Issue-4", "Issue-6")} == {
        "Issue-2": "st-stuck", "Issue-4": "st-blocked", "Issue-6": "st-parked"}
    assert all(leaves[k]["linked"] for k in ("Issue-2", "Issue-4", "Issue-6"))
    assert {i["key"] for i in m["schedule"]["items"]} >= {"Issue-2", "Issue-4", "Issue-6"}
    assert planned(m, 4) == {
        "Issue-1": (0, 6, 0), "Issue-4": (0, 6, 1), "Issue-6": (0, 6, 2),
        "Issue-2": (6, 12, 0), "Issue-5": (6, 12, 1), "Issue-7": (6, 12, 2),
        "Issue-3": (12, 18, 0),
    }
    assert m["notes"]["stuck"] == "3 stuck, blocked or parked leaves are scheduled as if an agent could start them"
    assert model()["notes"]["stuck"] is None
    one = model("states", unit=6, data=parse_wbs("- [!] Alone {issue: Issue-1}\n", "alpha"))
    assert one["notes"]["stuck"] == "1 stuck, blocked or parked leaf is scheduled as if an agent could start it"


def test_cycle_fixture():
    m = model("cycle")
    assert m["network"]["nodes"] == ["Issue-4", "Issue-5"]
    assert {i["key"] for i in m["schedule"]["items"]} == {"Issue-4", "Issue-5", "Issue-6", "Issue-9"}
    assert m["panel"] == [
        "WBS.md line 2: dependency cycle among 'Issue-1', 'Issue-2'",
        "WBS.md line 2: 'Issue-1' waits on a cycle",
        "WBS.md line 3: 'Issue-2' waits on a cycle",
        "WBS.md line 4: 'Issue-3' waits on a cycle",
    ]
    for lanes in range(1, 9):
        assert len(planned(m, lanes)) == 4


def test_default_unit_hours_drive_durations():
    from test_samples import SAMPLES
    from sample_roles import UNESTIMATED

    data = parse_wbs((SAMPLES / UNESTIMATED / "WBS.md").read_bytes().decode("utf-8"), UNESTIMATED)
    six = page_model(UNESTIMATED, data, default_unit_hours=6)
    assert six["schedule"]["items"]
    assert all(i["hours"] == 6 for i in six["schedule"]["items"])
    assert all(leaf["unestimated"] for leaf in six["leaves"])
    assert six["unestimated"]["site"] == len(six["schedule"]["items"])
    other = page_model(UNESTIMATED, data, default_unit_hours=7.5)
    assert all(i["hours"] == 7.5 for i in other["schedule"]["items"])
    end = lambda m: max(e for _, e, _ in plan(m["schedule"]["items"], 4).values())
    assert end(other) != end(six)


def test_estimate_midpoint():
    data = with_leaf(fixture("page"), "Issue-2", estimate=Estimate(2, 5, "estimates_file", "absent", "S"))
    m = model(data=data)
    leaf = by_key(m)["Issue-2"]
    assert (leaf["hours"], leaf["lo"], leaf["hi"], leaf["unestimated"]) == (3.5, 2, 5, False)
    assert (leaf["source"], leaf["effort"]) == ("estimates_file", "S")
    assert m["unestimated"]["site"] == len(m["schedule"]["items"]) - 1
    # apply_inputs is the one validator of an hours range (tests/test_estimates_file.py).
    default = model(data=with_leaf(fixture("page"), "Issue-2", estimate=Estimate(24, 24, "default", "absent", None)))
    assert by_key(default)["Issue-2"]["unestimated"] is True


def test_model_tiers_table_and_line():
    tiers = Models("opus", "fable", "estimates_file")
    m = model(data=with_leaf(fixture("page"), "Issue-11", models=tiers))
    leaf = by_key(m)["Issue-11"]
    assert leaf["models"] == ["opus", "fable"]
    assert leaf["mc"] == ["m-opus", "m-fable"]
    frame = m["lanes"][1]
    assert {side: dict(pairs) for side, pairs in frame["models"].items()} == {
        "think": {"opus": 1}, "check": {"fable": 1}}
    assert m["notes"]["no_tiers"] is None
    plain = model()
    assert plain["notes"]["no_tiers"] == NO_TIERS
    assert all(leaf["models"] is None and leaf["mc"] is None for leaf in plain["leaves"])


def test_empty_and_all_finished_sites():
    empty = model("empty")
    assert empty["empty"] is True
    assert empty["panel"] == ["WBS.md has no node lines"]
    done = model(data=parse_wbs("- [x] Done {issue: Issue-1}\n", "alpha"))
    assert done["empty"] is False
    assert done["schedule"]["items"] == [] and done["lanes"] == []


# --- the rendered page, read statically ------------------------------------------

# The classes the page's own markup and script use; every other class is a
# CLASS_TOKENS token from the data.
STRUCTURAL = {
    "alt", "arrow", "arrow-crit", "arrow-cross", "band", "band-split", "band-t", "box", "col-cap",
    "col-num", "col-rule", "compact", "controls", "cross", "detail", "dot", "dot-form", "dot-k", "dot-l",
    "e-all", "e-wave", "edge", "edges", "eyebrow", "full-form", "g-axis", "g-grid", "g-hrs",
    "g-leaf", "g-mark", "g-name", "g-prio", "g-sub", "g-toggle", "gantt", "gone", "hot", "k",
    "label", "lane", "lane-free", "lane-label", "lane-name", "lane-num", "legend", "legend-row",
    "lit", "ln", "mk-end", "mode-wave", "mono", "mt-h", "mt-m", "mt-row", "mt-rule", "mt-sw",
    "mt-v", "net", "nid", "nmeta", "node", "note", "ntitle", "outline", "panel", "plate", "prose",
    "seg", "sw", "tracing", "work", "wrap", "zero",
}
VOID = {"meta", "br", "img", "input", "link", "hr"}


class _Tree(HTMLParser):
    """Each element with its parent, and the text outside script and style."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements, self.stack, self.visible, self.foreign = [], [], [], 0

    def set_cdata_mode(self, elem, *args, **kwargs):
        # Inside svg nothing is raw text, as in a browser (page_safety does the same).
        if not self.foreign:
            super().set_cdata_mode(elem, *args, **kwargs)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.foreign += tag == "svg"
        self.elements.append({"tag": tag, "attrs": attrs, "classes": (attrs.get("class") or "").split(),
                              "parent": self.stack[-1] if self.stack else None, "text": []})
        if tag not in VOID:
            self.stack.append(len(self.elements) - 1)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        self.foreign -= tag == "svg" and self.foreign > 0
        for i in range(len(self.stack) - 1, -1, -1):
            if self.elements[self.stack[i]]["tag"] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        for i in self.stack:
            self.elements[i]["text"].append(data)
        if not any(self.elements[i]["tag"] in ("script", "style") for i in self.stack):
            self.visible.append(data)


def tree(page):
    t = _Tree()
    t.feed(page)
    t.close()
    return t


def find(t, tag=None, classes=(), within=None):
    """The indices of the elements with this tag and classes, under `within` if given."""
    out = []
    for i, el in enumerate(t.elements):
        if (tag is None or el["tag"] == tag) and set(classes) <= set(el["classes"]):
            if within is None or within in ancestors(t, i):
                out.append(i)
    return out


def ancestors(t, i):
    found = []
    while (i := t.elements[i]["parent"]) is not None:
        found.append(i)
    return found


def text_of(t, i):
    return "".join(t.elements[i]["text"])


def visible_text(page):
    return "".join(tree(page).visible)


def page_for(name="page", unit=6, data=None, site="alpha"):
    return render_page(site, fixture(name) if data is None else data, default_unit_hours=unit)


def sample_page(site):
    return render_page(site, read_wbs(SAMPLES / site / "WBS.md", site), default_unit_hours=6)


def with_tiers(data, thinking="opus", checking="fable"):
    tiers = Models(thinking, checking, "estimates_file")
    return dataclasses.replace(data, leaves=tuple(dataclasses.replace(leaf, models=tiers) for leaf in data.leaves))


EPIC = "Issue-1"


def _leaf(key, issue, outline, title, epic, line, **kw):
    return Leaf(key, issue, outline, title, " ", "pending", True, kw.pop("priority", None), (),
                kw.pop("meta", {}), epic, line, **kw)


def hostile_site():
    """Site data with a HOSTILE value in every record field a page shows."""
    h = HOSTILE
    leaves = (
        _leaf(h[7], h[7], "1.1", h[0], EPIC, 2, priority=h[5], meta={"issue": h[7], "note": h[6]}, flags=[h[9]]),
        _leaf(h[1], h[1], "1.2", h[8], EPIC, 3, models=Models(h[3], None, "estimates_file")),
        _leaf(h[2], None, "1.3", h[10], EPIC, 4),
        _leaf("Issue-4", "Issue-4", "2", "Plain", None, 5),
    )
    rows = (Row(EPIC, EPIC, "1", h[4], " ", "pending", 1, None, {}),)
    links = ((h[7], h[1]), (h[1], h[2]), (h[2], "Issue-4"))
    return SiteData(leaves, rows, links, (), ())


# Shown on the base page: the keys, titles, epic name, priority and tier.
ALWAYS = [HOSTILE[i] for i in (7, 1, 2, 0, 8, 10, 4, 5, 3)]
# Shown only in the detail of the leaf that holds them: its metadata and flag.
DETAIL = {HOSTILE[7]: [HOSTILE[6], HOSTILE[9]]}


def test_hostile_data_page_is_safe():
    data = assert_page_safe(render_page("alpha", hostile_site(), default_unit_hours=6), HOSTILE)
    leaves = {leaf["key"]: leaf for leaf in data["leaves"]}
    assert list(leaves) == [HOSTILE[7], HOSTILE[1], HOSTILE[2], "Issue-4"]
    first = leaves["__proto__"]
    assert (first["title"], first["priority"], first["flags"]) == (HOSTILE[0], HOSTILE[5], [HOSTILE[9]])
    assert ["note", HOSTILE[6]] in first["meta"]
    assert leaves[HOSTILE[1]]["title"] == HOSTILE[8]
    assert leaves[HOSTILE[1]]["models"] == [HOSTILE[3], None]
    assert (leaves[HOSTILE[2]]["issue"], leaves[HOSTILE[2]]["title"]) == (None, HOSTILE[10])
    assert data["lanes"][0]["name"] == HOSTILE[4]


def built_pages():
    pages = {name: page_for(name) for name in ("page", "states", "cycle", "empty")}
    pages["hostile"] = render_page("alpha", hostile_site(), default_unit_hours=6)
    pages.update({site: sample_page(site) for site in sorted(READABLE)})
    return pages


def test_no_external_resources_on_built_pages():
    pages = built_pages()
    assert len(pages) == 9
    for name, page in pages.items():
        assert_no_external_resources(page)
        assert_page_safe(page, [])
        assert page.startswith("<!doctype html>\n"), name


N = "(?:711|724|729|736|742|748|580|581|705|745)"
DEVAGENT_FORMS = {
    "epic": re.compile("#" + N + r"(?!\d)"),
    "lane order": re.compile(r"(?<!\d)" + N + r"(?:[\s,'\"]+" + N + r")+(?!\d)"),
    "aria-label": re.compile("open devagent WBS leaves", re.I),
    "caption": re.compile("your own attention as a shared limit", re.I),
    "network description": re.compile("issue trust gate", re.I),
    "title": re.compile("devagent WBS Network", re.I),
}
POSITIVE = {
    "epic": "covered by #745.",
    "lane order": "LANE_ORDER = [711, 'Unassigned', 724, 729]",
    "aria-label": "network of the Open Devagent WBS leaves",
    "caption": "does not model your own attention as a shared limit",
    "network description": "in epic 711, the Issue Trust Gate",
    "title": "<title>devagent WBS network</title>",
}


def devagent_hits(text):
    return [name for name, form in DEVAGENT_FORMS.items() if form.search(text)]


def test_no_prototype_devagent_content():
    for name, text in POSITIVE.items():
        assert name in devagent_hits(text), name
    assert devagent_hits("#7110 and 7110, 7240 and 711") == []
    files = [p for p in code_files(ROOT) if p.startswith("gantry/")]
    assert set(PAGE_ASSETS) | {"gantry/views/worksite.py"} <= set(files)
    found = {p: hits for p in files if (hits := devagent_hits((ROOT / p).read_bytes().decode("utf-8")))}
    assert found == {}


def test_page_assets_hold_no_path_form():
    for rel in PAGE_ASSETS:
        assert path_form_hits((ROOT / rel).read_bytes().decode("utf-8")) == [], rel


def test_sample_page_lane_default_4():
    t = tree(sample_page(BUSY))
    (select,) = [i for i in find(t, "select") if t.elements[i]["attrs"].get("data-control") == "lanes"]
    options = [t.elements[i]["attrs"] for i in find(t, "option", within=select)]
    assert [o["value"] for o in options] == [str(n) for n in range(1, 9)]
    assert [o["value"] for o in options if "selected" in o] == ["4"]


def test_page_texts_present():
    page = visible_text(page_for("page"))
    assert SCHEDULE_LABEL in page
    assert "stuck, blocked or parked" not in page
    states = visible_text(page_for("states"))
    assert "3 stuck, blocked or parked leaves are scheduled as if an agent could start them" in states
    empty = visible_text(page_for("empty"))
    assert NO_LEAVES in empty
    done = visible_text(render_page("alpha", parse_wbs("- [x] Done {issue: Issue-1}\n", "alpha"), default_unit_hours=6))
    assert NO_OPEN in done


def bands(t, node):
    return [set(t.elements[i]["classes"]) for i in find(t, "rect", ["band"], within=node)]


def test_model_band_and_table():
    t = tree(page_for(data=with_tiers(fixture("page"))))
    nodes = find(t, "g", ["node"])
    assert len(nodes) == 11
    for node in nodes:
        assert bands(t, node) == [{"band", "m-opus"}, {"band", "m-fable"}]
    counts = []
    for label in find(t, "g", ["lane-label"]):
        rows = find(t, "g", ["mt-row"], within=label)
        counts.append([[text_of(t, j) for j in find(t, "text", within=r)] for r in rows])
    assert counts == [
        [["opus", "6", "–"], ["fable", "–", "6"]],
        [["opus", "7", "–"], ["fable", "–", "7"]],
        [["opus", "1", "–"], ["fable", "–", "1"]],
    ]
    assert NO_TIERS not in visible_text(page_for(data=with_tiers(fixture("page"))))
    plain = page_for()
    assert find(tree(plain), "rect", ["band"]) == []
    assert find(tree(plain), "g", ["mt-row"]) == []
    assert visible_text(plain).count(NO_TIERS) == 1


def test_hostile_tier_gets_fallback_class():
    t = tree(render_page("alpha", hostile_site(), default_unit_hours=6))
    (node,) = [i for i in find(t, "g", ["node"]) if t.elements[i]["attrs"]["data-item"] == HOSTILE[1]]
    assert bands(t, node) == [{"band", "m-other"}, {"band", "m-other"}]
    used = {c for el in t.elements for c in el["classes"]}
    assert used - CLASS_TOKENS - STRUCTURAL == set()


def test_cross_link_is_only_dashed_edge():
    t = tree(page_for())
    ends = {(t.elements[i]["attrs"]["data-u"], t.elements[i]["attrs"]["data-v"])
            for i in find(t, "path", ["edge", "cross"])}
    assert ends == {("Issue-4", "Issue-13")}
    all_ends = {(t.elements[i]["attrs"]["data-u"], t.elements[i]["attrs"]["data-v"]) for i in find(t, "path", ["edge"])}
    assert len(all_ends) == 9


def test_page_assets_are_package_data():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    setuptools = pyproject["tool"]["setuptools"]
    assert {"gantry", "gantry.views"} <= set(setuptools["packages"])
    globs = setuptools["package-data"]["gantry"]
    assets = sorted(p.relative_to(ROOT / "gantry").as_posix()
                    for folder in ("templates", "static") for p in (ROOT / "gantry" / folder).iterdir())
    assert assets == ["static/schedule.js", "static/worksite.js", "templates/worksite.html"]
    for asset in assets:
        assert any(fnmatch.fnmatch(asset, g) for g in globs), asset


@pytest.mark.parametrize("bad", ["x </SCRIPT> y", "x <!-- y"])
def test_render_refuses_unsafe_asset(monkeypatch, bad):
    real = worksite._asset
    monkeypatch.setattr(worksite, "_asset", lambda name: bad if name.endswith(".js") else real(name))
    with pytest.raises(ValueError, match="must not hold"):
        page_for()


# --- the optional inputs on the page (#10) --------------------------------------------


def inputs_for(folder, text, site="alpha", unit=6, estimates=None, configured=None):
    """apply_inputs over WBS.md text in `folder`; `estimates` is copied in as a dated file."""
    folder.mkdir(parents=True, exist_ok=True)
    if estimates is not None:
        shutil.copyfile(estimates, folder / "estimates-2026-01-01.md")
    sites = {} if configured is None else {site: SiteOverride(None, configured)}
    config = SimpleNamespace(sites=sites, default_unit_hours=unit)
    return apply_inputs(SiteEntry(site, folder, None), parse_wbs(text, site), config)


def sample_inputs(site):
    """apply_inputs over a sample site, read where it is."""
    config = SimpleNamespace(sites={}, default_unit_hours=6)
    return apply_inputs(SiteEntry(site, SAMPLES / site, None), read_wbs(SAMPLES / site / "WBS.md", site), config)


def est_inputs(tmp_path):
    text = (INPUTS / "est.md").read_bytes().decode("utf-8")
    return inputs_for(tmp_path / "est", text, estimates=INPUTS / "estimates.md")


def all_estimated_inputs(tmp_path):
    return inputs_for(tmp_path / "all", (INPUTS / "all-estimated.md").read_bytes().decode("utf-8"))


def model_with(inputs, site="alpha", unit=6):
    return page_model(site, inputs.data, default_unit_hours=unit, inputs=inputs)


def page_with(inputs, site="alpha", unit=6):
    return render_page(site, inputs.data, default_unit_hours=unit, inputs=inputs)


def labelled(t, attr="data-unestimated"):
    """{element index: N} for every element that carries `attr`."""
    return {i: int(el["attrs"][attr]) for i, el in enumerate(t.elements) if attr in el["attrs"]}


def by_id(t, ident):
    (i,) = [i for i, el in enumerate(t.elements) if el["attrs"].get("id") == ident]
    return i


def fragments(n):
    return [INCLUDES_ONE, DATES_ONE] if n == 1 else [INCLUDES.format(n=n), DATES.format(n=n)]


def assert_labels_follow_n(t):
    """Each labelled element shows its fragment iff its N is above 0; returns {index: N}."""
    found = labelled(t)
    assert found, "no labelled element"
    for i, n in found.items():
        text = text_of(t, i)
        if n:
            assert any(f in text for f in fragments(n)), (t.elements[i]["tag"], n, text)
        else:
            assert "unestimated lea" not in text, (t.elements[i]["tag"], text)
    return found


def test_unestimated_sample_every_leaf_marked():
    inputs = sample_inputs(UNESTIMATED)
    m = model_with(inputs, UNESTIMATED)
    assert m["leaves"] and all(leaf["unestimated"] and leaf["source"] == "default" for leaf in m["leaves"])
    t = tree(page_with(inputs, UNESTIMATED))
    nodes = find(t, "g", ["node"])
    assert nodes and all("unest" in t.elements[i]["classes"] for i in nodes)
    titles = [text_of(t, find(t, "title", within=i)[0]) for i in nodes]
    assert all(title.endswith(" · unestimated") for title in titles)


def test_unestimated_sample_labels():
    inputs = sample_inputs(UNESTIMATED)
    m = model_with(inputs, UNESTIMATED)
    t = tree(page_with(inputs, UNESTIMATED))
    found = assert_labels_follow_n(t)
    site = m["unestimated"]["site"]
    assert site == inputs.totals.unestimated_count == len(m["schedule"]["items"]) > 0
    gutter = find(t, "g", ["lane-label"])
    assert [found[i] for i in gutter] == [lane["unestimated"] for lane in m["lanes"]]
    assert sum(found[i] for i in gutter) == site
    assert found[by_id(t, "hours-note")] == m["unestimated"]["heaviest"]
    assert found[by_id(t, "g-dates")] == site
    assert text_of(t, by_id(t, "g-dates")) == DATES.format(n=site)


def test_all_estimated_fixture_labels_zero(tmp_path):
    inputs = all_estimated_inputs(tmp_path)
    page = page_with(inputs)
    found = assert_labels_follow_n(tree(page))
    assert set(found.values()) == {0}
    assert "unestimated lea" not in visible_text(page)


def test_mixed_epics_fixture(tmp_path):
    inputs = est_inputs(tmp_path)
    m = model_with(inputs)
    assert [(lane["key"], lane["unestimated"]) for lane in m["lanes"]] == [("Issue-1", 0), ("Issue-6", 6)]
    t = tree(page_with(inputs))
    found = assert_labels_follow_n(t)
    assert [found[i] for i in find(t, "g", ["lane-label"])] == [0, 6]
    assert found[by_id(t, "g-dates")] == 6 == inputs.totals.unestimated_count


def test_estimates_line_names_file_and_date(tmp_path):
    harbor = sample_inputs(BUSY)
    assert ESTIMATES_LINE.format(name=harbor.estimates.name, date="2026-01-15") in visible_text(page_with(harbor, BUSY))
    folder = tmp_path / "configured"
    folder.mkdir()
    shutil.copyfile(INPUTS / "estimates.md", folder / "my-estimates.md")
    text = (INPUTS / "all-estimated.md").read_bytes().decode("utf-8")
    configured = inputs_for(folder, text, configured=folder / "my-estimates.md")
    assert ESTIMATES_UNDATED.format(name="my-estimates.md") in visible_text(page_with(configured))
    quarry = model_with(sample_inputs(UNESTIMATED), UNESTIMATED)
    assert "no estimates file" in quarry["panel"]
    assert quarry["notes"]["estimates"] is None


def test_non_time_line(tmp_path):
    assert "Not in any hour figure: 50.5 pr (2 leaves)" in visible_text(page_with(est_inputs(tmp_path)))
    assert "Not in any hour figure" not in visible_text(page_with(sample_inputs(UNESTIMATED), UNESTIMATED))


def test_panel_groups_unestimated_by_class(tmp_path):
    panel = model_with(est_inputs(tmp_path))["panel"]
    tail = " and no estimates-file hours "
    assert [line for line in panel if tail in line] == [
        "3 leaves with an unparseable est:" + tail + "are unestimated: Issue-7, Issue-8, Issue-9",
        "1 leaf with a non-time est:" + tail + "is unestimated: Issue-10",
        "1 leaf with a placeholder est:" + tail + "is unestimated: Issue-11",
        "1 leaf with no est:" + tail + "is unestimated: Issue-12",
    ]
    assert not [line for line in panel if "scheduled at the default unit" in line]


def test_inherit_tier_is_text_with_fallback(tmp_path):
    t = tree(page_with(est_inputs(tmp_path)))
    (node,) = [i for i in find(t, "g", ["node"]) if t.elements[i]["attrs"]["data-item"] == "Issue-21"]
    assert bands(t, node) == [{"band", "m-other"}, {"band", "m-sonnet"}]
    assert "think inherit" in [text_of(t, i) for i in find(t, "text", ["band-t"], within=node)]


def test_no_tiers_line_and_panel():
    inputs = sample_inputs(UNESTIMATED)
    m = model_with(inputs, UNESTIMATED)
    assert m["notes"]["no_tiers"] == NO_TIERS
    assert NO_TIERS in m["panel"]
    assert visible_text(page_with(inputs, UNESTIMATED)).count(NO_TIERS) == 2


def test_page_model_refuses_untyped_containers():
    for change in ({"estimate": SimpleNamespace(lo_h=2, hi_h=5)}, {"estimate": {"lo_h": 2, "hi_h": 5}},
                   {"models": SimpleNamespace(thinking="opus", checking="opus")}, {"models": ["opus", "opus"]}):
        with pytest.raises(TypeError):
            model(data=with_leaf(fixture("page"), "Issue-2", **change))


def test_interim_note_gone():
    assert not hasattr(worksite, "INTERIM_NOTE")
    for page in (sample_page(UNESTIMATED), page_with(sample_inputs(UNESTIMATED), UNESTIMATED)):
        assert "no estimates are read yet" not in page


# --- P0 on the page (#10) -------------------------------------------------------------


def test_p0_keeps_full_box():
    # Issue-13 is the page fixture's only circle at unit 24 (test_rolling_wave_set).
    m = model(data=with_leaf(fixture("page"), "Issue-13", priority="P0"))
    assert by_key(m)["Issue-13"]["wave_full"] is True
    assert by_key(model())["Issue-13"]["wave_full"] is False


def test_wave_caption_names_p0():
    assert "every leaf whose own priority is P0 or P1" in visible_text(page_for())


def test_p0_note_lists_leaf_and_inheritor(tmp_path):
    inputs = est_inputs(tmp_path)
    assert model_with(inputs)["notes"]["p0"] == ["Issue-20", "Issue-21"]
    assert worksite.P0_NOTE + "Issue-20, Issue-21" in visible_text(page_with(inputs))


def test_no_p0_no_note():
    assert model()["notes"]["p0"] is None
    assert "does not know P0" not in visible_text(page_for())


# --- the page under the harness ----------------------------------------------------


def write(tmp_path, page, name="page.html"):
    path = tmp_path / name
    path.write_bytes(page.encode("utf-8"))
    return path


def keys_of(elements):
    return {e.attrs["data-item"] for e in elements}


def test_wave_toggle(node, tmp_path):
    with open_page(write(tmp_path, page_for(unit=24))) as page:
        (net,) = page.query("svg.net")
        assert "mode-wave" in net.classes
        assert keys_of(page.query(".node.compact")) == {"Issue-13"}
        pressed = {b.attrs["data-value"]: b.attrs["aria-pressed"] for b in page.query('button[data-control="zoom"]')}
        assert pressed == {"wave": "true", "all": "false"}
        page.drive("zoom", "all")
        (net,) = page.query("svg.net")
        assert "mode-wave" not in net.classes
        pressed = {b.attrs["data-value"]: b.attrs["aria-pressed"] for b in page.query('button[data-control="zoom"]')}
        assert pressed == {"wave": "false", "all": "true"}


def test_limit_line_on_page(node, tmp_path):
    with open_page(write(tmp_path, page_for(unit=24))) as page:
        assert [e.text for e in page.query("#g-limit")] == ["With 4 lanes, the dependency chain sets the finish."]
        page.drive("lanes", "1")
        assert [e.text for e in page.query("#g-limit")] == ["With 1 lane, capacity sets the finish."]


def test_no_epic_row_in_schedule(node, tmp_path):
    with open_page(write(tmp_path, page_for())) as page:
        (bar,) = page.query('rect.seg[data-item="Issue-20"]')
        assert bar.attrs["data-lane"] == "2"
        rows = [e for e in page.query(".g-toggle") if e.attrs["data-lane"] == "2"]
        assert len(rows) == 1 and "no epic" in rows[0].text


def test_foreign_entry_in_detail(node, tmp_path):
    with open_page(write(tmp_path, page_for())) as page:
        page.drive("schedule-detail", "Issue-15")
        (detail,) = page.query("#gdetail")
        assert "beta:Issue-3 (on another site)" in detail.text
        assert "gamma:Issue-1 (on another site)" in detail.text


def test_states_markers_on_page(node, tmp_path):
    marks = {"Issue-2": "st-stuck", "Issue-4": "st-blocked", "Issue-6": "st-parked"}
    with open_page(write(tmp_path, page_for("states"))) as page:
        for selector in ("rect.seg", "g.node"):
            found = {e.attrs["data-item"]: e.classes for e in page.query(selector)}
            for key, mark in marks.items():
                assert mark in found[key], (selector, key)


def test_cycle_page_renders(node, tmp_path):
    with open_page(write(tmp_path, page_for("cycle"))) as page:
        drawn = keys_of(page.query("rect.seg")) | keys_of(page.query("g.node"))
        assert drawn.isdisjoint({"Issue-1", "Issue-2", "Issue-3"})
        assert "Issue-4" in keys_of(page.query("rect.seg"))
        panel = [e.text for e in page.query("#panel li")]
        assert [line for line in panel if "waits on a cycle" in line] == [
            "WBS.md line 2: 'Issue-1' waits on a cycle",
            "WBS.md line 3: 'Issue-2' waits on a cycle",
            "WBS.md line 4: 'Issue-3' waits on a cycle",
        ]


def test_hostile_page_under_harness(node, tmp_path):
    path = write(tmp_path, render_page("alpha", hostile_site(), default_unit_hours=6))
    keys = [HOSTILE[7], HOSTILE[1], HOSTILE[2], "Issue-4"]
    shown = {"net": None, "sched": None}
    drives = 0

    def check(page):
        subset = ALWAYS + DETAIL.get(shown["net"], []) + DETAIL.get(shown["sched"], [])
        assert page.planted_nodes(subset) == Planted([], [], [])

    def drive(page, control, value):
        nonlocal drives
        page.drive(control, value)
        drives += 1
        if control == "network-detail":
            shown["net"] = value
        elif control == "schedule-detail":
            shown["sched"] = value
        elif control == "lanes":
            shown["sched"] = None
        check(page)

    with open_page(path) as page:
        check(page)
        for key in keys:
            drive(page, "network-detail", key)
        for key in keys:
            drive(page, "schedule-detail", key)
        for group in ("epic", "lane"):
            drive(page, "group", group)
            if group == "epic":
                drive(page, "expand", "all")
            for lanes in range(1, 9):
                drive(page, "lanes", str(lanes))
                for key in keys:
                    drive(page, "schedule-detail", key)
        assert drives >= 40
        (bar,) = [e for e in page.query("rect.seg") if e.attrs["data-item"] == HOSTILE[1]]
        assert "m-other" in bar.classes
        assert set(bar.classes) <= CLASS_TOKENS | STRUCTURAL
    assert run_function(path, "gantryPage.esc", ["&<>\"'"]) == "&amp;&lt;&gt;&quot;&#39;"


def test_js_class_tokens_match_render(node, tmp_path):
    path = write(tmp_path, page_for())
    assert run_function(path, "gantryPage.classTokens") == sorted(CLASS_TOKENS)


# --- the docs ----------------------------------------------------------------------

PAGE_TEXTS = [getattr(worksite, name) for name in (
    "SCHEDULE_LABEL", "STUCK_LINE", "STUCK_LINE_ONE", "NO_TIERS", "NO_LEAVES", "NO_OPEN", "ALL_HELD",
    "NOTHING_MISSING", "NO_ISSUE_LINE", "NO_ISSUE_LINE_ONE", "WAITS_LINE", "FOREIGN", "CHAIN_LINE",
    "CAPACITY_LINE", "INCLUDES", "INCLUDES_ONE", "DATES", "DATES_ONE", "ESTIMATES_LINE", "ESTIMATES_UNDATED",
    "NON_TIME_LINE", "UNESTIMATED_LINE", "UNESTIMATED_LINE_ONE", "P0_NOTE")] + list(worksite.STATE_MARKS.values())


def test_doc_states_page_texts():
    doc = (ROOT / "docs" / "worksite-page.md").read_bytes().decode("utf-8")
    missing = [text for text in PAGE_TEXTS if f"`{text}`" not in doc]
    assert missing == []
    assert f"`{CSP_CONTENT}`" in doc
    assert "`--site`" in doc
    assert "](page-safety.md" in doc
    assert "file:" + "//" not in doc
    stale = {
        "docs/config.md": "It writes nothing yet",
        "README.md": "nothing built yet",
        "commands/build.md": "pages come in later releases",
        "docs/worksite-page.md": "The page itself comes in #9",
    }
    for rel, text in stale.items():
        assert text not in (ROOT / rel).read_bytes().decode("utf-8"), rel


# --- final-review fixes --------------------------------------------------------------

FORK = "- [ ] A {issue: Issue-1}\n- [ ] B {issue: Issue-2, depends_on: [Issue-1]}\n- [ ] C {issue: Issue-3, depends_on: [Issue-1]}\n"


def test_fork_critical_path_is_one_chain():
    m = model(data=parse_wbs(FORK, "alpha"))
    assert m["network"]["critical"] == ["Issue-1", "Issue-2", "Issue-3"]
    assert m["network"]["path"] == ["Issue-1", "Issue-2"]
    assert m["network"]["heaviest"]["same"] is True
    t = tree(page_for(data=parse_wbs(FORK, "alpha")))
    (detail,) = [i for i in find(t, "div") if t.elements[i]["attrs"].get("id") == "detail"]
    text = text_of(t, detail)
    assert "Issue-1 → Issue-2" in text and "Issue-2 → Issue-3" not in text
    assert "picks the same chain, 12 h end to end" in text


def test_all_held_out_site_says_so():
    data = parse_wbs("- [ ] A {issue: Issue-1, depends_on: [Issue-2]}\n- [ ] B {issue: Issue-2, depends_on: [Issue-1]}\n",
                     "alpha")
    page = visible_text(render_page("alpha", data, default_unit_hours=6))
    assert worksite.ALL_HELD in page
    assert NO_OPEN not in page


def test_critical_cross_link_stays_dashed():
    style = (ROOT / "gantry" / "templates" / "worksite.html").read_bytes().decode("utf-8")
    crit = style.index(".edge.crit {")
    both = re.search(r"\.edge\.crit\.cross \{[^}]*stroke-dasharray: 5 4", style)
    assert both is not None and both.start() > crit
