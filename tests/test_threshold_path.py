"""Threshold and objective paths for a requirement (#79): the threshold path
set, its figures and E_t beside #77's path, the objective mark and the split
path box.

The model tests need no Node; the page-script tests take the `node` fixture.
Literals are hand-derived from each fixture's estimates (HOURS_PER_DAY 6).
"""

import re

import pytest

import helpers_portfolio as hp
from gantry import build
from gantry.config import load_config
from gantry.inputs import scheduled
from gantry.model import SiteData
from gantry.path import build_paths, split_paths, work_order
from gantry.render import CLASS_TOKENS
from gantry.schedule import plan
from gantry.sites import config_roots, load_sites
from gantry.trace import build_site_trace, load_catalog, page_trace
from gantry.views import worksite as w
from gantry.views.worksite import plan_finish_day
from helpers_config import write_site_tree
from page_safety import HOSTILE, _strings, assert_no_external_resources, assert_page_safe
from page_script.harness import Planted, open_page
from sample_roles import SMALL
from test_own_requirements import own_set
from test_requirement_path import CSS_PATH, PATH_KEYS, box, box_items, css_rules, props, site_model, site_read
from test_samples import GANTRY_SAMPLE, ROOT
from test_schedule_agreement import random_dags
from test_trace_cross_site import installation_models
from test_trace_page import (EMPTY_MARKS, hand_trace, hostile_leaf, installation, marked, marks_after,  # noqa: F401
                             render_traced, row, sample_page, select, trace_of)
from test_worksite_page import STRUCTURAL, visible_text

# A trace row's `threshold_path`: `path`'s seven keys over the threshold path set, then these.
THRESHOLD_KEYS = [*PATH_KEYS, "waits", "rest", "objective_lines"]

# The paths fixture of the ACs: a 4-hour gating leaf and an independent 20-hour
# objective-only leaf, ahead by priority.
PATHS = hp.source(("# Alpha\n\n"
                   "- [ ] Core {issue: Issue-1, est: 4h, satisfies: alpha:FR-1}\n"
                   "- [ ] Tail {issue: Issue-2, est: 20h, priority: P1, satisfies: alpha:FR-1, "
                   "objective: alpha:FR-1}\n").encode(),
                  hp.node("Core", issue="Issue-1", est="4h", satisfies="alpha:FR-1"),
                  hp.node("Tail", issue="Issue-2", est="20h", priority="P1", satisfies="alpha:FR-1",
                          objective="alpha:FR-1"),
                  start=3)
FR1 = "# R\n\n## Functional requirements\n\n- FR-1: One.\n"
STATED = ("# R\n\n## Functional requirements\n\n- FR-1: One.\n"
          "  - Threshold: the common cases.\n  - Objective: every case.\n")
# AC4: Gate waits on Mid, which waits on Tail, objective-only.
WAITS = hp.source(("# Alpha\n\n"
                   "- [ ] Gate {issue: Issue-1, est: 1d, depends_on: [Issue-2], satisfies: alpha:FR-1}\n"
                   "- [ ] Mid {issue: Issue-2, est: 1d, depends_on: [Issue-3]}\n"
                   "- [ ] Tail {issue: Issue-3, est: 1d, satisfies: alpha:FR-1, objective: alpha:FR-1}\n").encode(),
                  hp.node("Gate", issue="Issue-1", est="1d", depends_on=["Issue-2"], satisfies="alpha:FR-1"),
                  hp.node("Mid", issue="Issue-2", est="1d", depends_on=["Issue-3"]),
                  hp.node("Tail", issue="Issue-3", est="1d", satisfies="alpha:FR-1", objective="alpha:FR-1"), start=3)


def no_objective(wbs):
    """`wbs` with no objective: alpha:FR-1 key, in its text and in devagent's reading of it."""
    def keep(meta):
        return {key: value for key, value in meta.items() if (key, value) != ("objective", "alpha:FR-1")}

    return hp.Source(wbs.text.replace(b", objective: alpha:FR-1", b""),
                     tuple({**n, "meta": keep(n["meta"])} for n in wbs.nodes), wbs.report)


def split_row(tmp_path, wbs, reqs=FR1):
    m = site_model(tmp_path, wbs, reqs)
    return row(m["trace"], "alpha:FR-1")


# --- the data: split_paths ------------------------------------------------------------


def test_two_sections_figures(tmp_path):
    r = split_row(tmp_path / "split", PATHS)
    t = r["threshold_path"]
    assert t["lines"][0] == "1 leaf, 4 agent-hours to finish its threshold work"
    assert t["objective_lines"][0] == "2 leaves, 24 agent-hours to finish all its serving work"
    assert t["end_day"] == 1
    assert r["path"]["end_day"] == 4
    assert t["order"] == [["Issue-1", "direct"]]
    assert t["rest"] == [["Issue-2", "direct"]]
    assert t["waits"] == []
    assert r["path"] == split_row(tmp_path / "plain", no_objective(PATHS))["path"]


