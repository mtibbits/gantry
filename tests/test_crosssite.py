"""Cross-site depends_on resolution (#68): gantry/crosssite.py and its page."""

import copy
import dataclasses
import re
from collections import Counter
from pathlib import Path

import pytest

from gantry import build
from gantry.build import CLASH, build_all, read_sites, site_list, write_all
from gantry.config import load_config
from gantry.deps import classify, refs
from gantry.crosssite import MESSAGES, NO_ISSUE, NOT_BUILT, resolve_foreign
from gantry.model import REPORT_KINDS, meta_values, report_order
from gantry.sites import config_roots
from gantry.wbs_stopgap import parse_wbs
from gantry.views import worksite
from gantry.views.worksite import (
    BW,
    COLW,
    FBOX_LINE,
    FLAG,
    FOREIGN,
    FOREIGN_UNRESOLVED,
    GUT,
    LIMITS_CAPTION_NO_CHAIN,
    NO_CHAIN,
    NO_CHAIN_XSITE,
    SLACK_NONE,
    SUMMARY_NO_CHAIN,
)
from helpers_config import (
    copy_samples,
    demo_build,
    lands,
    page_ids,
    page_links,
    run_main,
    sample_build,
    sample_projects,
    write_site_tree,
)
from page_safety import HOSTILE, assert_no_external_resources, assert_page_safe
from page_script.harness import open_page
from sample_roles import BUSY, NO_WBS, OWN_UNIT, SMALL, UNESTIMATED, UNREADABLE
from test_samples import ROOT
from test_worksite_page import ancestors, by_key, find, model_with, text_of, tree, with_leaf


def test_report_kinds_registered():
    i = REPORT_KINDS.index("malformed_ref")
    assert REPORT_KINDS[i + 1:i + 3] == ("foreign_site_absent", "foreign_unresolved")


def test_leaf_defaults_unresolved():
    data = parse_wbs("- [ ] One {issue: Issue-1, depends_on: [beta:Issue-1]}\n", "alpha")
    (leaf,) = data.leaves
    assert leaf.waits_on_other_site is False
    assert leaf.foreign_targets is None


# --- resolve_foreign (Task 2) --------------------------------------------------------

FOREIGN_KINDS = {"foreign_site_absent", "foreign_unresolved"}


def read_tree(tmp_path, sites):
    """(sites, inputs, reasons) for a synthetic tree; `sites` maps a name to its WBS.md text."""
    raw = {name: (text.encode("utf-8") if isinstance(text, str) else text) for name, text in sites.items()}
    config = load_config(write_site_tree(tmp_path, raw))
    roots = config_roots(config)
    entries, _ = site_list(config, roots)
    inputs, reasons = read_sites(config, entries, roots=roots)
    return entries, inputs, reasons


def resolve_tree(tmp_path, sites):
    return resolve_foreign(*read_tree(tmp_path, sites))


def leaf(resolved, site, key):
    return next(x for x in resolved[site].data.leaves if x.key == key)


def foreign_report(resolved, site):
    return [e for e in resolved[site].data.report if e.kind in FOREIGN_KINDS]


def test_open_leaf_outcome(tmp_path):
    resolved, foreign = resolve_tree(tmp_path, {
        "alpha": "- [ ] Wait {issue: Issue-1, depends_on: [beta:Issue-1]}\n",
        "beta": "- [ ] Pour the base {issue: Issue-1}\n",
    })
    (o,) = foreign["alpha"]
    assert (o.status, o.target_key, o.title, o.href) == ("open", "Issue-1", "Pour the base", "beta.html#leaf-Issue-1")
    assert (o.site, o.source_key, o.source_line, o.raw, o.target_site, o.target_issue) == (
        "alpha", "Issue-1", 1, "beta:Issue-1", "beta", "Issue-1")
    w = leaf(resolved, "alpha", "Issue-1")
    assert w.waits_on_other_site is True
    assert w.foreign_targets == (o,)
    assert foreign["beta"] == ()
    assert leaf(resolved, "beta", "Issue-1").foreign_targets == ()


def test_finished_target_satisfied(tmp_path):
    resolved, foreign = resolve_tree(tmp_path, {
        "alpha": "- [ ] Wait {issue: Issue-1, depends_on: [beta:Issue-1]}\n",
        "beta": "- [x] Pour the base {issue: Issue-1}\n",
    })
    (o,) = foreign["alpha"]
    assert (o.status, o.target_key, o.href, o.reason) == ("satisfied", "Issue-1", None, None)
    w = leaf(resolved, "alpha", "Issue-1")
    assert (w.waits_on_other_site, w.foreign_targets) == (False, ())
    assert foreign_report(resolved, "alpha") == []


ROW_BETA = ("- [ ] The yard {{issue: Issue-2}}\n"
            "  - [{0}] Clear {{issue: Issue-3}}\n"
            "  - [{1}] Fence {{issue: Issue-4}}\n"
            "  - [{1}] Gate {{issue: Issue-5}}\n")


def test_row_target_first_open_leaf(tmp_path):
    alpha = "- [ ] Wait {issue: Issue-1, depends_on: [beta:Issue-2]}\n"
    _, foreign = resolve_tree(tmp_path / "open", {"alpha": alpha, "beta": ROW_BETA.format("x", " ")})
    (o,) = foreign["alpha"]
    assert (o.status, o.target_is_row, o.target_key, o.title, o.href) == (
        "open", True, "Issue-2", "The yard", "beta.html#leaf-Issue-4")
    _, foreign = resolve_tree(tmp_path / "done", {"alpha": alpha, "beta": ROW_BETA.format("x", "x")})
    (o,) = foreign["alpha"]
    assert (o.status, o.target_is_row, o.href) == ("satisfied", True, None)


