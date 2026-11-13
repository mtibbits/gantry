"""gantry/trace.py: `satisfies:` read from parsed leaves, the catalog and the
per-site trace (#19). Every fixture is an inline string; no WBS.md is read
except through parse_wbs in memory."""

import re
import time
from dataclasses import fields
from pathlib import Path

import pytest

from gantry import requirements
from gantry.config import load_config
from gantry.model import FINISHED_GLYPHS, STATE_GLYPHS, Leaf
from gantry.requirements import ReportEntry, ReqId
from gantry.sites import config_roots, load_sites
from gantry.trace import (FIELDS, LEAF_SOURCE, MESSAGES, TRACE_KINDS, UNSERVED_SCOPE, WHY, Catalog, Serving,
                          SiteTrace, TraceReport, UseCase, build_site_trace, load_catalog, satisfies_ids)
from gantry.wbs_stopgap import parse_wbs
from helpers_config import write_site_tree
from sample_roles import BUSY, SMALL
from test_build import heading_slugs
from test_samples import GANTRY_SAMPLE, md_tables


def leaf(key, line, glyph=" ", **meta):
    """A parsed leaf; `meta` values are raw texts as the stopgap stores them."""
    return Leaf(key, key, str(line), key, glyph, STATE_GLYPHS[glyph], glyph not in FINISHED_GLYPHS, None, (),
                {"issue": key, **meta}, None, line)