def test_threshold_waits_on_objective_only(tmp_path):
    r = split_row(tmp_path, WAITS)
    t = r["threshold_path"]
    assert t["order"] == [["Issue-3", "upstream"], ["Issue-2", "upstream"], ["Issue-1", "direct"]]
    assert t["lines"][0] == "3 leaves, 18 agent-hours to finish its threshold work"
    assert t["waits"] == ["threshold work waits on objective-only Issue-3"]
    assert t["rest"] == []
    assert "Issue-3" in r["objective_only"]


HELD_TAIL = hp.source(("# Alpha\n\n"
                       "- [ ] Gate {issue: Issue-4, est: 1d, satisfies: alpha:FR-1}\n"
                       "- [ ] One {issue: Issue-1, depends_on: [Issue-2], satisfies: alpha:FR-1, objective: alpha:FR-1}\n"
                       "- [ ] Two {issue: Issue-2, depends_on: [Issue-1, Issue-3]}\n"
                       "- [ ] Three {issue: Issue-3, est: 1d}\n").encode(),
                      hp.node("Gate", issue="Issue-4", est="1d", satisfies="alpha:FR-1"),
                      hp.node("One", issue="Issue-1", depends_on=["Issue-2"], satisfies="alpha:FR-1",
                              objective="alpha:FR-1"),
                      hp.node("Two", issue="Issue-2", depends_on=["Issue-1", "Issue-3"]),
                      hp.node("Three", issue="Issue-3", est="1d"), start=3,
                      report=((4, "dep_cycle", "dependency cycle: Issue-1 (line 4), Issue-2 (line 5)"),))
HELD_GATE = hp.source(("# Alpha\n\n"
                       "- [ ] One {issue: Issue-1, depends_on: [Issue-2], satisfies: alpha:FR-1}\n"
                       "- [ ] Two {issue: Issue-2, depends_on: [Issue-1, Issue-3]}\n"
                       "- [ ] Three {issue: Issue-3, est: 1d}\n"
                       "- [ ] Tail {issue: Issue-4, est: 1d, satisfies: alpha:FR-1, objective: alpha:FR-1}\n").encode(),
                      hp.node("One", issue="Issue-1", depends_on=["Issue-2"], satisfies="alpha:FR-1"),
                      hp.node("Two", issue="Issue-2", depends_on=["Issue-1", "Issue-3"]),
                      hp.node("Three", issue="Issue-3", est="1d"),
                      hp.node("Tail", issue="Issue-4", est="1d", satisfies="alpha:FR-1", objective="alpha:FR-1"),
                      start=3, report=((3, "dep_cycle", "dependency cycle: Issue-1 (line 3), Issue-2 (line 4)"),))


def test_held_objective_only_leaf_is_no_reason(tmp_path):
    r = split_row(tmp_path, HELD_TAIL)
    assert r["threshold_path"]["incomplete"] == []
    assert r["path"]["incomplete"] == [w.PATH_HELD.format(key="Issue-1")]
    assert r["threshold_path"]["order"] == [["Issue-4", "direct"]]


def test_held_gating_leaf_is_a_reason(tmp_path):
    r = split_row(tmp_path, HELD_GATE)
    assert r["threshold_path"]["incomplete"] == [w.PATH_HELD.format(key="Issue-1")]
    assert r["path"]["incomplete"] == [w.PATH_HELD.format(key="Issue-1")]


def test_other_site_objective_only_leaf_is_no_reason(tmp_path):
    def alpha_row(case, beta):
        config_path = installation(tmp_path / case, {
            "alpha": hp.source(("# Alpha\n\n- [ ] Core {issue: Issue-1, est: 1d, satisfies: alpha:FR-1}\n"
                                "- [ ] Tail {issue: Issue-2, est: 1d, satisfies: alpha:FR-1, "
                                "objective: alpha:FR-1}\n").encode(),
                               hp.node("Core", issue="Issue-1", est="1d", satisfies="alpha:FR-1"),
                               hp.node("Tail", issue="Issue-2", est="1d", satisfies="alpha:FR-1",
                                       objective="alpha:FR-1"),
                               start=3),
            "beta": beta}, {"alpha": FR1})
        return row(installation_models(config_path)["alpha"]["trace"], "alpha:FR-1")

    also = w.PATH_OTHER_SITE.format(site="beta", issue="Issue-7")
    r = alpha_row("tail", hp.source(b"# Beta\n\n- [ ] Far {issue: Issue-7, satisfies: alpha:FR-1, "
                                    b"objective: alpha:FR-1}\n",
                                    hp.node("Far", issue="Issue-7", satisfies="alpha:FR-1", objective="alpha:FR-1"),
                                    start=3))
    assert also in r["path"]["incomplete"]
    assert also not in r["threshold_path"]["incomplete"]
    r = alpha_row("gate", hp.source(b"# Beta\n\n- [ ] Far {issue: Issue-7, satisfies: alpha:FR-1}\n",
                                    hp.node("Far", issue="Issue-7", satisfies="alpha:FR-1"), start=3))
    assert also in r["path"]["incomplete"] and also in r["threshold_path"]["incomplete"]