def test_absent_and_case(tmp_path):
    resolved, foreign = resolve_tree(tmp_path, {
        "alpha": "- [ ] Wait {issue: Issue-1, depends_on: [gamma:Issue-1, Beta:Issue-1]}\n",
        "beta": "- [ ] Pour {issue: Issue-1}\n",
    })
    assert [(o.raw, o.status) for o in foreign["alpha"]] == [("gamma:Issue-1", "absent"), ("Beta:Issue-1", "absent")]
    report = foreign_report(resolved, "alpha")
    assert [e.kind for e in report] == ["foreign_site_absent"] * 2
    assert all(e.message.endswith("names a site not in this installation") for e in report)
    assert not any(e.kind == "unresolved_ref" for e in resolved["alpha"].data.report)


GOOD_BETA = "- [ ] Pour {issue: Issue-1}\n"
NOT_UTF8 = b"- [ ] Pour {issue: Issue-1}\n\xff\n"
UNRESOLVED = {
    "no_wbs": ({"beta": None}, "beta:Issue-1",
               NOT_BUILT.format(site="beta", reason="no WBS.md in the planning directory")),
    "not_utf8": ({"beta": NOT_UTF8}, "beta:Issue-1",
                 NOT_BUILT.format(site="beta", reason=f"WBS.md is not valid UTF-8 at byte {NOT_UTF8.index(b'\xff')}")),
    "clash": ({"beta": GOOD_BETA, "Beta": GOOD_BETA}, "Beta:Issue-1",
              NOT_BUILT.format(site="Beta", reason=CLASH.format(earlier="beta"))),
    "no_issue": ({"beta": GOOD_BETA}, "beta:Issue-9", NO_ISSUE.format(site="beta", issue="Issue-9")),
    "outline": ({"beta": "- [ ] Row\n  - [ ] Keyless\n"}, "beta:1.1", NO_ISSUE.format(site="beta", issue="1.1")),
}


@pytest.mark.parametrize("case", UNRESOLVED)
def test_unresolved_reasons(tmp_path, case):
    others, entry, reason = UNRESOLVED[case]
    resolved, foreign = resolve_tree(tmp_path, {
        "alpha": f"- [ ] Wait {{issue: Issue-1, depends_on: [{entry}]}}\n", **others})
    (o,) = foreign["alpha"]
    assert (o.status, o.reason, o.href) == ("unresolved", reason, None)
    assert reason and not reason.endswith(": ")
    (e,) = foreign_report(resolved, "alpha")
    assert (e.kind, e.line, e.message) == (
        "foreign_unresolved", 1, MESSAGES["foreign_unresolved"].format(line=1, entry=entry, reason=reason))
    assert leaf(resolved, "alpha", "Issue-1").foreign_targets == (o,)


def test_row_source_each_open_leaf_report_once(tmp_path):
    resolved, foreign = resolve_tree(tmp_path, {"alpha": (
        "- [ ] Row {depends_on: [ferry:Issue-1]}\n"
        "  - [ ] A {issue: Issue-1}\n"
        "  - [ ] B {issue: Issue-2}\n"
        "  - [x] C {issue: Issue-3}\n")})
    (o,) = foreign["alpha"]
    assert (o.source_key, o.source_is_row, o.status) == ("1", True, "absent")
    assert [leaf(resolved, "alpha", k).waits_on_other_site for k in ("Issue-1", "Issue-2", "Issue-3")] == [
        True, True, False]
    assert leaf(resolved, "alpha", "Issue-3").foreign_targets is None
    assert [(e.kind, e.line) for e in foreign_report(resolved, "alpha")] == [("foreign_site_absent", 1)]


def test_duplicate_target_one_box(tmp_path):
    resolved, foreign = resolve_tree(tmp_path, {
        "alpha": ("- [ ] Row {depends_on: [beta:Issue-1]}\n"
                  "  - [ ] A {issue: Issue-1, depends_on: [beta:Issue-1, beta:Issue-1]}\n"),
        "beta": GOOD_BETA,
    })
    assert len(foreign["alpha"]) == 3
    assert len(leaf(resolved, "alpha", "Issue-1").foreign_targets) == 1


def test_finished_source_checked_not_flagged(tmp_path):
    resolved, foreign = resolve_tree(tmp_path, {"alpha": "- [x] Done {issue: Issue-1, depends_on: [ferry:Issue-1]}\n"})
    assert [o.status for o in foreign["alpha"]] == ["absent"]
    w = leaf(resolved, "alpha", "Issue-1")
    assert (w.waits_on_other_site, w.foreign_targets) == (False, None)
    assert [e.kind for e in foreign_report(resolved, "alpha")] == ["foreign_site_absent"]


def test_held_out_leaf_flagged(tmp_path):
    resolved, _ = resolve_tree(tmp_path, {
        "alpha": ("- [ ] A {issue: Issue-1, depends_on: [Issue-2, beta:Issue-1]}\n"
                  "- [ ] B {issue: Issue-2, depends_on: [Issue-1]}\n"),
        "beta": GOOD_BETA,
    })
    assert "Issue-1" in resolved["alpha"].data.held_out
    w = leaf(resolved, "alpha", "Issue-1")
    assert w.waits_on_other_site is True
    assert [o.status for o in w.foreign_targets] == ["open"]