def tree(tmp_path, sites, portfolio=None, br=None, tail="", **raw):
    """An installation in tmp_path: `sites` maps a site name to its requirements
    text (written to <name>.md beside the config), or None for a site with no
    requirements key. `portfolio` is the portfolio file's BR-section text."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    tables = []
    for name, text in sites.items():
        if text is not None:
            (tmp_path / f"{name}.md").write_bytes(text.encode("utf-8"))
            tables.append(f'[site.{name}]\nrequirements = "{name}.md"\n')
    if br is not None:
        tables.append(f'[requirements]\nbr_namespace = "{br}"\n')
    if portfolio is not None:
        (tmp_path / "portfolio.md").write_bytes(("# P\n\n## Business requirements\n\n" + portfolio).encode("utf-8"))
        raw.setdefault("portfolio_file", '"portfolio.md"')
    return write_site_tree(tmp_path, {name: b"# x\n" for name in sites}, tail="".join(tables) + tail, **raw)


def catalog(config_path) -> Catalog:
    c = load_config(config_path)
    return load_catalog(config_roots(c), c, load_sites(c)[0])


def message(kind, **fields_):
    return MESSAGES[kind].format(**fields_)


def entries(report_field):
    return [(e.kind, e.source, e.line) for e in report_field]


def rid(text):
    return requirements.parse_id(text)


# --- Task 1: the model and satisfies_ids ------------------------------------------


def test_model_field_lists():
    assert [f.name for f in fields(TraceReport)] == [
        "unreadable", "malformed", "duplicate", "misplaced", "cycle", "undefined", "unserved", "unserved_scope",
        "untraced"]
    assert [f.name for f in fields(SiteTrace)] == ["site", "in_trace", "leaf_ids", "serving", "use_cases", "report"]
    assert [f.name for f in fields(Serving)] == ["leaf", "how"]
    assert UNSERVED_SCOPE == "site"
    assert LEAF_SOURCE == "WBS.md"


def test_fields_partition_kinds():
    assert list(FIELDS) == ["unreadable", "malformed", "duplicate", "misplaced", "cycle", "undefined"]
    kinds = [kind for held in FIELDS.values() for kind in held]
    assert len(kinds) == len(set(kinds))
    assert set(kinds) == set(requirements.REPORT_KINDS) | set(TRACE_KINDS)
    assert set(TRACE_KINDS) <= set(MESSAGES)


NS = frozenset({"gantry", "life"})


@pytest.mark.parametrize("raw, ids", [
    ("[gantry:FR-1, gantry:QA-2]", ["gantry:FR-1", "gantry:QA-2"]),
    ("gantry:FR-1", ["gantry:FR-1"]),
    ('"gantry:FR-1"', ["gantry:FR-1"]),
    ("[\"gantry:FR-1\", 'gantry:QA-2']", ["gantry:FR-1", "gantry:QA-2"]),
])
def test_satisfies_forms_agree(raw, ids):
    got, problems = satisfies_ids(leaf("L1", 2, satisfies=raw), NS)
    assert problems == []
    assert got == [rid(i) for i in ids]


def test_satisfies_malformed_reported_and_ignored():
    got, problems = satisfies_ids(leaf("L1", 3, satisfies="[FR-1, gantry:FR-01, a:b:FR-1, gantry:FR-2]"), NS)
    assert got == [rid("gantry:FR-2")]
    assert entries(problems) == [("malformed_satisfies", LEAF_SOURCE, 3)] * 3
    assert [p.message for p in problems] == [message("malformed_satisfies", entry=e)
                                             for e in ("FR-1", "gantry:FR-01", "a:b:FR-1")]


def test_satisfies_unbracketed_list_reported():
    got, problems = satisfies_ids(leaf("L1", 4, satisfies="gantry:FR-1", gantry="QA-2"), NS)
    assert got == [rid("gantry:FR-1")]
    assert entries(problems) == [("unbracketed_list", LEAF_SOURCE, 4)]
    assert problems[0].message == message("unbracketed_list", key="gantry")
    # Controls: the key is no known namespace; no satisfies key at all.
    assert satisfies_ids(leaf("L1", 4, satisfies="gantry:FR-1", gantry="QA-2"), frozenset({"life"})) == (
        [rid("gantry:FR-1")], [])
    assert satisfies_ids(leaf("L2", 5, gantry="QA-2"), NS) == ([], [])


def test_satisfies_from_stopgap_meta():
    data = parse_wbs("- [ ] a {issue: Issue-7, satisfies: a:FR-1, a:QA-2}\n"
                     "- [ ] b {issue: Issue-8, satisfies: [a:FR-1, a:QA-2]}\n", "a")
    first, second = data.leaves
    ids, problems = satisfies_ids(first, frozenset({"a"}))
    assert ids == [rid("a:FR-1")]
    assert [(p.kind, p.message) for p in problems] == [("unbracketed_list", message("unbracketed_list", key="a"))]
    assert satisfies_ids(second, frozenset({"a"})) == ([rid("a:FR-1"), rid("a:QA-2")], [])


def test_satisfies_absent_empty_and_repeat():
    assert satisfies_ids(leaf("L1", 2), NS) == ([], [])
    assert satisfies_ids(leaf("L1", 2, satisfies="[]"), NS) == ([], [])
    assert satisfies_ids(leaf("L1", 2, satisfies="[a:FR-1, a:FR-1]"), NS) == ([rid("a:FR-1")], [])
    ids, problems = satisfies_ids(leaf("L1", 2, satisfies="[open"), NS)
    assert ids == []
    assert [(p.kind, p.message) for p in problems] == [
        ("malformed_satisfies", message("malformed_satisfies", entry="open"))]


# --- Task 2: the catalog -----------------------------------------------------------


def test_catalog_reads_in_trace_sites_and_brs(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n", "beta": "- SR-1: b\n", "gamma": None},
                       portfolio="- BR-1: one\n"))
    assert list(cat.sets) == ["alpha", "beta", "life"]
    assert "alpha:FR-1" in cat.sets["alpha"].requirements
    assert "beta:SR-1" in cat.sets["beta"].requirements
    assert list(cat.sets["life"].requirements) == ["life:BR-1"]
    assert cat.br_namespace == "life"
    assert cat.namespaces == frozenset({"alpha", "beta", "gamma", "life"})
    assert cat.portfolio_set
    assert cat.portfolio_report == ()
    assert cat.link_report == {}
    assert cat.in_trace("alpha") and cat.in_trace("beta")
    assert not cat.in_trace("gamma") and not cat.in_trace("life")


def test_catalog_br_namespace_from_config(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n"}, portfolio="- BR-1: one\n", br="ops"))
    assert list(cat.sets) == ["alpha", "ops"]
    assert cat.namespaces == frozenset({"alpha", "ops"})


def test_catalog_without_portfolio_file(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n"}))
    assert "life" not in cat.sets
    assert not cat.portfolio_set
    assert cat.lookup(ReqId("life", "BR", 1)) == (None, WHY["no_portfolio"])


def test_catalog_unreadable_requirements_file(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": None}, tail='[site.alpha]\nrequirements = "missing.md"\n'))
    assert cat.sets["alpha"].requirements == {}
    assert [e.kind for e in cat.sets["alpha"].report] == ["unreadable"]
    assert cat.in_trace("alpha")
    assert cat.lookup(rid("alpha:FR-1")) == (None, WHY["unreadable"].format(ns="alpha"))


def test_catalog_portfolio_report_is_br_only(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n"}, portfolio="- BR-1: a\n- BR-1: b\n"))
    assert [(e.kind, e.section) for e in cat.portfolio_report] == [("duplicate_id", "business requirements")]
    assert cat.lookup(rid("life:BR-2")) == (None, WHY["not_in_portfolio"].format(rid="life:BR-2"))


def test_catalog_portfolio_unreadable(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n"}, portfolio_file='"nope.md"'))
    assert [(e.kind, e.section) for e in cat.portfolio_report] == [("portfolio_unreadable", None)]
    assert cat.portfolio_set
    assert cat.sets["life"].requirements == {}
    assert cat.lookup(rid("life:BR-1")) == (None, WHY["portfolio_unreadable"])


def test_catalog_br_section_hidden_by_a_comment(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "hidden.md").write_bytes(b"# P\n\n<!-- note\n\n## Business requirements\n\n- BR-1: x\n")
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n"}, portfolio_file='"hidden.md"'))
    assert "malformed_comment" in [e.kind for e in cat.portfolio_report]
    assert cat.lookup(rid("life:BR-1")) == (
        None, "the portfolio file has no '## Business requirements' section outside a fence or comment")


def test_catalog_portfolio_without_br_section(tmp_path):
    # A mis-cased heading: the BR-1 line is there, but no section is read.
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "nobr.md").write_bytes(b"# P\n\n## Business Requirements\n\n- BR-1: x\n")
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n"}, portfolio_file='"nobr.md"'))
    assert [e.kind for e in cat.portfolio_report] == ["no_br_section"]
    assert cat.lookup(rid("life:BR-1")) == (None, WHY["no_br_section"])


def test_lookup_reasons(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n", "beta": "- SR-1: b\n", "gamma": None},
                       portfolio="- BR-1: one\n"))
    found, why = cat.lookup(rid("alpha:FR-1"))
    assert (found.id, why) == (rid("alpha:FR-1"), None)
    assert cat.lookup(rid("life:BR-1"))[0].layer == "BR"
    assert cat.lookup(rid("alpha:FR-9")) == (None, WHY["not_in_file"].format(ns="alpha", rid="alpha:FR-9"))
    assert cat.lookup(rid("gamma:FR-1")) == (None, WHY["no_file"].format(ns="gamma"))
    assert cat.lookup(rid("zeta:FR-1")) == (None, WHY["not_a_site"].format(ns="zeta"))
    assert cat.lookup(rid("life:BR-9")) == (None, WHY["not_in_portfolio"].format(rid="life:BR-9"))


def test_sample_catalog_resolves_br_id():
    cat = catalog(GANTRY_SAMPLE)
    assert list(cat.sets) == [SMALL, "life"]
    assert list(cat.sets["life"].requirements) == ["life:BR-1", "life:BR-2", "life:BR-3"]
    assert cat.portfolio_report == ()
    assert cat.lookup(ReqId("life", "BR", 2))[0].layer == "BR"


# --- Task 3: cross-namespace links and cross-file cycles ----------------------------


def source_of(cat, key):
    return cat.lookup(rid(key))[0].source


def test_cross_namespace_link_undefined(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a {refines: ferry:SR-2}\n- UC-1: u {needs: [zeta:FR-1]}\n",
                                  "ferry": "- SR-1: s\n"}))
    src = source_of(cat, "alpha:FR-1")
    assert list(cat.link_report) == ["alpha"]
    assert cat.link_report["alpha"] == (
        ReportEntry(src, 1, "undefined_cross_link",
                    message("undefined_cross_link", key="refines", rid="ferry:SR-2",
                            why=WHY["not_in_file"].format(ns="ferry", rid="ferry:SR-2"))),
        ReportEntry(src, 2, "undefined_cross_link",
                    message("undefined_cross_link", key="needs", rid="zeta:FR-1", why=WHY["not_a_site"].format(ns="zeta"))),
    )
    # The reader reports no own-namespace miss for either link.
    assert cat.sets["alpha"].report == []


def test_cross_namespace_link_defined_not_reported(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a {refines: ferry:SR-1}\n", "ferry": "- SR-1: s\n"}))
    assert cat.link_report == {}


def test_cross_file_refines_cycle_reported_once(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a {refines: beta:SR-1}\n",
                                  "beta": "- SR-1: b {refines: alpha:FR-1}\n"}))
    assert cat.link_report == {"alpha": (
        ReportEntry(source_of(cat, "alpha:FR-1"), 1, "refines_cycle",
                    "cross-file refines cycle: alpha:FR-1 -> beta:SR-1 -> alpha:FR-1"),)}


def test_cross_file_cycle_headed_by_first_in_catalog_order(tmp_path):
    # beta is listed first, so the cycle is written from beta's member, though
    # alpha's member has the earlier line (the reader's rule would pick it).
    cat = catalog(tree(tmp_path, {"beta": "- SR-1: x\n- SR-2: b {refines: alpha:FR-1}\n",
                                  "alpha": "- FR-1: a {refines: beta:SR-2}\n"}))
    assert [(e.kind, e.source, e.line, e.message) for e in cat.link_report["beta"]] == [
        ("refines_cycle", source_of(cat, "beta:SR-2"), 2,
         message("refines_cycle", chain="beta:SR-2 -> alpha:FR-1 -> beta:SR-2"))]
    assert list(cat.link_report) == ["beta"]


def test_no_cross_file_cycle_through_a_business_requirement(tmp_path):
    # A project file cannot link into br_namespace (the reader's misplaced), so
    # a cycle through a business requirement never forms across files.
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a {refines: life:BR-1}\n"},
                       portfolio="- BR-1: x {refines: alpha:FR-1}\n"))
    assert [e.kind for e in cat.sets["alpha"].report] == ["misplaced"]
    assert cat.link_report == {}


def test_within_file_cycle_not_double_reported(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a {refines: FR-2}\n- FR-2: b {refines: FR-1}\n"}))
    assert [e.kind for e in cat.sets["alpha"].report] == ["refines_cycle"]
    assert cat.link_report == {}


def test_br_links_stay_on_the_catalog(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n"}, portfolio="- BR-1: x {refines: alpha:FR-9}\n"))
    assert cat.link_report == {"life": (
        ReportEntry("the portfolio file", 5, "undefined_cross_link",
                    message("undefined_cross_link", key="refines", rid="alpha:FR-9",
                            why=WHY["not_in_file"].format(ns="alpha", rid="alpha:FR-9"))),)}


# --- Task 4: build_site_trace --------------------------------------------------------


def undefined_why(report_field):
    return [e.message for e in report_field]


def test_undefined_leaf_id_reasons(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n", "gamma": None}, portfolio="- BR-1: b\n"))
    leaves = [leaf("L1", 2, satisfies="alpha:FR-9"), leaf("L2", 3, satisfies="gamma:FR-1"),
              leaf("L3", 4, satisfies="zeta:FR-1"), leaf("L4", 5, satisfies="life:BR-9"),
              leaf("L5", 6, satisfies="[alpha:FR-1, life:BR-1]")]
    t = build_site_trace("alpha", leaves, cat)
    assert t.site == "alpha" and t.in_trace
    assert t.leaf_ids == {"L5": (rid("alpha:FR-1"), rid("life:BR-1"))}
    assert entries(t.report.undefined) == [("undefined_id", LEAF_SOURCE, line) for line in (2, 3, 4, 5)]
    assert undefined_why(t.report.undefined) == [
        message("undefined_id", rid="alpha:FR-9", why=WHY["not_in_file"].format(ns="alpha", rid="alpha:FR-9")),
        message("undefined_id", rid="gamma:FR-1", why=WHY["no_file"].format(ns="gamma")),
        message("undefined_id", rid="zeta:FR-1", why=WHY["not_a_site"].format(ns="zeta")),
        message("undefined_id", rid="life:BR-9", why=WHY["not_in_portfolio"].format(rid="life:BR-9")),
    ]


def test_undefined_br_id_without_portfolio_file(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n"}))
    t = build_site_trace("alpha", [leaf("L1", 2, satisfies="life:BR-1")], cat)
    assert undefined_why(t.report.undefined) == [message("undefined_id", rid="life:BR-1", why=WHY["no_portfolio"])]
    assert t.leaf_ids == {}


def test_sample_busy_leaf_resolves_br_id():
    cat = catalog(GANTRY_SAMPLE)
    t = build_site_trace(BUSY, [leaf("Issue-1", 2, satisfies="life:BR-2")], cat)
    assert t.leaf_ids == {"Issue-1": (ReqId("life", "BR", 2),)}
    assert t.report.undefined == ()
    assert not t.in_trace


def test_untraced_open_leaves(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n"}))
    leaves = [leaf("L1", 2), leaf("L2", 3, satisfies="[]"), leaf("L3", 4, satisfies="alpha:FR-9"),
              leaf("L4", 5, satisfies="FR-1"), leaf("L5", 6, satisfies="[FR-1, alpha:FR-1]"),
              leaf("L6", 7, "x"), leaf("L7", 8, "-")]
    t = build_site_trace("alpha", leaves, cat)
    assert t.report.untraced == ("L1", "L2", "L3", "L4")
    assert entries(t.report.malformed) == [("malformed_satisfies", LEAF_SOURCE, 5),
                                           ("malformed_satisfies", LEAF_SOURCE, 6)]
    assert entries(t.report.undefined) == [("undefined_id", LEAF_SOURCE, 4)]
    assert t.leaf_ids == {"L5": (rid("alpha:FR-1"),)}


READER_KINDS_TEXT = ("- FR12: m\n- FR-1: a {owner: x}\n- FR-2: b {refines: nope}\n- BR-1: c\n- FR-1: d\n"
                     "- FR-3: e {refines: SR-9}\n- FR-4: f {refines: FR-4}\n<!--\n")


def test_report_fields_carry_reader_kinds(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": READER_KINDS_TEXT, "beta": None},
                       tail='[site.beta]\nrequirements = "missing.md"\n'))
    own = cat.sets["alpha"].report
    assert {e.kind for e in own} == set(requirements.REPORT_KINDS) - {"unreadable"}
    report = build_site_trace("alpha", [], cat).report
    for e in own:
        field = next(name for name, kinds in FIELDS.items() if e.kind in kinds)
        assert any(x is e for x in getattr(report, field)), e
    assert sum(len(getattr(report, name)) for name in FIELDS) == len(own)
    assert report.unreadable == ()
    beta = build_site_trace("beta", [], cat).report
    assert len(beta.unreadable) == 1 and beta.unreadable[0] is cat.sets["beta"].report[0]


def test_cross_file_entries_reach_their_site(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a {refines: beta:SR-1}\n- FR-2: b {refines: zeta:SR-1}\n",
                                  "beta": "- SR-1: b {refines: alpha:FR-1}\n"}))
    alpha = build_site_trace("alpha", [], cat).report
    assert [e.kind for e in alpha.cycle] == ["refines_cycle"]
    assert [e.kind for e in alpha.undefined] == ["undefined_cross_link"]
    beta = build_site_trace("beta", [], cat).report
    assert beta.cycle == () and beta.undefined == ()


def test_br_links_not_in_any_site_report(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n"}, portfolio="- BR-1: x {refines: alpha:FR-9}\n"))
    assert build_site_trace("alpha", [], cat).report.undefined == ()


def test_unreadable_site_reports_one_cause(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": None}, tail='[site.alpha]\nrequirements = "missing.md"\n'))
    t = build_site_trace("alpha", [leaf("L1", 2, satisfies="alpha:FR-1")], cat)
    assert [e.kind for e in t.report.unreadable] == ["unreadable"]
    assert undefined_why(t.report.undefined) == [
        message("undefined_id", rid="alpha:FR-1", why=WHY["unreadable"].format(ns="alpha"))]
    assert t.report.unserved == ()
    assert t.report.untraced == ("L1",)


def test_not_in_trace_site(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n", "gamma": None}))
    t = build_site_trace("gamma", [leaf("L1", 2, satisfies="alpha:FR-1"), leaf("L2", 3, satisfies="FR-1")], cat)
    assert t.in_trace is False
    assert t.leaf_ids == {"L1": (rid("alpha:FR-1"),)}
    assert t.report.untraced is None
    assert t.report.unserved is None
    assert entries(t.report.malformed) == [("malformed_satisfies", LEAF_SOURCE, 3)]
    assert t.use_cases == {}


def test_site_with_nothing(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n", "gamma": None}))
    empty = TraceReport((), (), (), (), (), (), None, "site", None)
    t = build_site_trace("gamma", [leaf("L1", 2), leaf("L2", 3)], cat)
    assert t.leaf_ids == {} and t.serving == {} and t.use_cases == {}
    assert t.report == empty
    assert build_site_trace("gamma", [], cat).report == empty


def test_unserved_scope_is_site(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n", "gamma": None}))
    assert build_site_trace("alpha", [], cat).report.unserved_scope == "site"
    assert build_site_trace("gamma", [], cat).report.unserved_scope == "site"
    assert UNSERVED_SCOPE == "site"


HOSTILE = ['"', "'", "[", "]", "[,]", ":", "a:", ":FR-1", "life:BR-1" * 50, "alpha:FR-" + "9" * 5000, "\x00"]


def test_hostile_values_never_raise(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n"}, portfolio="- BR-1: b\n"))
    leaves = [leaf(f"L{i}", i + 2, satisfies=raw, alpha="x", life="y") for i, raw in enumerate(HOSTILE)]
    report = build_site_trace("alpha", leaves, cat).report
    problems = [e for name in FIELDS for e in getattr(report, name)]
    assert problems
    assert {e.kind for e in problems} <= set(TRACE_KINDS)
    assert sum(e.kind == "unbracketed_list" for e in problems) == 2 * len(HOSTILE)


# --- Task 5: serving -----------------------------------------------------------------

SERVING_REQ = """\
- UC-1: u1 {needs: [FR-1, QA-1]}
- UC-2: u2 {needs: [SR-2]}
- SR-1: s1
- SR-2: s2
- FR-1: f1 {refines: SR-1}
- FR-2: f2 {refines: SR-2}
- FR-3: f3
- QA-1: q1
- CON-1: c1
- EI-1: e1
"""

SERVING_LEAVES = [
    leaf("L1", 2, satisfies="alpha:FR-1"),
    leaf("L2", 3, "x", satisfies="alpha:FR-2"),
    leaf("L3", 4, "-", satisfies="alpha:FR-3"),
    leaf("L4", 5, satisfies="[alpha:SR-1, alpha:FR-1]"),
    leaf("L5", 6, satisfies="alpha:CON-1"),
    leaf("L6", 7),
    leaf("L7", 8, satisfies="[]"),
    leaf("L8", 9, satisfies="zeta:FR-1"),
]

D, I = "direct", "indirect"


def serving_trace(tmp_path, leaves=SERVING_LEAVES, text=SERVING_REQ):
    return build_site_trace("alpha", leaves, catalog(tree(tmp_path, {"alpha": text})))


def test_serving_table(tmp_path):
    t = serving_trace(tmp_path)
    assert t.serving == {
        "alpha:UC-1": (Serving("L1", I), Serving("L4", I)),
        "alpha:UC-2": (Serving("L2", I),),
        "alpha:SR-1": (Serving("L1", I), Serving("L4", D)),
        "alpha:SR-2": (Serving("L2", I),),
        "alpha:FR-1": (Serving("L1", D), Serving("L4", D)),
        "alpha:FR-2": (Serving("L2", D),),
        "alpha:CON-1": (Serving("L5", D),),
    }
    assert list(t.serving) == ["alpha:UC-1", "alpha:UC-2", "alpha:SR-1", "alpha:SR-2", "alpha:FR-1", "alpha:FR-2",
                               "alpha:CON-1"]
    assert t.report.unserved == ("alpha:FR-3", "alpha:QA-1", "alpha:EI-1")
    assert t.report.untraced == ("L6", "L7", "L8")
    assert list(t.leaf_ids) == ["L1", "L2", "L3", "L4", "L5"]
    assert t.use_cases == {
        "alpha:UC-1": UseCase((rid("alpha:FR-1"), rid("alpha:QA-1")), t.serving["alpha:UC-1"]),
        "alpha:UC-2": UseCase((rid("alpha:SR-2"),), t.serving["alpha:UC-2"]),
    }


def test_direct_wins(tmp_path):
    t = serving_trace(tmp_path)
    assert t.serving["alpha:SR-1"] == (Serving("L1", I), Serving("L4", D))
    only = serving_trace(tmp_path / "b", [leaf("S1", 2, satisfies="alpha:SR-1")])
    assert only.serving == {"alpha:SR-1": (Serving("S1", D),)}


def test_use_case_serving_via_needs_and_refinement(tmp_path):
    t = serving_trace(tmp_path)
    assert t.serving["alpha:UC-2"] == (Serving("L2", I),)  # FR-2 -> SR-2 -> UC-2
    up = serving_trace(tmp_path / "b", [leaf("U1", 2, satisfies="alpha:UC-1")])
    assert up.serving == {"alpha:UC-1": (Serving("U1", D),)}  # flows go up, never down


def test_skipped_never_serves_done_serves(tmp_path):
    t = serving_trace(tmp_path)
    assert "L3" in t.leaf_ids
    assert all(s.leaf != "L3" for held in t.serving.values() for s in held)
    assert "L3" not in t.report.untraced
    assert t.serving["alpha:FR-2"] == (Serving("L2", D),)


def test_con_never_unserved(tmp_path):
    t = serving_trace(tmp_path, [x for x in SERVING_LEAVES if x.key != "L5"])
    assert "alpha:CON-1" not in t.serving
    assert "alpha:CON-1" not in t.report.unserved
    every = SERVING_LEAVES + [leaf("M1", 10, satisfies="[alpha:FR-3, alpha:QA-1, alpha:EI-1]")]
    every = [x for x in every if x.key != "L5"]
    assert serving_trace(tmp_path / "b", every).report.unserved == ()


def test_serving_across_namespaces(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a {refines: beta:SR-1}\n",
                                  "beta": "- SR-1: b\n- UC-1: u {needs: [alpha:FR-1]}\n"}))
    t = build_site_trace("alpha", [leaf("L1", 2, satisfies="alpha:FR-1")], cat)
    assert t.serving == {"alpha:FR-1": (Serving("L1", D),), "beta:SR-1": (Serving("L1", I),),
                         "beta:UC-1": (Serving("L1", I),)}
    assert t.report.unserved == ()
    assert build_site_trace("beta", [], cat).report.unserved == ("beta:SR-1", "beta:UC-1")  # site scope; #21


def test_cycle_does_not_loop_serving(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a {refines: beta:SR-1}\n",
                                  "beta": "- SR-1: b {refines: alpha:FR-1}\n"}))
    t = build_site_trace("alpha", [leaf("L1", 2, satisfies="alpha:FR-1")], cat)
    assert t.serving == {"alpha:FR-1": (Serving("L1", D),), "beta:SR-1": (Serving("L1", I),)}
    assert [e.kind for e in t.report.cycle] == ["refines_cycle"]


def chain_text(n_fr, n_uc, closed):
    lines = [f"- FR-{i}: f {{refines: FR-{i + 1}}}" for i in range(1, n_fr)]
    lines.append(f"- FR-{n_fr}: f {{refines: FR-1}}" if closed else f"- FR-{n_fr}: f")
    lines += [f"- UC-{i}: u {{needs: [FR-{n_fr}]}}" for i in range(1, n_uc + 1)]
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("closed", [False, True])
def test_serving_walk_budget(tmp_path, closed):
    cat = catalog(tree(tmp_path, {"alpha": chain_text(2000, 300, closed)}))
    leaves = [leaf(f"L{i}", i + 2, satisfies="alpha:FR-1") for i in range(50)]
    start = time.perf_counter()
    t = build_site_trace("alpha", leaves, cat)
    assert time.perf_counter() - start < 2
    assert len(t.serving) == 2300
    assert all(len(held) == 50 for held in t.serving.values())
    assert [e.kind for e in t.report.cycle] == (["refines_cycle"] if closed else [])


def test_derived_extremes(tmp_path):
    t = serving_trace(tmp_path, [])
    assert t.serving == {}
    assert t.report.unserved == tuple(k for k in catalog(tree(tmp_path / "b", {"alpha": SERVING_REQ})).sets[
        "alpha"].requirements if not k.startswith("alpha:CON-"))
    assert t.report.untraced == ()
    cat = catalog(tree(tmp_path / "c", {"gamma": None}))
    lone = build_site_trace("gamma", [leaf("L1", 2, satisfies="alpha:FR-1")], cat)
    assert undefined_why(lone.report.undefined) == [
        message("undefined_id", rid="alpha:FR-1", why=WHY["not_a_site"].format(ns="alpha"))]
    assert lone.serving == {}


# --- Task 6: the docs ----------------------------------------------------------------

DOCS = Path(__file__).resolve().parent.parent / "docs"


def test_format_doc_states_trace_rules():
    text = (DOCS / "requirements-format.md").read_bytes().decode("utf-8")
    doc = " ".join(text.split())
    firsts = {row[0] for table in md_tables(text) for row in table.rows}
    names = [name for name in (f.name for f in fields(TraceReport)) if name != "unserved_scope"]
    assert [name for name in names if f"`{name}`" not in firsts] == []
    kinds = [kind for held in FIELDS.values() for kind in held]
    assert [kind for kind in kinds if f"`{kind}`" not in doc] == []
    wanted = ["unserved_scope", '"site"', "bracketed list", "skipped", "direct", "indirect",
              "never listed as unserved", "catalog", "#21", "gantry/trace.py", "probable unbracketed list"]
    assert [w for w in wanted if w not in doc] == []
    assert "(#19)" not in doc
    slugs = heading_slugs(text.replace("\r", ""))
    anchors = re.findall(r"\]\(#([^)]+)\)", text)
    assert anchors and [a for a in anchors if a not in slugs] == []


def test_worksite_page_points_at_the_satisfies_key():
    page = " ".join((DOCS / "worksite-page.md").read_bytes().decode("utf-8").split())
    assert "[requirements-format.md](requirements-format.md#the-satisfies-key)" in page
    assert "the-satisfies-key" in heading_slugs(
        (DOCS / "requirements-format.md").read_bytes().decode("utf-8").replace("\r", ""))


def test_catalog_keeps_the_portfolio_it_read(tmp_path):
    # The full build plans the week from this one read (#61).
    from test_planner import install

    kept = catalog(install(tmp_path, "binds_lanes") / "gantry.toml").portfolio
    assert kept.settings_read is True
    assert kept.capacity.agent_lanes == 1
    assert kept.capacity.attention_hours["mon"] == 8
    assert fields(Catalog)[-1].name == "portfolio"
    none = catalog(install(tmp_path, "binds_lanes", config={"portfolio_file": None}) / "gantry.toml")
    assert none.portfolio is None and none.portfolio_set is False