def test_other_site_open_objective_only_leaf_splits_the_row(tmp_path):
    config_path = installation(tmp_path, {
        "alpha": hp.source(b"# Alpha\n\n- [ ] Core {issue: Issue-1, est: 1d, satisfies: alpha:FR-1}\n",
                           hp.node("Core", issue="Issue-1", est="1d", satisfies="alpha:FR-1"), start=3),
        "beta": hp.source(b"# Beta\n\n- [ ] Far {issue: Issue-7, satisfies: alpha:FR-1, objective: alpha:FR-1}\n",
                          hp.node("Far", issue="Issue-7", satisfies="alpha:FR-1", objective="alpha:FR-1"), start=3)},
        {"alpha": FR1})
    r = row(installation_models(config_path)["alpha"]["trace"], "alpha:FR-1")
    assert r["threshold_path"]["order"] == [["Issue-1", "direct"]]
    assert r["threshold_path"]["incomplete"] == []


def test_no_open_objective_only_leaf_keeps_77(tmp_path):
    finished = hp.source(PATHS.text.replace(b"- [ ] Tail", b"- [x] Tail"),
                         hp.node("Core", issue="Issue-1", est="4h", satisfies="alpha:FR-1"),
                         hp.node("Tail", glyph="x", issue="Issue-2", est="20h", priority="P1", satisfies="alpha:FR-1",
                                 objective="alpha:FR-1"), start=3)
    r = split_row(tmp_path / "finished", finished, STATED)
    assert r["threshold_path"] is None
    assert r["path"] == split_row(tmp_path / "plain", no_objective(finished), STATED)["path"]
    assert (r["threshold"], r["objective"]) == ("the common cases.", "every case.")
    r = split_row(tmp_path / "keyless", no_objective(PATHS), STATED)
    assert r["threshold_path"] is None
    assert (r["threshold"], r["objective"]) == ("the common cases.", "every case.")


def test_threshold_status():
    assert [w.path_status(*case, w.THRESHOLD_TEXTS) for case in (
        ("unserved", "site", None), ("unserved", "installation", ("marsh",)), ("unserved", "installation", ()),
        ("finished", "site", None), ("finished", "installation", ()), ("finished", "installation", ("marsh",)))] == [
        w.PATH_T_UNSERVED_SITE, w.PATH_T_UNSERVED_SITE, w.PATH_T_UNSERVED,
        w.PATH_T_FINISHED_SITE, w.PATH_T_FINISHED, w.PATH_T_FINISHED]
    assert (w.PATH_T_UNSERVED, w.PATH_T_UNSERVED_SITE) == ("No leaf gates its threshold",
                                                          "No leaf on this site gates its threshold")
    assert (w.PATH_T_FINISHED, w.PATH_T_FINISHED_SITE) == ("Its threshold work is finished",
                                                          "Its threshold work on this site is finished")


ONLY_TAIL = hp.source(b"# Alpha\n\n- [ ] Tail {issue: Issue-2, est: 1d, satisfies: alpha:FR-1, objective: alpha:FR-1}\n",
                      hp.node("Tail", issue="Issue-2", est="1d", satisfies="alpha:FR-1", objective="alpha:FR-1"),
                      start=3)
DONE_GATE = hp.source(("# Alpha\n\n- [x] Core {issue: Issue-1, satisfies: alpha:FR-1}\n"
                       "- [ ] Tail {issue: Issue-2, est: 1d, satisfies: alpha:FR-1, objective: alpha:FR-1}\n").encode(),
                      hp.node("Core", glyph="x", issue="Issue-1", satisfies="alpha:FR-1"),
                      hp.node("Tail", issue="Issue-2", est="1d", satisfies="alpha:FR-1", objective="alpha:FR-1"),
                      start=3)
TAIL_FIGURES = ["1 leaf, 6 agent-hours to finish all its serving work",
                "Longest chain: 6 agent-hours, about 1 working day"]