def test_own_prefix_is_local(tmp_path):
    resolved, foreign = resolve_tree(tmp_path, {"alpha": (
        "- [ ] One {issue: Issue-1}\n"
        "- [ ] Two {issue: Issue-2, depends_on: [alpha:Issue-1]}\n")})
    assert resolved["alpha"].data.links == (("Issue-1", "Issue-2"),)
    assert foreign["alpha"] == ()
    assert foreign_report(resolved, "alpha") == []


MIXED = {
    "alpha": ("- [ ] Wait {issue: Issue-1, depends_on: "
              "[beta:Issue-1, beta:Issue-3, gamma:Issue-1, beta:Issue-9, beta:Issue-2]}\n"
              "- [ ] Local {issue: Issue-7, depends_on: [Issue-404]}\n"),
    "beta": ("- [ ] Pour {issue: Issue-1}\n"
             "- [ ] The yard {issue: Issue-2}\n"
             "  - [x] Clear {issue: Issue-3}\n"
             "  - [ ] Fence {issue: Issue-4}\n"),
}


def test_outcome_shape(tmp_path):
    _, foreign = resolve_tree(tmp_path, MIXED)
    outcomes = [o for site in foreign.values() for o in site]
    assert sorted(o.status for o in outcomes) == ["absent", "open", "open", "satisfied", "unresolved"]
    for o in outcomes:
        if o.status == "open":
            assert o.title and o.href and o.target_key and o.reason is None
        elif o.status == "satisfied":
            assert o.target_key and o.href is None and o.reason is None
        elif o.status == "absent":
            assert (o.target_key, o.title, o.href, o.reason, o.target_is_row) == (None, None, None, None, None)
        else:
            assert o.status == "unresolved" and o.reason and o.href is None


def test_report_in_report_order(tmp_path):
    resolved, _ = resolve_tree(tmp_path, MIXED)
    report = resolved["alpha"].data.report
    assert {e.kind for e in report} >= FOREIGN_KINDS | {"unresolved_ref"}
    assert list(report) == sorted(report, key=report_order)


def test_pure(tmp_path):
    entries, inputs, reasons = read_tree(tmp_path, MIXED)
    before = copy.deepcopy((inputs, reasons))
    first = resolve_foreign(entries, inputs, reasons)
    assert (inputs, reasons) == before
    assert resolve_foreign(entries, inputs, reasons) == first


# --- build_all and the render-failure pass (Task 3) ------------------------------------


def tree_config(tmp_path, sites):
    raw = {name: text.encode("utf-8") for name, text in sites.items()}
    return load_config(write_site_tree(tmp_path, raw))


def force_render(monkeypatch, fail, why="forced"):
    """Make render_model raise ValueError(why) for a site while `fail(site, nth
    render of it)` is true; returns the {site: render_site calls} counter."""
    calls = {}
    real_site, real_model = build.render_site, worksite.render_model

    def render_site(name, *args, **kw):
        calls[name] = calls.get(name, 0) + 1
        return real_site(name, *args, **kw)

    def render_model(m, **kw):
        if fail(m["site"], calls[m["site"]]):
            raise ValueError(why)
        return real_model(m, **kw)

    monkeypatch.setattr(build, "render_site", render_site)
    monkeypatch.setattr(worksite, "render_model", render_model)
    return calls


def test_render_failure_reresolves(tmp_path, monkeypatch):
    calls = force_render(monkeypatch, lambda site, n: site == "beta")
    result = build_all(tree_config(tmp_path, {
        "alpha": "- [ ] Wait {issue: Issue-1, depends_on: [beta:Issue-1]}\n",
        "beta": "- [ ] Pour {issue: Issue-1}\n",
    }))
    (o,) = result.foreign["alpha"]
    assert (o.status, o.reason) == ("unresolved", "beta is not built: render error: ValueError: forced")
    assert list(result.pages) == ["alpha"]
    assert page_links(result.pages["alpha"]) == []
    assert calls == {"alpha": 2, "beta": 1}
    assert [e.message for e in result.inputs["alpha"].data.report if e.kind in FOREIGN_KINDS] == [
        MESSAGES["foreign_unresolved"].format(line=1, entry="beta:Issue-1", reason=o.reason)]


def test_render_failure_chain_bounded(tmp_path, monkeypatch):
    sites = {
        "alpha": "- [ ] A {issue: Issue-1, depends_on: [beta:Issue-1]}\n",
        "beta": "- [ ] B {issue: Issue-1, depends_on: [gamma:Issue-1]}\n",
        "gamma": "- [ ] C {issue: Issue-1}\n",
    }
    calls = force_render(monkeypatch, lambda site, n: site == "gamma" or (site == "beta" and n == 2))
    result = build_all(tree_config(tmp_path, sites))
    summaries = {s.name: s.status for s in result.summaries}
    assert summaries == {"alpha": "built", "beta": "not_built", "gamma": "not_built"}
    (o,) = result.foreign["alpha"]
    assert (o.status, o.reason) == ("unresolved", "beta is not built: render error: ValueError: forced")
    assert list(result.pages) == ["alpha"]
    assert not any("beta.html" in page or "gamma.html" in page for page in result.pages.values())
    # Pass 1 renders each site once; each later pass re-renders only the waiting sites.
    assert calls == {"alpha": 2, "beta": 2, "gamma": 1}
    assert sum(calls.values()) - len(sites) <= len(sites)


