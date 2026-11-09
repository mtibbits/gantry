"""gantry/trace.py: `satisfies:` read from parsed leaves, the catalog and the
per-site trace (#19). Every fixture is an inline string; no WBS.md is read
except through parse_wbs in memory."""

import re
import time
from dataclasses import fields

import pytest

from gantry import requirements
from gantry.config import load_config
from gantry.model import FINISHED_GLYPHS, STATE_GLYPHS, Leaf
from gantry.requirements import ReportEntry, ReqId
from gantry.sites import load_sites
from gantry.trace import (FIELDS, LEAF_SOURCE, MESSAGES, TRACE_KINDS, UNSERVED_SCOPE, WHY, Catalog, Serving,
                          SiteTrace, TraceReport, load_catalog, satisfies_ids)
from gantry.wbs_stopgap import parse_wbs
from helpers_config import write_site_tree
from sample_roles import SMALL
from test_samples import GANTRY_SAMPLE


def leaf(key, line, glyph=" ", **meta):
    """A parsed leaf; `meta` values are raw texts as the stopgap stores them."""
    return Leaf(key, key, str(line), key, glyph, STATE_GLYPHS[glyph], glyph not in FINISHED_GLYPHS, None, (),
                {"issue": key, **meta}, None, line)


def tree(tmp_path, sites, portfolio=None, br=None, tail="", **raw):
    """An installation in tmp_path: `sites` maps a site name to its requirements
    text (written to <name>.md beside the config), or None for a site with no
    requirements key. `portfolio` is the portfolio file's BR-section text."""
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
    return load_catalog(c, load_sites(c)[0])


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


def test_lookup_reasons(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: a\n", "beta": "- SR-1: b\n", "gamma": None},
                       portfolio="- BR-1: one\n"))
    found, why = cat.lookup(rid("alpha:FR-1"))
    assert (found.id, why) == (rid("alpha:FR-1"), None)
    assert cat.lookup(rid("life:BR-1"))[0].layer == "BR"
    assert cat.lookup(rid("alpha:FR-9")) == (None, WHY["not_in_file"].format(ns="alpha", rid="alpha:FR-9"))
    assert cat.lookup(rid("gamma:FR-1")) == (None, WHY["no_file"].format(ns="gamma"))
    assert cat.lookup(rid("zeta:FR-1")) == (None, WHY["no_file"].format(ns="zeta"))
    assert cat.lookup(rid("life:BR-9")) == (None, WHY["not_in_portfolio"].format(rid="life:BR-9"))


def test_sample_catalog_resolves_br_id():
    cat = catalog(GANTRY_SAMPLE)
    assert list(cat.sets) == [SMALL, "life"]
    assert list(cat.sets["life"].requirements) == ["life:BR-1", "life:BR-2", "life:BR-3"]
    assert cat.portfolio_report == ()
    assert cat.lookup(ReqId("life", "BR", 2))[0].layer == "BR"
