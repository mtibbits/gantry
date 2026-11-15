"""Threshold and objective paths for a requirement (#79): the threshold path
set, its figures and E_t beside #77's path, the objective mark and the split
path box.

The model tests need no Node; the page-script tests take the `node` fixture.
Literals are hand-derived from each fixture's estimates (HOURS_PER_DAY 6).
"""

from gantry import build
from gantry.config import load_config
from gantry.inputs import scheduled
from gantry.path import build_paths, split_paths, work_order
from gantry.views import worksite
from gantry.views.worksite import THRESHOLD_KEYS
from helpers_config import write_site_tree
from sample_roles import SMALL
from test_requirement_path import PATH_KEYS, site_model, path_of, site_read
from test_samples import GANTRY_SAMPLE
from test_schedule_agreement import random_dags
from test_trace_cross_site import installation_models
from page_safety import assert_page_safe
from test_trace_page import (EMPTY_MARKS, hand_trace, installation, marks_after, render_traced, row,  # noqa: F401
                             sample_page, select, trace_of)

w = worksite

# The paths fixture of the ACs: a 4-hour gating leaf and an independent 20-hour
# objective-only leaf, ahead by priority.
PATHS = ("# Alpha\n\n"
         "- [ ] Core {issue: Issue-1, est: 4h, satisfies: alpha:FR-1}\n"
         "- [ ] Tail {issue: Issue-2, est: 20h, priority: P1, satisfies: alpha:FR-1, objective: alpha:FR-1}\n")
FR1 = "# R\n\n## Functional requirements\n\n- FR-1: One.\n"
STATED = ("# R\n\n## Functional requirements\n\n- FR-1: One.\n"
          "  - Threshold: the common cases.\n  - Objective: every case.\n")
# AC4: Gate waits on Mid, which waits on Tail, objective-only.
WAITS = ("# Alpha\n\n"
         "- [ ] Gate {issue: Issue-1, est: 1d, depends_on: [Issue-2], satisfies: alpha:FR-1}\n"
         "- [ ] Mid {issue: Issue-2, est: 1d, depends_on: [Issue-3]}\n"
         "- [ ] Tail {issue: Issue-3, est: 1d, satisfies: alpha:FR-1, objective: alpha:FR-1}\n")


def no_objective(wbs):
    return wbs.replace(", objective: alpha:FR-1", "")


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


HELD_TAIL = ("# Alpha\n\n"
             "- [ ] Gate {issue: Issue-4, est: 1d, satisfies: alpha:FR-1}\n"
             "- [ ] One {issue: Issue-1, depends_on: [Issue-2], satisfies: alpha:FR-1, objective: alpha:FR-1}\n"
             "- [ ] Two {issue: Issue-2, depends_on: [Issue-1, Issue-3]}\n"
             "- [ ] Three {issue: Issue-3, est: 1d}\n")
HELD_GATE = ("# Alpha\n\n"
             "- [ ] One {issue: Issue-1, depends_on: [Issue-2], satisfies: alpha:FR-1}\n"
             "- [ ] Two {issue: Issue-2, depends_on: [Issue-1, Issue-3]}\n"
             "- [ ] Three {issue: Issue-3, est: 1d}\n"
             "- [ ] Tail {issue: Issue-4, est: 1d, satisfies: alpha:FR-1, objective: alpha:FR-1}\n")


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
            "alpha": ("# Alpha\n\n- [ ] Core {issue: Issue-1, est: 1d, satisfies: alpha:FR-1}\n"
                      "- [ ] Tail {issue: Issue-2, est: 1d, satisfies: alpha:FR-1, objective: alpha:FR-1}\n"),
            "beta": beta}, {"alpha": FR1})
        return row(installation_models(config_path)["alpha"]["trace"], "alpha:FR-1")

    also = w.PATH_OTHER_SITE.format(site="beta", issue="Issue-7")
    r = alpha_row("tail", "# Beta\n\n- [ ] Far {issue: Issue-7, satisfies: alpha:FR-1, objective: alpha:FR-1}\n")
    assert also in r["path"]["incomplete"]
    assert also not in r["threshold_path"]["incomplete"]
    r = alpha_row("gate", "# Beta\n\n- [ ] Far {issue: Issue-7, satisfies: alpha:FR-1}\n")
    assert also in r["path"]["incomplete"] and also in r["threshold_path"]["incomplete"]