def test_site_mode_keeps_text(tmp_path):
    entries = ["beta:Issue-1", "ferry:Issue-1", "beta:Issue-9"]
    config = write_site_tree(tmp_path, {
        "alpha": ("- [ ] Wait {issue: Issue-1, depends_on: [%s]}\n" % ", ".join(entries)).encode(),
        "beta": b"- [ ] Pour {issue: Issue-1}\n",
    })
    code, _, err = run_main("--config", str(config), "--site", "alpha")
    assert code == 0, err
    page = (tmp_path / "out" / "sites" / "alpha.html").read_text(encoding="utf-8")
    for raw in entries:
        assert FOREIGN.format(raw=raw) in page
    assert page_links(page) == []
    assert "names a site not in this installation" not in page
    assert " is unresolved: " not in page


# --- the page (Task 4) ---------------------------------------------------------------

BETA = "- [ ] Pour the base {issue: Issue-1}\n"


def full_build(tmp_path, sites):
    return build_all(tree_config(tmp_path, sites))


def model_of(result, site):
    return model_with(result.inputs[site], site, result.config.default_unit_hours)


def node_el(t, key):
    (i,) = [i for i in find(t, "g", ["node"]) if t.elements[i]["attrs"].get("data-item") == key]
    return i


def fboxes(t):
    return find(t, None, ["fbox"])


def box_texts(t):
    return [text_of(t, find(t, "text", ["fid"], within=i)[0]) for i in fboxes(t)]


def test_box_drawn_and_linked(tmp_path):
    result = full_build(tmp_path, {
        "alpha": "- [ ] Lay the deck {issue: Issue-1, depends_on: [beta:Issue-1]}\n- [ ] Rest {issue: Issue-2}\n",
        "beta": BETA,
    })
    t = tree(result.pages["alpha"])
    (a,) = find(t, "a", ["fbox"])
    assert t.elements[a]["attrs"]["href"] == "beta.html#leaf-Issue-1"
    assert "beta:Issue-1" in text_of(t, a) and "Pour the base" in text_of(t, a)
    assert by_key(model_of(result, "alpha"))["Issue-1"]["other_site"] == FLAG
    node = node_el(t, "Issue-1")
    assert "xsite" in t.elements[node]["classes"]
    assert FLAG in text_of(t, find(t, "title", within=node)[0])
    # The unflagged, unlinked leaf is in the schedule only.
    assert by_key(model_of(result, "alpha"))["Issue-2"]["other_site"] is None
    assert [el["attrs"].get("data-item") for el in t.elements if "xsite" in el["classes"]] == ["Issue-1"]


def test_unlinked_box_has_no_href(tmp_path):
    result = full_build(tmp_path, {
        "alpha": "- [ ] Lay {issue: Issue-1, depends_on: [ferry:Issue-1, beta:Issue-9]}\n",
        "beta": BETA,
    })
    t = tree(result.pages["alpha"])
    assert find(t, "a", ["fbox"]) == []
    boxes = find(t, "g", ["fbox", "fbox-none"])
    assert len(boxes) == 2
    assert [text_of(t, find(t, "text", ["ftitle"], within=i)[0]) for i in boxes] == [
        FBOX_LINE["absent"], FBOX_LINE["unresolved"]]
    assert page_links(result.pages["alpha"]) == []


FOREIGN_ONLY = {"alpha": "- [ ] Lay the deck {issue: Issue-1, depends_on: [beta:Issue-1]}\n", "beta": BETA}


def test_foreign_only_first_column_empty_network(tmp_path):
    result = full_build(tmp_path, FOREIGN_ONLY)
    m = model_of(result, "alpha")
    net = m["network"]
    assert (net["nodes"], net["length"], net["foreign_only"]) == ([], 0, ["Issue-1"])
    leaf = by_key(m)["Issue-1"]
    assert (leaf["es"], leaf["slack"], leaf["sclass"], leaf["linked"]) == (0, None, None, False)
    assert m["lanes"][0]["unlinked"] == 0
    page = result.pages["alpha"]
    t = tree(page)
    node = node_el(t, "Issue-1")
    (box,) = find(t, "rect", ["box"], within=node)
    assert float(t.elements[box]["attrs"]["x"]) == GUT + (COLW - BW) / 2
    assert "1 of the 1 open leaves have an open link" in page
    assert not any("unlinked" in text_of(t, i) for i in find(t, "text", ["lane-free"]))


def test_no_foreign_no_boxes(tmp_path):
    entries, raw, reasons = read_tree(tmp_path, FOREIGN_ONLY)
    resolved, _ = resolve_foreign(entries, raw, reasons)
    assert resolved["beta"].data.leaves[0].foreign_targets == ()
    as_resolved = worksite.render_page("beta", None, default_unit_hours=6, inputs=resolved["beta"])
    unresolved = worksite.render_page("beta", None, default_unit_hours=6, inputs=raw["beta"])
    assert as_resolved == unresolved
    assert 'class="fbox' not in as_resolved and 'id="arrow-foreign"' not in as_resolved


def test_held_out_flagged_not_drawn(tmp_path):
    result = full_build(tmp_path, {
        "alpha": ("- [ ] A {issue: Issue-1, depends_on: [Issue-2, beta:Issue-1]}\n"
                  "- [ ] B {issue: Issue-2, depends_on: [Issue-1]}\n"),
        "beta": BETA,
    })
    assert result.inputs["alpha"].data.leaves[0].waits_on_other_site is True
    t = tree(result.pages["alpha"])
    assert fboxes(t) == []
    assert not any("xsite" in el["classes"] for el in t.elements)


def test_foreign_only_anchor_once(tmp_path):
    page = full_build(tmp_path, FOREIGN_ONLY).pages["alpha"]
    assert page_ids(page).count("leaf-Issue-1") == 1