def test_threshold_empty_cases(tmp_path):
    for case, wbs, status in (("none", ONLY_TAIL, w.PATH_T_UNSERVED_SITE), ("done", DONE_GATE, w.PATH_T_FINISHED_SITE)):
        t = split_row(tmp_path / case, wbs)["threshold_path"]
        assert t["lines"] == [status], case
        assert t["order"] == [] and t["targets"] == [] and t["end_day"] is None and t["rest"] == [["Issue-2", "direct"]]
        assert t["objective_lines"] == TAIL_FIGURES, case
    # A finished objective-only leaf gates nothing, so no leaf gates: here, or on another site in a full build.
    done_tail = hp.source(ONLY_TAIL.text + b"- [x] Old {issue: Issue-1, satisfies: alpha:FR-1, objective: alpha:FR-1}\n",
                          hp.node("Tail", issue="Issue-2", est="1d", satisfies="alpha:FR-1", objective="alpha:FR-1"),
                          hp.node("Old", glyph="x", issue="Issue-1", satisfies="alpha:FR-1", objective="alpha:FR-1"),
                          start=3)
    assert split_row(tmp_path / "done-tail", done_tail)["threshold_path"]["lines"] == [w.PATH_T_UNSERVED_SITE]
    done_far = hp.source(b"# Beta\n\n- [x] Far {issue: Issue-7, satisfies: alpha:FR-1, objective: alpha:FR-1}\n",
                         hp.node("Far", glyph="x", issue="Issue-7", satisfies="alpha:FR-1", objective="alpha:FR-1"),
                         start=3)
    config_path = installation(tmp_path / "done-tail-all", {"alpha": ONLY_TAIL, "beta": done_far}, {"alpha": FR1})
    t = row(trace_of(build.build_all(load_config(config_path)).pages["alpha"]), "alpha:FR-1")["threshold_path"]
    assert t["lines"] == [w.PATH_T_UNSERVED]
    # A full build that read every site: the unscoped texts.
    for case, wbs, status in (("none-all", ONLY_TAIL, w.PATH_T_UNSERVED), ("done-all", DONE_GATE, w.PATH_T_FINISHED)):
        config_path = installation(tmp_path / case, {"alpha": wbs}, {"alpha": FR1})
        t = row(trace_of(build.build_all(load_config(config_path)).pages["alpha"]), "alpha:FR-1")["threshold_path"]
        assert t["lines"] == [status] and t["incomplete"] == [], case
    # A site not read: "No leaf on this site gates", but "Its threshold work is finished", with the reason.
    for case, wbs, status in (("none-unread", ONLY_TAIL, w.PATH_T_UNSERVED_SITE),
                              ("done-unread", DONE_GATE, w.PATH_T_FINISHED)):
        config = write_site_tree(tmp_path / case, {"alpha": wbs,
                                                    "gamma": hp.source(b"# Gamma\n\n- [ ] Bad \xff {issue: Issue-3}\n")},
                                 tail='[site.alpha]\nrequirements = "req-alpha.md"\n')
        (tmp_path / case / "req-alpha.md").write_bytes(FR1.encode("utf-8"))
        t = row(trace_of(build.build_all(load_config(config)).pages["alpha"]), "alpha:FR-1")["threshold_path"]
        assert t["lines"] == [status], case
        assert t["incomplete"][-1] == w.PATH_NOT_READ.format(sites="gamma"), case
        assert t["objective_lines"] == TAIL_FIGURES, case


def test_threshold_all_gating_held(tmp_path):
    t = split_row(tmp_path / "one", HELD_GATE)["threshold_path"]
    assert t["lines"][0] == "1 upstream leaf, 6 agent-hours feed its held-out threshold work"
    assert t["targets"] == [] and t["end_day"] is None
    assert t["incomplete"] == [w.PATH_HELD.format(key="Issue-1")]
    two = hp.source(HELD_GATE.text.replace(b"depends_on: [Issue-1, Issue-3]", b"depends_on: [Issue-1, Issue-3, Issue-5]")
                    + b"- [ ] Five {issue: Issue-5, est: 1d}\n",
                    hp.node("One", issue="Issue-1", depends_on=["Issue-2"], satisfies="alpha:FR-1"),
                    hp.node("Two", issue="Issue-2", depends_on=["Issue-1", "Issue-3", "Issue-5"]),
                    hp.node("Three", issue="Issue-3", est="1d"),
                    hp.node("Tail", issue="Issue-4", est="1d", satisfies="alpha:FR-1", objective="alpha:FR-1"),
                    hp.node("Five", issue="Issue-5", est="1d"), start=3,
                    report=((3, "dep_cycle", "dependency cycle: Issue-1 (line 3), Issue-2 (line 4)"),))
    t = split_row(tmp_path / "two", two)["threshold_path"]
    assert t["lines"][0] == "2 upstream leaves, 12 agent-hours feed its held-out threshold work"


def check_split(items, links, lit, tail):
    """split_paths over one row "R" whose `tail` keys of `lit` are objective-only: the
    threshold set is a subset of the path set and closed under preds (U3)."""
    paths = build_paths({"R": (lit, [], 0)}, items, unestimated=set(), links=links, foreign={})
    gating = {"R": ([x for x in lit if x[0] not in tail], [], 0)}
    threshold = build_paths(gating, items, unestimated=set(), links=links, foreign={})
    s = split_paths(paths, threshold, {"R": tail})["R"]
    members = {k for k, _ in s.threshold.order}
    assert members <= {k for k, _ in paths["R"].order}
    by_key = {item["key"]: item for item in items}
    assert all(p in members for k in members for p in by_key[k]["preds"])
    assert [k for k, _ in s.rest] == [k for k, _ in paths["R"].order if k not in members]
    assert list(s.waits) == [k for k, _ in s.threshold.order if k in tail]
    return s


def test_threshold_set_is_closed_subset(real_sample_document):
    # U3: the threshold set is a subset of the path set and closed under preds, so work_order never raises.
    for items, _ in random_dags(count=48, seed=79):
        links = [(p, item["key"]) for item in items for p in item["preds"]]
        keys = [item["key"] for item in items]
        check_split(items, links, [(k, "direct") for k in keys[-3:]], {keys[-1]})
        check_split(items, links, [(k, "direct") for k in keys[::2]], set(keys[::4]))
    _, data = site_read(GANTRY_SAMPLE, SMALL)
    m = render_traced(GANTRY_SAMPLE, SMALL)[1]
    items = m["schedule"]["items"]
    assert len(items) == len(scheduled(data))
    for r in m["trace"]["requirements"]:
        lit = [tuple(x) for x in r["light"]]
        if len(lit) > 1:
            check_split(items, list(data.links), lit, {lit[-1][0]})


