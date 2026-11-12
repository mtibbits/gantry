"""Cross-site depends_on resolution (#68): gantry/crosssite.py and its page."""

import copy

import pytest

from gantry import build
from gantry.build import CLASH, build_all, read_sites, site_list
from gantry.config import load_config
from gantry.crosssite import MESSAGES, NO_ISSUE, NOT_BUILT, resolve_foreign
from gantry.model import REPORT_KINDS, report_order
from gantry.sites import config_roots
from gantry.wbs_stopgap import parse_wbs
from gantry.views import worksite
from gantry.views.worksite import FOREIGN
from helpers_config import page_links, run_main, write_site_tree


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


def force_render(monkeypatch, fail):
    """Make render_model raise for a site while `fail(site, nth render of it)` is true;
    returns the {site: render_site calls} counter."""
    calls = {}
    real_site, real_model = build.render_site, worksite.render_model

    def render_site(name, *args, **kw):
        calls[name] = calls.get(name, 0) + 1
        return real_site(name, *args, **kw)

    def render_model(m, **kw):
        if fail(m["site"], calls[m["site"]]):
            raise ValueError("forced")
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