def test_boxes_stack_in_column(tmp_path):
    result = full_build(tmp_path, {
        "alpha": ("- [ ] Lane\n"
                  "  - [ ] A {issue: Issue-1, depends_on: [beta:Issue-1, ferry:Issue-2]}\n"
                  "  - [ ] B {issue: Issue-2, depends_on: [beta:Issue-9]}\n"),
        "beta": BETA,
    })
    t = tree(result.pages["alpha"])
    boxes = fboxes(t)
    assert box_texts(t) == ["beta:Issue-1", "ferry:Issue-2", "beta:Issue-9"]
    rects = [t.elements[find(t, "rect", ["fb"], within=i)[0]]["attrs"] for i in boxes]
    assert len({r["x"] for r in rects}) == 1
    ys = [float(r["y"]) for r in rects]
    assert ys == sorted(ys) and len(set(ys)) == 3
    leaf_bottoms = [float(t.elements[find(t, "rect", ["box"], within=node_el(t, k))[0]]["attrs"]["y"])
                    for k in ("Issue-1", "Issue-2")]
    assert max(leaf_bottoms) < min(ys)
    assert len(find(t, "path", ["fedge"])) >= 3
    assert check_fedges(result.pages["alpha"]) == 2


def test_no_chain_text_absent_when_foreign_only(tmp_path):
    page = full_build(tmp_path, FOREIGN_ONLY).pages["alpha"]
    assert "No open leaf waits on another" not in page
    assert NO_CHAIN not in page
    assert NO_CHAIN_XSITE in page
    for text in (SUMMARY_NO_CHAIN, LIMITS_CAPTION_NO_CHAIN):
        assert text.split(".")[1].strip() not in page


def css_rule(css, selector):
    m = re.search(re.escape(selector) + r" \{([^}]*)\}", css)
    assert m, selector
    return {d.split(":")[0].strip() for d in m.group(1).split(";") if d.strip()}


def test_xsite_css_resets():
    css = (ROOT / "gantry" / "templates" / "worksite.html").read_text(encoding="utf-8")
    # Colour and width only, so a held state keeps its dash.
    assert css_rule(css, ".node.xsite .outline") == {"stroke", "stroke-width"}
    # After the base-state rules it overrides, before the focus ring that must win over it.
    assert css.index(".node.st-parked .outline") < css.index(".node.xsite .outline") < css.index(
        ".node:focus-visible .outline")
    assert "{{FBOX_LEGEND}}" in css


def test_page_details_for_foreign_only(node, tmp_path):
    page = full_build(tmp_path / "b", FOREIGN_ONLY).pages["alpha"]
    path = tmp_path / "alpha.html"
    path.write_bytes(page.encode("utf-8"))
    with open_page(path) as live:
        live.drive("network-detail", "Issue-1")
        (detail,) = live.query("#detail")
        assert FLAG in detail.text and "beta:Issue-1: Pour the base (open on beta)" in detail.text
        assert SLACK_NONE in detail.text
        assert "Waits on: none" not in detail.text
        live.drive("schedule-detail", "Issue-1")
        (gdetail,) = live.query("#gdetail")
        assert FLAG in gdetail.text
        assert "no links: could move anywhere" not in gdetail.text
        (summary,) = live.query("#g-sum")
        assert "No open leaf waits on another" not in summary.text


def retitle(monkeypatch, site, key, title):
    real = build.read_site

    def read_site(entry, *args, **kw):
        found, reason = real(entry, *args, **kw)
        if entry.name == site and found is not None:
            found = dataclasses.replace(found, data=with_leaf(found.data, key, title=title))
        return found, reason

    monkeypatch.setattr(build, "read_site", read_site)


@pytest.mark.parametrize("h", HOSTILE)
def test_hostile_target_title(tmp_path, monkeypatch, h):
    retitle(monkeypatch, "beta", "Issue-1", h)
    page = full_build(tmp_path, FOREIGN_ONLY).pages["alpha"]
    assert_page_safe(page, [h])
    assert_no_external_resources(page)
    assert len(find(tree(page), "a", ["fbox"])) == 1


@pytest.mark.parametrize("h", HOSTILE)
def test_hostile_render_reason(tmp_path, monkeypatch, h):
    force_render(monkeypatch, lambda site, n: site == "beta", why=h)
    result = full_build(tmp_path, FOREIGN_ONLY)
    # The reason reaches the page inside the leaf's waits-on text.
    reason = NOT_BUILT.format(site="beta", reason=build.render_reason(ValueError(h), result.roots))
    planted = FOREIGN_UNRESOLVED.format(raw="beta:Issue-1", reason=reason)
    page = result.pages["alpha"]
    assert_page_safe(page, [planted])
    assert_no_external_resources(page)
    t = tree(page)
    assert find(t, "a", ["fbox"]) == [] and len(find(t, "g", ["fbox"])) == 1


# --- the samples (Task 5) -------------------------------------------------------------

# A site name in no sample config: the absent case. It is not a role constant,
# because every role constant names a configured project.
ABSENT_SITE = "ferry"
SAMPLES = ROOT / "samples"
# (site, node: key or title, entry): the ten cross-site cases.
CASES = [
    (UNESTIMATED, "Issue-9", f"{BUSY}:Issue-7"),  # open, and quarry has its own Issue-7
    (UNESTIMATED, "Issue-3", f"{BUSY}:Issue-3"),  # a finished target
    (UNESTIMATED, "Issue-7", f"{BUSY}:1.2"),  # an outline position
    (UNESTIMATED, "Issue-8", f"{UNESTIMATED}:Issue-9"),  # its own prefix: local
    (OWN_UNIT, "Irrigation", f"{SMALL}:Issue-4"),  # a row naming an open leaf
    (OWN_UNIT, "Irrigation", f"{ABSENT_SITE}:Issue-2"),  # a site not in the installation
    (OWN_UNIT, "Issue-5", f"{BUSY}:Issue-2"),  # a row target
    (OWN_UNIT, "Issue-6", f"{NO_WBS}:Issue-1"),  # no WBS.md
    (OWN_UNIT, "Stake the young trees", f"{UNREADABLE}:Issue-2"),  # not valid UTF-8
    (OWN_UNIT, "Mulch the beds", f"{SMALL}:Issue-40"),  # lacks the issue
]