def test_threshold_keys_exact(tmp_path):
    m = site_model(tmp_path, PATHS)
    for r in m["trace"]["requirements"]:
        assert list(r["path"]) == PATH_KEYS
        assert r["threshold_path"] is None or list(r["threshold_path"]) == THRESHOLD_KEYS
    assert list(row(m["trace"], "alpha:FR-1")["threshold_path"]) == THRESHOLD_KEYS


def test_constraint_row_has_no_threshold_path(tmp_path):
    reqs = FR1 + "\n## Constraints\n\n- CON-1: Bound.\n"
    wbs = hp.source(PATHS.text.replace(b"satisfies: alpha:FR-1, objective: alpha:FR-1",
                                       b"satisfies: [alpha:FR-1, alpha:CON-1], objective: alpha:CON-1"),
                    hp.node("Core", issue="Issue-1", est="4h", satisfies="alpha:FR-1"),
                    hp.node("Tail", issue="Issue-2", est="20h", priority="P1", satisfies=["alpha:FR-1", "alpha:CON-1"],
                            objective="alpha:CON-1"), start=3)
    m = site_model(tmp_path, wbs, reqs)
    con = row(m["trace"], "alpha:CON-1")
    assert con["objective_only"] == ["Issue-2"]
    assert con["threshold_path"] is None and con["path"]["state"] == "constraint"


def test_threshold_equals_path_when_tail_is_upstream_only(tmp_path):
    r = split_row(tmp_path, WAITS)
    t = r["threshold_path"]
    # The same set; the tail is a target of the path but upstream of the threshold's.
    assert [k for k, _ in t["order"]] == [k for k, _ in r["path"]["order"]] and t["rest"] == []
    assert r["path"]["order"][0] == ["Issue-3", "direct"] and t["order"][0] == ["Issue-3", "upstream"]
    assert (t["chain"], t["targets"]) == (r["path"]["chain"], ["Issue-1"])
    assert t["objective_lines"][0] == "3 leaves, 18 agent-hours to finish all its serving work"
    assert work_order([item for item in site_model(tmp_path / "b", WAITS)["schedule"]["items"]]) == [
        "Issue-3", "Issue-2", "Issue-1"]


def test_texts_carry_the_threshold_words(tmp_path):
    texts = site_model(tmp_path, PATHS)["texts"]
    assert (texts["path_end_t"], texts["section_t"], texts["section_o"], texts["mark_objective"]) == (
        w.PATH_T_END, "Threshold", "Objective", "objective")
    assert w.PATH_T_END.format(lanes="4 lanes", day=1) == (
        "In the current schedule on 4 lanes, its threshold work ends in working day 1")


def test_render_traced_still_renders(tmp_path):
    page, m = render_traced(installation(tmp_path, {"alpha": PATHS}, {"alpha": FR1}), "alpha")
    assert row(m["trace"], "alpha:FR-1")["threshold_path"] is not None
    assert "threshold_path" in page


# --- the selection function: the objective mark ---------------------------------------------


def page_file(tmp_path, wbs, reqs=FR1):
    """(path, trace, data) of site alpha's page, written to tmp_path as a --site build renders it."""
    html, _ = render_traced(installation(tmp_path / "site", {"alpha": wbs}, {"alpha": reqs}), "alpha")
    path = tmp_path / "alpha.html"
    path.write_bytes(html.encode("utf-8"))
    data = assert_page_safe(html, [])
    return path, data["trace"], data


def test_objective_mark_set_and_cleared(node, tmp_path):
    path, trace, _ = page_file(tmp_path, PATHS)
    assert marks_after(path, trace, [select("alpha:FR-1")])["objective"] == ["Issue-2"]
    assert marks_after(path, trace, [select("alpha:FR-1"), select("alpha:FR-1")]) == EMPTY_MARKS
    escape = {"type": "key", "key": "Escape", "id": None}
    assert marks_after(path, trace, [select("alpha:FR-1"), escape]) == EMPTY_MARKS


def test_objective_mark_survives_hover_and_leave(node, tmp_path):
    path, trace, _ = page_file(tmp_path, PATHS)
    marks = marks_after(path, trace, [select("alpha:FR-1"), {"type": "hover", "leaf": "Issue-1"}])
    assert marks["objective"] == ["Issue-2"] and marks["tracing"] == "Issue-1"
    marks = marks_after(path, trace, [select("alpha:FR-1"), {"type": "hover", "leaf": "Issue-1"}, {"type": "leave"}])
    assert marks["objective"] == ["Issue-2"] and marks["tracing"] is None


def test_proto_objective_marks_exactly_it(node, sample_page):
    path, _, _ = sample_page
    trace = hand_trace([["__proto__", "direct"], ["constructor", "direct"]])
    trace["requirements"][0]["objective_only"] = ["__proto__"]
    assert marks_after(path, trace, [select("alpha:FR-1")])["objective"] == ["__proto__"]


