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
from gantry.trace import (FIELDS, LEAF_SOURCE, MESSAGES, TRACE_KINDS, UNSERVED_SCOPE, Serving, SiteTrace,
                          TraceReport, satisfies_ids)
from gantry.wbs_stopgap import parse_wbs
from helpers_config import write_site_tree


def leaf(key, line, glyph=" ", **meta):
    """A parsed leaf; `meta` values are raw texts as the stopgap stores them."""
    return Leaf(key, key, str(line), key, glyph, STATE_GLYPHS[glyph], glyph not in FINISHED_GLYPHS, None, (),
                {"issue": key, **meta}, None, line)


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