def sample_node(site, name):
    data = parse_wbs((SAMPLES / site / "WBS.md").read_bytes().decode("utf-8"), site)
    (node,) = [n for n in (*data.leaves, *data.rows) if name in (n.key, n.title)]
    return node


def test_samples_carry_each_cross_site_case():
    assert ABSENT_SITE not in sample_projects()
    for site, name, entry in CASES:
        assert entry in meta_values(sample_node(site, name).meta.get("depends_on")), (site, name, entry)


# --- sample-level acceptance (Task 6) --------------------------------------------------


def outcome(result, site, raw):
    (o,) = [o for o in result.foreign[site] if o.raw == raw]
    return o


def test_sample_open_leaf_box(tmp_path):
    result = sample_build(tmp_path)
    o = outcome(result, UNESTIMATED, f"{BUSY}:Issue-7")
    assert (o.status, o.target_site, o.source_key) == ("open", BUSY, "Issue-9")
    assert o.title == leaf(result.inputs, BUSY, "Issue-7").title
    t = tree(result.pages[UNESTIMATED])
    assert [t.elements[i]["attrs"]["href"] for i in find(t, "a", ["fbox"])] == [f"{BUSY}.html#leaf-Issue-7"]
    assert by_key(model_of(result, UNESTIMATED))["Issue-9"]["other_site"] == FLAG


def test_sample_same_id_never_target(tmp_path):
    result = sample_build(tmp_path)
    o = outcome(result, UNESTIMATED, f"{BUSY}:Issue-7")
    assert o.title != leaf(result.inputs, UNESTIMATED, "Issue-7").title
    assert ("Issue-7", "Issue-9") not in result.inputs[UNESTIMATED].data.links
    assert not any(o.target_site == UNESTIMATED for o in result.foreign[UNESTIMATED])


def test_sample_finished_no_box(tmp_path):
    result = sample_build(tmp_path)
    o = outcome(result, UNESTIMATED, f"{BUSY}:Issue-3")
    assert (o.status, o.source_key, o.href) == ("satisfied", "Issue-3", None)
    x = leaf(result.inputs, UNESTIMATED, "Issue-3")
    assert (x.waits_on_other_site, x.foreign_targets) == (False, ())
    assert by_key(model_of(result, UNESTIMATED))["Issue-3"]["fboxes"] == []


def test_sample_row_target(tmp_path):
    result = sample_build(tmp_path)
    data = result.inputs[BUSY].data
    (row,) = [r for r in data.rows if r.issue == "Issue-2"]
    k = next(x.key for x in data.leaves if x.open and x.outline.startswith(row.outline + "."))
    o = outcome(result, OWN_UNIT, f"{BUSY}:Issue-2")
    assert (o.status, o.target_is_row, o.href) == ("open", True, f"{BUSY}.html#leaf-{k}")


def test_sample_row_source(tmp_path):
    result = sample_build(tmp_path)
    data = result.inputs[OWN_UNIT].data
    (row,) = [r for r in data.rows if r.title == "Irrigation"]
    under = [x for x in data.leaves if x.open and x.outline.startswith(row.outline + ".")]
    assert len(under) == 3
    for leaf in under:
        raws = {o.raw for o in leaf.foreign_targets}
        assert {f"{SMALL}:Issue-4", f"{ABSENT_SITE}:Issue-2"} <= raws, leaf.key
    absent = [e for e in data.report if e.kind == "foreign_site_absent"]
    assert [(e.line, e.message) for e in absent] == [(row.line, MESSAGES["foreign_site_absent"].format(
        line=row.line, entry=f"{ABSENT_SITE}:Issue-2"))]


EXPECTED_STATUS = Counter({"open": 3, "satisfied": 1, "absent": 1, "unresolved": 4})


def test_sample_every_entry_has_outcome(tmp_path):
    result = sample_build(tmp_path)
    expected = {(site, node.key, ref.raw) for site, found in result.inputs.items()
                for node in (*found.data.leaves, *found.data.rows)
                for ref in refs(node.meta, site) if ref.kind == "foreign"}
    recorded = [(o.site, o.source_key, o.raw) for outcomes in result.foreign.values() for o in outcomes]
    assert len(recorded) == len(set(recorded)) == 9
    assert set(recorded) == expected
    assert Counter(o.status for outcomes in result.foreign.values() for o in outcomes) == EXPECTED_STATUS
    assert list(result.foreign) == [s.name for s in result.sites if s.name in result.inputs]