def test_objective_only_undrawn_leaf_not_marked(node, sample_page):
    path, _, _ = sample_page
    trace = hand_trace([["A", "direct"], ["C", "indirect"]])
    trace["requirements"][0]["objective_only"] = ["B", "C", 7, None, ["A"], "A"]
    assert marks_after(path, trace, [select("alpha:FR-1")])["objective"] == ["C", "A"]
    assert marks_after(path, hand_trace([["A", "direct"]]), [select("alpha:FR-1")])["objective"] == []


# --- the page: the split box, E_t, the mark's look --------------------------------------


def children(page):
    """(tag, class, text) of each element directly in the path box, in order."""
    return [(e.tag.lower(), e.attrs.get("class", ""), e.text) for e in page.query("#req-path > *")]


def shape(page):
    return [(tag, cls) for tag, cls, _ in children(page)]


def test_split_box_on_fixture(node, tmp_path):
    path, trace, _ = page_file(tmp_path, PATHS)
    with open_page(path) as page:
        page.drive("requirement", "alpha:FR-1")
        assert "gone" not in box(page).classes
        assert shape(page) == [("p", "path-section"), ("p", "label"), ("p", ""), ("p", ""), ("p", "path-end-t"),
                               ("p", "path-section"), ("p", "label"), ("p", ""), ("p", ""), ("p", "path-end"),
                               ("ul", "path-list"), ("hr", "path-divider"), ("ul", "path-list")]
        assert [e.text for e in page.query("#req-path .path-section")] == ["Threshold", "Objective"]
        text = box(page).text
        assert "1 leaf, 4 agent-hours to finish its threshold work" in text
        assert "2 leaves, 24 agent-hours to finish all its serving work" in text
        (end_t,) = page.query("#req-path .path-end-t")
        assert end_t.text == w.PATH_T_END.format(lanes="4 lanes", day=1)
        assert end_t.attrs.get("aria-live") == "polite"
        assert [e.text for e in page.query("#req-path .path-end")] == [w.path_end_text(4, 4)]
        assert box_items(page, "path-list") == ["Issue-1 · Issue-1 · Core · 4 h · direct · chain",
                                                "Issue-2 · Issue-2 · Tail · 20 h · direct · objective · chain"]
        assert [e.text for e in page.query("#req-path .label")].count(w.SCHEDULE_LABEL) == 2


def test_two_chains(node, tmp_path):
    # The graph's chain marks follow the whole selection's path; the threshold list's chain word, the threshold's.
    path, trace, _ = page_file(tmp_path, PATHS)
    r = row(trace, "alpha:FR-1")
    assert (r["path"]["chain"], r["threshold_path"]["chain"]) == (["Issue-2"], ["Issue-1"])
    with open_page(path) as page:
        page.drive("requirement", "alpha:FR-1")
        assert marked(page, "rect.seg", "req-chain") == {"Issue-2"}
        # Issue-1 is the threshold's chain, though its bar carries no req-chain.
        assert box_items(page, "path-list")[0] == "Issue-1 · Issue-1 · Core · 4 h · direct · chain"


def test_e_t_follows_lanes(node, tmp_path):
    path, trace, data = page_file(tmp_path, PATHS)
    t = row(trace, "alpha:FR-1")["threshold_path"]
    items = data["schedule"]["items"]
    one = plan_finish_day([item for item in items if item["key"] in t["targets"]], plan(items, 1))
    assert (t["end_day"], one) == (1, 4)
    default = w.PATH_T_END.format(lanes="4 lanes", day=t["end_day"])
    with open_page(path) as page:
        page.drive("requirement", "alpha:FR-1")
        assert [e.text for e in page.query("#req-path .path-end-t")] == [default]
        page.drive("lanes", "1")
        (end_t,) = page.query("#req-path .path-end-t")
        assert end_t.text == w.PATH_T_END.format(lanes="1 lane", day=one) != default
        assert [e.text for e in page.query("#req-path .path-end")] == [w.path_end_text(one, 1)]


def test_waits_line_and_upstream_objective_row(node, tmp_path):
    path, _, _ = page_file(tmp_path, WAITS)
    with open_page(path) as page:
        page.drive("requirement", "alpha:FR-1")
        assert box_items(page, "path-waits") == ["threshold work waits on objective-only Issue-3"]
        items = box_items(page, "path-list")
        assert items[0].startswith("Issue-3 ") and items[0].endswith(" · upstream · objective · chain")
        # rest is empty: one list, no divider.
        assert len(page.query("#req-path ul.path-list")) == 1 and page.query("#req-path .path-divider") == []