def test_other_site_open_objective_only_leaf_splits_the_row(tmp_path):
    config_path = installation(tmp_path, {
        "alpha": "# Alpha\n\n- [ ] Core {issue: Issue-1, est: 1d, satisfies: alpha:FR-1}\n",
        "beta": "# Beta\n\n- [ ] Far {issue: Issue-7, satisfies: alpha:FR-1, objective: alpha:FR-1}\n"},
        {"alpha": FR1})
    r = row(installation_models(config_path)["alpha"]["trace"], "alpha:FR-1")
    assert r["threshold_path"]["order"] == [["Issue-1", "direct"]]
    assert r["threshold_path"]["incomplete"] == []


def test_no_open_objective_only_leaf_keeps_77(tmp_path):
    finished = PATHS.replace("- [ ] Tail", "- [x] Tail")
    r = split_row(tmp_path / "finished", finished, STATED)
    assert r["threshold_path"] is None
    assert r["path"] == split_row(tmp_path / "plain", no_objective(finished), STATED)["path"]
    assert (r["threshold"], r["objective"]) == ("the common cases.", "every case.")
    r = split_row(tmp_path / "keyless", no_objective(PATHS), STATED)
    assert r["threshold_path"] is None
    assert (r["threshold"], r["objective"]) == ("the common cases.", "every case.")


def test_threshold_status():
    assert [w.threshold_status(*case) for case in (
        ("unserved", "site", None), ("unserved", "installation", ("marsh",)), ("unserved", "installation", ()),
        ("finished", "site", None), ("finished", "installation", ()), ("finished", "installation", ("marsh",)))] == [
        w.PATH_T_UNSERVED_SITE, w.PATH_T_UNSERVED_SITE, w.PATH_T_UNSERVED,
        w.PATH_T_FINISHED_SITE, w.PATH_T_FINISHED, w.PATH_T_FINISHED]
    assert (w.PATH_T_UNSERVED, w.PATH_T_UNSERVED_SITE) == ("No leaf gates its threshold",
                                                          "No leaf on this site gates its threshold")
    assert (w.PATH_T_FINISHED, w.PATH_T_FINISHED_SITE) == ("Its threshold work is finished",
                                                          "Its threshold work on this site is finished")


ONLY_TAIL = "# Alpha\n\n- [ ] Tail {issue: Issue-2, est: 1d, satisfies: alpha:FR-1, objective: alpha:FR-1}\n"
DONE_GATE = ("# Alpha\n\n- [x] Core {issue: Issue-1, satisfies: alpha:FR-1}\n"
             "- [ ] Tail {issue: Issue-2, est: 1d, satisfies: alpha:FR-1, objective: alpha:FR-1}\n")
TAIL_FIGURES = ["1 leaf, 6 agent-hours to finish all its serving work",
                "Longest chain: 6 agent-hours, about 1 working day"]


def test_threshold_empty_cases(tmp_path):
    for case, wbs, status in (("none", ONLY_TAIL, w.PATH_T_UNSERVED_SITE), ("done", DONE_GATE, w.PATH_T_FINISHED_SITE)):
        t = split_row(tmp_path / case, wbs)["threshold_path"]
        assert t["lines"] == [status], case
        assert t["order"] == [] and t["targets"] == [] and t["end_day"] is None and t["rest"] == [["Issue-2", "direct"]]
        assert t["objective_lines"] == TAIL_FIGURES, case
    # A full build that read every site: the unscoped texts.
    for case, wbs, status in (("none-all", ONLY_TAIL, w.PATH_T_UNSERVED), ("done-all", DONE_GATE, w.PATH_T_FINISHED)):
        config_path = installation(tmp_path / case, {"alpha": wbs}, {"alpha": FR1})
        t = row(trace_of(build.build_all(load_config(config_path)).pages["alpha"]), "alpha:FR-1")["threshold_path"]
        assert t["lines"] == [status] and t["incomplete"] == [], case
    # A site not read: "No leaf on this site gates", but "Its threshold work is finished", with the reason.
    for case, wbs, status in (("none-unread", ONLY_TAIL, w.PATH_T_UNSERVED_SITE),
                              ("done-unread", DONE_GATE, w.PATH_T_FINISHED)):
        config = write_site_tree(tmp_path / case, {"alpha": wbs.encode("utf-8"),
                                                    "gamma": b"# Gamma\n\n- [ ] Bad \xff {issue: Issue-3}\n"},
                                 tail='[site.alpha]\nrequirements = "req-alpha.md"\n')
        (tmp_path / case / "req-alpha.md").write_bytes(FR1.encode("utf-8"))
        t = row(trace_of(build.build_all(load_config(config)).pages["alpha"]), "alpha:FR-1")["threshold_path"]
        assert t["lines"] == [status], case
        assert t["incomplete"][-1] == w.PATH_NOT_READ.format(sites="gamma"), case
        assert t["objective_lines"] == TAIL_FIGURES, case