def test_sample_absent_unresolved(tmp_path):
    result = sample_build(tmp_path)
    m = model_of(result, OWN_UNIT)
    cases = {
        f"{ABSENT_SITE}:Issue-2": ("absent", None),
        f"{SMALL}:Issue-40": ("unresolved", NO_ISSUE.format(site=SMALL, issue="Issue-40")),
        f"{NO_WBS}:Issue-1": ("unresolved", NOT_BUILT.format(site=NO_WBS, reason="no WBS.md in the planning directory")),
        f"{UNREADABLE}:Issue-2": ("unresolved", NOT_BUILT.format(site=UNREADABLE,
                                                                  reason="WBS.md is not valid UTF-8 at byte 88")),
    }
    report = result.inputs[OWN_UNIT].data.report
    for raw, (status, reason) in cases.items():
        o = outcome(result, OWN_UNIT, raw)
        assert (o.status, o.reason, o.href) == (status, reason, None), raw
        kind = "foreign_site_absent" if status == "absent" else "foreign_unresolved"
        assert any(e.kind == kind and e.line == o.source_line and repr(raw) in e.message for e in report), raw
        waiting = [x for x in result.inputs[OWN_UNIT].data.leaves if x.open and o in (x.foreign_targets or ())]
        assert waiting and all(x.waits_on_other_site for x in waiting), raw
        for x in waiting:
            boxes = [b for b in by_key(m)[x.key]["fboxes"] if f"{b[0]}:{b[1]}" == raw]
            assert [b[3] for b in boxes] == [None], (raw, x.key)


def test_sample_outline_unresolved(tmp_path):
    result = sample_build(tmp_path)
    o = outcome(result, UNESTIMATED, f"{BUSY}:1.2")
    assert (o.status, o.reason) == ("unresolved", NO_ISSUE.format(site=BUSY, issue="1.2"))
    data = result.inputs[BUSY].data
    assert any(n.outline == "1.2" for n in (*data.leaves, *data.rows))


def test_sample_own_prefix_local(tmp_path):
    result = sample_build(tmp_path)
    assert not any(o.raw.startswith(f"{UNESTIMATED}:") for o in result.foreign[UNESTIMATED])
    assert ("Issue-9", "Issue-8") in result.inputs[UNESTIMATED].data.links


def test_sample_render_failure(tmp_path, monkeypatch):
    force_render(monkeypatch, lambda site, n: site == BUSY)
    result = sample_build(tmp_path)
    naming = [o for outcomes in result.foreign.values() for o in outcomes if o.target_site == BUSY]
    assert len(naming) == 4
    reason = NOT_BUILT.format(site=BUSY, reason="render error: ValueError: forced")
    assert {(o.status, o.reason) for o in naming} == {("unresolved", reason)}
    assert outcome(result, UNESTIMATED, f"{BUSY}:Issue-3").status == "unresolved"
    assert not any(f"{BUSY}.html" in href for page in result.pages.values() for href in page_links(page))
    written, paths, error = write_all(result)
    assert error is None
    out = result.config.output_dir
    for path in paths:
        rel = path.relative_to(out).as_posix()
        assert all(lands(out, rel, href) for href in page_links(Path(path).read_text(encoding="utf-8"))), rel


def strip_foreign(copy):
    """Drop every foreign depends_on entry from the samples copy, in place:
    line numbers and keys stay, and an emptied list is written []."""
    for site in sample_projects():
        path = copy / site / "WBS.md"
        if not path.is_file():
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue

        def keep(m, site=site):
            entries = [e for e in meta_values("[" + m.group(1) + "]") if classify(e, site).kind != "foreign"]
            return "depends_on: [" + ", ".join(entries) + "]"

        path.write_bytes(re.sub(r"depends_on: \[([^\]]*)\]", keep, text).encode("utf-8"))


def stripped_build(tmp_path):
    copy = copy_samples(tmp_path)
    strip_foreign(copy)
    return build_all(load_config(copy / "gantry.sample.toml"), samples=copy)


def svg_kinds(t, skip):
    """(tag, classes) counts in the network figure; a lane's "+N unlinked"
    line is left out, as its count is checked lane by lane."""
    (net,) = find(t, "svg", ["net"])
    kinds = Counter()
    for i in find(t, within=net):
        if "lane-free" in t.elements[i]["classes"] and text_of(t, i).endswith(" unlinked"):
            continue
        if not skip(i):
            kinds[(t.elements[i]["tag"], tuple(c for c in t.elements[i]["classes"] if c != "xsite"))] += 1
    return kinds


def node_x(t):
    return {t.elements[i]["attrs"]["data-item"]: t.elements[find(t, "rect", ["box"], within=i)[0]]["attrs"]["x"]
            for i in find(t, "g", ["node"])}


def test_schedule_unchanged_without_foreign(tmp_path):
    a, b = sample_build(tmp_path / "a"), stripped_build(tmp_path / "b")
    assert sum(len(o) for o in a.foreign.values()) == 9 and sum(len(o) for o in b.foreign.values()) == 0
    assert set(a.pages) == set(b.pages)
    extras = 0
    for site in a.pages:
        ma, mb = model_of(a, site), model_of(b, site)
        assert ma["schedule"] == mb["schedule"], site
        assert ma["finish_day"] == mb["finish_day"], site
        # The figures, not the prose: a foreign-only site words its no-chain summary apart.
        assert re.findall(r"\d+", ma["summary"]) == re.findall(r"\d+", mb["summary"]), site
        assert (ma["totals"], ma["unestimated"]) == (mb["totals"], mb["unestimated"]), site
        only = ma["network"].pop("foreign_only")
        assert mb["network"].pop("foreign_only") == []
        assert ma["network"] == mb["network"], site
        for la, lb in zip(ma["lanes"], mb["lanes"], strict=True):
            mine = sum(by_key(ma)[k]["lane"] == ma["lanes"].index(la) for k in only)
            assert la["unlinked"] == lb["unlinked"] - mine, (site, la["key"])
        ta, tb = tree(a.pages[site]), tree(b.pages[site])
        xa, xb = node_x(ta), node_x(tb)
        assert {k: xa[k] for k in xb} == xb, site
        first = GUT + (COLW - BW) / 2
        assert set(xa) - set(xb) == set(only), site
        assert all(float(xa[k]) == first for k in only), site
        extras += len(only)

        def skip(i, ta=ta, only=set(only), grew=bool(only) and not mb["network"]["length"]):
            chain = [i, *ancestors(ta, i)]
            for j in chain:
                el = ta.elements[j]
                if el["tag"] == "g" and "node" in el["classes"] and el["attrs"].get("data-item") in only:
                    return True
                if "fbox" in el["classes"] or "fedge" in el["classes"] or el["attrs"].get("id") == "arrow-foreign":
                    return True
            el = ta.elements[i]
            # A network with no column of its own gains column 1 for its foreign-only leaves.
            return grew and ({"col-num", "col-rule"} & set(el["classes"]) != set())

        assert svg_kinds(ta, skip) == svg_kinds(tb, lambda i: False), site
    assert extras > 0