def test_objective_mark_on_page(node, tmp_path):
    def marks(page):
        return marked(page, "g.node", "req-objective"), marked(page, "rect.seg", "req-objective")

    path, _, _ = page_file(tmp_path / "waits", WAITS)
    with open_page(path) as page:
        page.drive("requirement", "alpha:FR-1")
        assert marks(page) == ({"Issue-3"}, {"Issue-3"})
        page.drive("network-detail", "Issue-2")
        page.leave("network-detail", "Issue-2")
        assert marks(page) == ({"Issue-3"}, {"Issue-3"})
        page.drive("lanes", "1")
        assert marks(page) == ({"Issue-3"}, {"Issue-3"})
        page.drive("group", "lane")
        assert marks(page) == ({"Issue-3"}, {"Issue-3"})
        page.drive("requirement", "alpha:FR-1")
        assert marks(page) == (set(), set())
    path, _, _ = page_file(tmp_path / "paths", PATHS)
    with open_page(path) as page:
        page.drive("requirement", "alpha:FR-1")
        assert marked(page, "rect.seg", "req-objective") == {"Issue-2"}
        assert page.query("g.node") == []  # unlinked leaves are not drawn in the network


@pytest.mark.parametrize(("reqs", "above"), [
    (STATED, [("p", "path-section", "Threshold"), ("p", "path-statement", "the common cases."),
              ("p", "path-section", "Objective"), ("p", "path-statement", "every case.")]),
    (FR1 + "  - Threshold: t\n", [("p", "path-section", "Threshold"), ("p", "path-statement", "t")]),
    (FR1 + "  - Objective:\n", [("p", "path-section", "Objective")]),
])
def test_unsplit_box_shows_statements(node, tmp_path, reqs, above):
    def box_of(case, reqs_):
        path, trace, _ = page_file(tmp_path / case, no_objective(PATHS), reqs_)
        assert row(trace, "alpha:FR-1")["threshold_path"] is None
        with open_page(path) as page:
            page.drive("requirement", "alpha:FR-1")
            return children(page)

    plain = box_of("plain", FR1)
    assert plain[0][1] == "label" and ("p", "path-end", w.path_end_text(4, 4)) in plain
    assert box_of("stated", reqs) == above + plain
    assert not any(cls in ("path-end-t", "path-divider", "path-waits") for _, cls, _ in plain)


def test_threshold_empty_case_box(node, tmp_path):
    path, trace, _ = page_file(tmp_path, ONLY_TAIL)
    with open_page(path) as page:
        page.drive("requirement", "alpha:FR-1")
        assert shape(page) == [("p", "path-section"), ("p", ""), ("p", "path-section"), ("p", "label"), ("p", ""),
                               ("p", ""), ("p", "path-end"), ("ul", "path-list")]
        assert children(page)[1][2] == w.PATH_T_UNSERVED_SITE
        assert [c[2] for c in children(page)[4:6]] == TAIL_FIGURES
        assert page.query("#req-path .path-end-t, #req-path .path-divider") == []


def reasons_by_section(page):
    """{section word: the texts of the path-why lists after it}."""
    found, word = {}, None
    for _, cls, text in children(page):
        if cls == "path-section":
            word = text
            found[word] = []
        elif cls == "path-why":
            found[word].append(text)
    return found


def test_split_box_reasons_by_section(node, tmp_path):
    # AC5 on the page: a held-out objective-only leaf is a reason in the Objective section only.
    held = w.PATH_HELD.format(key="Issue-1")
    for case, wbs, threshold in (("tail", HELD_TAIL, []), ("gate", HELD_GATE, [held])):
        (tmp_path / case).mkdir()
        path, _, _ = page_file(tmp_path / case, wbs)
        with open_page(path) as page:
            page.drive("requirement", "alpha:FR-1")
            assert reasons_by_section(page) == {"Threshold": threshold, "Objective": [held]}, case


def test_objective_css():
    rules = [(s, props(b)) for s, b in css_rules(CSS_PATH.read_bytes().decode("utf-8")) if "req-objective" in s]
    assert [s for s, _ in rules] == [".net.selecting:not(.tracing) .node.req-objective",
                                     "#gantt.selecting .seg.req-objective"]
    assert all(list(p) == ["opacity"] and 0.3 < float(p["opacity"]) < 1 for _, p in rules)


def test_structural_classes_cover_the_box():
    assert {"req-objective", "path-section", "path-statement", "path-end-t", "path-waits", "path-divider"} <= STRUCTURAL


# --- hostile strings ----------------------------------------------------------------------