CYCLE_GATE = ("# Alpha\n\n"
              "- [ ] One {issue: Issue-1, depends_on: [Issue-2], satisfies: alpha:FR-1}\n"
              "- [ ] Two {issue: Issue-2, depends_on: [Issue-1, Issue-3]}\n"
              "- [ ] Three {issue: Issue-3, est: 1d}\n"
              "- [ ] Tail {issue: Issue-4, est: 1d, satisfies: alpha:FR-1, objective: alpha:FR-1}\n")


def test_threshold_all_gating_held(tmp_path):
    t = split_row(tmp_path / "one", CYCLE_GATE)["threshold_path"]
    assert t["lines"][0] == "1 upstream leaf, 6 agent-hours feed its held-out threshold work"
    assert t["targets"] == [] and t["end_day"] is None
    assert t["incomplete"] == [w.PATH_HELD.format(key="Issue-1")]
    two = CYCLE_GATE.replace("depends_on: [Issue-1, Issue-3]", "depends_on: [Issue-1, Issue-3, Issue-5]") + (
        "- [ ] Five {issue: Issue-5, est: 1d}\n")
    t = split_row(tmp_path / "two", two)["threshold_path"]
    assert t["lines"][0] == "2 upstream leaves, 12 agent-hours feed its held-out threshold work"


def check_split(items, links, lit, tail):
    """split_paths over one row "R" whose `tail` keys of `lit` are objective-only: the
    threshold set is a subset of the path set and closed under preds (U3)."""
    paths = build_paths({"R": (lit, [], 0)}, items, unestimated=set(), links=links, foreign={})
    gating = {"R": ([x for x in lit if x[0] not in tail], [], 0)}
    s = split_paths(paths, gating, {"R": tail}, items, unestimated=set(), links=links, foreign={})["R"]
    members = {k for k, _ in s.threshold.order}
    assert members <= {k for k, _ in paths["R"].order}
    by_key = {item["key"]: item for item in items}
    assert all(p in members for k in members for p in by_key[k]["preds"])
    assert [k for k, _ in s.rest] == [k for k, _ in paths["R"].order if k not in members]
    assert list(s.waits) == [k for k, _ in s.threshold.order if k in tail]
    return s


def test_threshold_set_is_closed_subset():
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
    assert THRESHOLD_KEYS == [*PATH_KEYS, "waits", "rest", "objective_lines"]
    m = site_model(tmp_path, PATHS)
    for r in m["trace"]["requirements"]:
        assert list(r["path"]) == PATH_KEYS
        assert r["threshold_path"] is None or list(r["threshold_path"]) == THRESHOLD_KEYS
    assert list(row(m["trace"], "alpha:FR-1")["threshold_path"]) == THRESHOLD_KEYS


def test_constraint_row_has_no_threshold_path(tmp_path):
    reqs = FR1 + "\n## Constraints\n\n- CON-1: Bound.\n"
    wbs = PATHS.replace("satisfies: alpha:FR-1, objective: alpha:FR-1",
                        "satisfies: [alpha:FR-1, alpha:CON-1], objective: alpha:CON-1")
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
    """(path, trace) of site alpha's page, written to tmp_path as a --site build renders it."""
    html, _ = render_traced(installation(tmp_path / "site", {"alpha": wbs}, {"alpha": reqs}), "alpha")
    path = tmp_path / "alpha.html"
    path.write_bytes(html.encode("utf-8"))
    return path, assert_page_safe(html, [])["trace"]


def test_objective_mark_set_and_cleared(node, tmp_path):
    path, trace = page_file(tmp_path, PATHS)
    assert marks_after(path, trace, [select("alpha:FR-1")])["objective"] == ["Issue-2"]
    assert marks_after(path, trace, [select("alpha:FR-1"), select("alpha:FR-1")]) == EMPTY_MARKS
    escape = {"type": "key", "key": "Escape", "id": None}
    assert marks_after(path, trace, [select("alpha:FR-1"), escape]) == EMPTY_MARKS


def test_objective_mark_survives_hover_and_leave(node, tmp_path):
    path, trace = page_file(tmp_path, PATHS)
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