HREF_RE = re.compile(r"[A-Za-z0-9_.~%-]+\.html#leaf-[A-Za-z0-9_.~%-]+")


def test_sample_hrefs_form_and_land(tmp_path, monkeypatch):
    code, out, _, err = demo_build(tmp_path, monkeypatch)
    assert code == 0, err
    hrefs = 0
    for page in sorted((out / "sites").glob("*.html")):
        rel = f"sites/{page.name}"
        for href in page_links(page.read_text(encoding="utf-8")):
            assert HREF_RE.fullmatch(href), (rel, href)
            assert lands(out, rel, href), (rel, href)
            hrefs += 1
    assert hrefs == 5


# --- the docs (Task 7) ----------------------------------------------------------------


def test_doc_pins():
    doc = (ROOT / "docs" / "worksite-page.md").read_bytes().decode("utf-8")
    for text in (*MESSAGES.values(), NOT_BUILT, NO_ISSUE):
        assert f"`{text}`" in doc, text
    for kind in MESSAGES:
        assert re.search(rf"^\| `{kind}` \|", doc, re.M), kind
    for stale in ("does not resolve another site's leaves yet", "stays unresolved and unreported",
                  "for a later cross-site view"):
        assert stale not in doc, stale
    assert "check_portfolio" in doc


# --- final-review fixes ----------------------------------------------------------------

POINTS_RE = re.compile(r"[ML](-?[0-9.]+),(-?[0-9.]+)")


def node_rects(t):
    out = {}
    for i in find(t, "g", ["node"]):
        a = t.elements[find(t, "rect", ["box"], within=i)[0]]["attrs"]
        out[t.elements[i]["attrs"]["data-item"]] = (float(a["x"]), float(a["y"]), float(a["width"]),
                                                      float(a["height"]))
    return out


def crosses(seg, rect):
    (x1, y1), (x2, y2) = seg
    rx, ry, w, h = rect
    lo_x, hi_x, lo_y, hi_y = min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2)
    return lo_x < rx + w and hi_x > rx and lo_y < ry + h and hi_y > ry


def check_fedges(page):
    """Each box's link (full form) keeps clear of every leaf box and ends at its own leaf's left edge;
    the leaves waiting in one lane and column each have a line of their own."""
    t = tree(page)
    rects = node_rects(t)
    lines = {}
    for i in find(t, "path", ["fedge"]):
        if "e-wave" in t.elements[i]["classes"]:
            continue
        attrs = t.elements[i]["attrs"]
        own = attrs["data-v"]
        pts = [tuple(map(float, m)) for m in POINTS_RE.findall(attrs["d"])]
        segs = list(zip(pts, pts[1:]))
        assert all(a[0] == b[0] or a[1] == b[1] for a, b in segs), attrs["d"]
        for seg in segs:
            assert not any(crosses(seg, r) for r in rects.values()), (own, attrs["d"])
        rx, ry, _, h = rects[own]
        end = pts[-1]
        assert end[0] == rx - 2 and ry < end[1] < ry + h, (own, attrs["d"])
        lines.setdefault(own, []).extend((a[0], min(a[1], b[1]), max(a[1], b[1])) for a, b in segs if a[0] == b[0])
    # Two leaves' lines never run along each other: one x, overlapping spans.
    for own, mine in lines.items():
        for other, theirs in lines.items():
            if own != other:
                for x, lo, hi in mine:
                    assert not any(x == x2 and lo < hi2 and lo2 < hi for x2, lo2, hi2 in theirs), (own, other)
    return len(lines)


def test_fedges_keep_clear_of_other_leaves(tmp_path):
    result = sample_build(tmp_path)
    assert check_fedges(result.pages[OWN_UNIT]) == 5
    assert check_fedges(result.pages[UNESTIMATED]) == 2
    t = tree(result.pages[OWN_UNIT])
    titles = [text_of(t, find(t, "title", within=i)[0]) for i in fboxes(t)]
    keys = {x.key for x in result.inputs[OWN_UNIT].data.leaves}
    assert all(title.split(" waits on ")[0] in keys for title in titles), titles


def test_xsite_focus_ring_wins():
    css = (ROOT / "gantry" / "templates" / "worksite.html").read_text(encoding="utf-8")
    # The ring is as specific as the one xsite rule and after it; no xsite
    # selector is more specific.
    assert css.index(".node.xsite .outline") < css.index(".node:focus-visible .outline")
    assert {"stroke", "stroke-width"} <= css_rule(css, ".node:focus-visible .outline")
    assert not re.search(r"\.node\.xsite[.:]", css)