def hostile_threshold_site(tmp_path):
    """(page, planted, malformed): site alpha in the trace. FR-1 states HOSTILE[0] as its
    threshold and HOSTILE[2] as its objective; its gating leaf waits, through a mid leaf,
    on an objective-only leaf keyed and numbered HOSTILE[1] and titled HOSTILE[3]; a
    fourth leaf's objective: holds HOSTILE[5], a malformed entry. The requirements file
    is on disk; the leaves are built in memory, as hostile_path_site builds them."""
    h = HOSTILE
    reqs = ("# R\n\n## Functional requirements\n\n- FR-1: One.\n"
            f"  - Threshold: {h[0]}\n  - Objective: {h[2]}\n")
    config_path = installation(tmp_path, {"alpha": hp.source(b"# A\n\n- [ ] One {issue: Issue-1}\n",
                                                             hp.node("One", issue="Issue-1"), start=3)}, {"alpha": reqs})
    config = load_config(config_path)
    roots = config_roots(config)
    catalog = load_catalog(roots, config, load_sites(config, roots)[0])
    leaves = (
        hostile_leaf("Issue-1", "Issue-1", 1, satisfies="alpha:FR-1"),
        hostile_leaf("Issue-2", "Issue-2", 2),
        hostile_leaf(h[1], h[1], 3, title=h[3], satisfies="alpha:FR-1", objective="alpha:FR-1"),
        hostile_leaf("Issue-4", "Issue-4", 4, satisfies="alpha:FR-1", objective=h[5]),
    )
    data = SiteData(leaves, (), ((h[1], "Issue-2"), ("Issue-2", "Issue-1")), (), (), schema_models=True)
    trace = page_trace(build_site_trace("alpha", leaves, catalog), catalog, data)
    page = w.render_page("alpha", data, default_unit_hours=6, trace=trace)
    malformed = (f"WBS.md line 4: objective: entry {h[5]!r} is not a full requirement ID "
                 "(<namespace>:<code>-<n>); it is ignored")
    return page, [h[0], h[1], h[2], h[3], h[5]], malformed


def test_hostile_threshold_page_is_safe(tmp_path):
    page, planted, malformed = hostile_threshold_site(tmp_path)
    # HOSTILE[5] is only ever inside its malformed entry's message, so not a whole embedded value.
    data = assert_page_safe(page, [p for p in planted if p != HOSTILE[5]])
    assert_no_external_resources(page)
    # The title is in the leaves data, not the trace; every planted value is whole in a string of the data.
    strings = list(_strings(data))
    assert [p for p in planted if not any(p in s for s in strings)] == []
    r = row(data["trace"], "alpha:FR-1")
    assert (r["threshold"], r["objective"]) == (HOSTILE[0], HOSTILE[2])
    assert r["objective_only"] == [HOSTILE[1]]
    assert r["threshold_path"]["waits"] == [w.PATH_WAITS.format(key=HOSTILE[1])]
    assert malformed in visible_text(page)


def test_hostile_threshold_under_harness(node, tmp_path):
    page_text, planted, _ = hostile_threshold_site(tmp_path)
    path = tmp_path / "hostile.html"
    path.write_bytes(page_text.encode("utf-8"))
    with open_page(path) as page:
        # The statements are shown only in the path box, on selection: unrendered until then.
        assert page.planted_nodes(planted) == Planted([], [], [HOSTILE[0], HOSTILE[2]])
        page.drive("requirement", "alpha:FR-1")
        assert page.planted_nodes(planted) == Planted([], [], [])
        assert box_items(page, "path-waits") == [w.PATH_WAITS.format(key=HOSTILE[1])]
        assert [e.text for e in page.query("#req-path .path-statement")] == [HOSTILE[0], HOSTILE[2]]
        assert box_items(page, "path-list")[0].startswith(HOSTILE[1] + " · " + HOSTILE[1] + " · " + HOSTILE[3])
        assert marked(page, "rect.seg", "req-objective") == {HOSTILE[1]}
        used = {c for e in page.query("[class]") for c in e.classes}
        assert used - CLASS_TOKENS - STRUCTURAL == set()


# --- the sample ------------------------------------------------------------------------------


def test_sample_split_box(node, sample_page):
    path, trace, _ = sample_page
    r = row(trace, "lighthouse:FR-3")
    t = r["threshold_path"]
    assert r["objective_only"] == ["Issue-11"] and t is not None
    with open_page(path) as page:
        page.drive("requirement", "lighthouse:FR-3")
        assert [e.text for e in page.query("#req-path .path-section")] == ["Threshold", "Objective"]
        assert [e.text for e in page.query("#req-path .path-statement")] == [r["threshold"], r["objective"]]
        assert marked(page, "rect.seg", "req-objective") == {"Issue-11"}
        assert [e.text for e in page.query("#req-path .path-end-t")] == [
            w.PATH_T_END.format(lanes="4 lanes", day=t["end_day"])]
        assert [e.text for e in page.query("#req-path .path-end")] == [w.path_end_text(r["path"]["end_day"], 4)]


# --- requirements and docs -------------------------------------------------------------------


def test_own_requirements_state_the_threshold():
    reqs = own_set().requirements
    fr27 = reqs["gantry:FR-27"]
    assert fr27.layer == "FR" and str(fr27.refines) == "gantry:SR-4"
    assert "gantry:FR-27" in [str(n) for n in reqs["gantry:UC-5"].needs]
    assert all((r.threshold, r.objective) == (None, None) for r in reqs.values())


def test_doc_states_threshold_record():
    doc =(ROOT / "docs" / "worksite-page.md").read_bytes().decode("utf-8")
    flat = " ".join(doc.split())
    assert [key for key in THRESHOLD_KEYS if f"`{key}`" not in doc] == []
    for key in ("threshold", "objective", "objective_only", "objective_only_foreign", "threshold_path"):
        assert f"`{key}`" in doc, key
    (classes,) = [s for s in re.split(r"(?<=\.)\s", flat) if s.startswith("A selection sets only")]
    assert "`req-objective`" in classes
    assert "`objective`" in flat and "E_t" in flat
