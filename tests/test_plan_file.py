"""The plan file: week-plan.json, its pinned shape and its write (#61)."""

import copy
import json
import os
import re
from pathlib import Path

import pytest

from gantry import planner
from gantry.planner import PLAN_FILE, PLAN_VERSION, plan_to_json, write_plan
from gantry.roots import mkdir_confined
from helpers_config import copy_samples, demo_build, run_main, snapshot, written_bytes
from test_planner import (CAPACITY_INSTALL, FIXTURE_NAMES, FIXTURES, PAUSED_C, RATIO, built, capacity_config,
                          capacity_portfolio, declares, install, plan_of, portfolio_md, reserved)

SHAPE = json.loads((FIXTURES / "shape-v1.json").read_bytes().decode("utf-8"))
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


SCALARS = {
    "string": lambda v: isinstance(v, str),
    "date": lambda v: isinstance(v, str) and DATE_RE.fullmatch(v) is not None,
    "number": _is_number,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
}


def _node_errors(value, node, path):
    if isinstance(node, str):
        return [] if SCALARS[node](value) else [f"{path}: not {'an' if node[0] in 'aeiou' else 'a'} {node}"]
    if isinstance(node, list):  # ["null", <node>]
        assert node[0] == "null" and len(node) == 2
        return [] if value is None else _node_errors(value, node[1], path)
    (kind, inner), = node.items()
    if kind == "array":
        if not isinstance(value, list):
            return [f"{path}: not an array"]
        return [e for i, item in enumerate(value) for e in _node_errors(item, inner, f"{path}[{i}]")]
    assert kind == "object"
    if not isinstance(value, dict):
        return [f"{path}: not an object"]
    errors = [f"{path}.{name}: missing" for name in inner if name not in value]
    errors += [f"{path}.{name}: not in the pinned shape" for name in value if name not in inner]
    return errors + [e for name in inner if name in value
                     for e in _node_errors(value[name], inner[name], f"{path}.{name}")]


def shape_errors(document, shape=SHAPE) -> list[str]:
    """Why `document` is not of the pinned shape; [] when it is. Every listed
    field is required, an unlisted one is an error, and only `status` is
    checked against values."""
    errors = _node_errors(document, shape["shape"], "plan")
    if isinstance(document, dict) and document.get("status") not in shape["status_values"]:
        errors.append(f"plan.status: {document.get('status')!r} is not a status of plan_version "
                      f"{shape['plan_version']}")
    return errors


def document_of(plan):
    return json.loads(plan_to_json(plan))


def no_plan(tmp_path):
    return plan_of(tmp_path, "binds_lanes", config={"portfolio_file": None})


# --- the text -----------------------------------------------------------------------------


def test_plan_version_is_1(tmp_path):
    assert PLAN_VERSION == 1
    assert document_of(plan_of(tmp_path, "states"))["plan_version"] == 1
    assert document_of(no_plan(tmp_path))["plan_version"] == 1
    assert PLAN_FILE == "week-plan.json"


def _assert_sorted(pairs):
    names = [name for name, _ in pairs]
    assert names == sorted(names), names
    return dict(pairs)


def test_plan_json_is_sorted_utf8_lf(tmp_path):
    label = "Réunion d'équipe – 週次"
    plan = plan_of(tmp_path, "binds_lanes", portfolio=portfolio_md(tail=reserved("tue", 1, label)))
    text = plan_to_json(plan)
    assert isinstance(text, str)
    assert text.endswith("}\n") and not text.endswith("\n\n")
    assert "\r" not in text
    document = json.loads(text, object_pairs_hook=_assert_sorted)  # keys sorted at every level
    assert document["days"][1]["reserved"][0]["label"] == label
    assert label in text and "\\u" not in text
    assert text.encode("utf-8").decode("utf-8") == text
    assert "NaN" not in text and "Infinity" not in text
    assert set(document) == set(plan.__dataclass_fields__)
    assert plan_to_json(plan) == text


# --- the pinned shape ---------------------------------------------------------------------


@pytest.mark.parametrize("base", FIXTURE_NAMES)
def test_shape_holds(tmp_path, base):
    document = document_of(plan_of(tmp_path, base))
    assert document["status"] == "planned" and document["leaves"]
    assert shape_errors(document) == []


def test_shape_holds_with_no_plan(tmp_path):
    document = document_of(no_plan(tmp_path))
    assert document["status"] == "no_plan"
    assert shape_errors(document) == []


def test_shape_holds_with_reserved_blocks_and_unplanned_leaves(tmp_path):
    # Every array of the shape holds at least one item in some checked document.
    document = document_of(plan_of(tmp_path, "states", portfolio=portfolio_md(
        hours=6, lanes=3, tail=reserved("tue", 1, "Standing meeting") + reserved("wed", 1))))
    assert document["days"][1]["reserved"] and document["sites"][0]["unplanned"]
    assert any(day["blocks"] for day in document["days"])
    assert shape_errors(document) == []
    document = document_of(plan_of(tmp_path, "binds_lanes"))
    assert document["days"][0]["lanes_row"] and document["pools"][1]["days_run_out"]
    assert shape_errors(document) == []
    document = document_of(plan_of(tmp_path, "cross_site_cycle"))
    assert document["report"][0]["leaves"]
    assert shape_errors(document) == []
    document = document_of(plan_of(tmp_path, "share_unbuilt"))
    assert document["notes"][0]["projects"]
    assert shape_errors(document) == []
    document = document_of(plan_of(tmp_path, "no_share"))
    assert any(site["shareless_days"] for site in document["sites"])
    assert shape_errors(document) == []
    document = document_of(plan_of(tmp_path, "p0"))
    assert any(entry["held_by"] for site in document["sites"] for entry in site["p0"])
    assert shape_errors(document) == []


def _states(tmp_path):
    return document_of(plan_of(tmp_path, "states"))


def test_shape_rejects(tmp_path):
    base = _states(tmp_path)
    assert shape_errors(base) == []

    removed = copy.deepcopy(base)
    del removed["binding"]["first_day"]
    assert shape_errors(removed) == ["plan.binding.first_day: missing"]

    removed = copy.deepcopy(base)
    del removed["allocation_rule"]
    assert shape_errors(removed) == ["plan.allocation_rule: missing"]

    retyped = copy.deepcopy(base)
    retyped["days"][0]["lane_hours"] = "18"
    assert shape_errors(retyped) == ["plan.days[0].lane_hours: not a number"]

    retyped = copy.deepcopy(base)
    retyped["leaves"][0]["need"] = 1.5
    assert shape_errors(retyped) == ["plan.leaves[0].need: not an integer"]

    retyped = copy.deepcopy(base)
    retyped["plan_version"] = 1.0
    assert shape_errors(retyped) == ["plan.plan_version: not an integer"]

    added = copy.deepcopy(base)
    added["sites"][0]["tier"] = "started"
    assert shape_errors(added) == ["plan.sites[0].tier: not in the pinned shape"]

    added = copy.deepcopy(base)
    added["note"] = None
    assert shape_errors(added) == ["plan.note: not in the pinned shape"]

    paused = copy.deepcopy(base)
    paused["status"] = "paused"
    assert shape_errors(paused) == ["plan.status: 'paused' is not a status of plan_version 1"]

    undated = copy.deepcopy(base)
    undated["as_of"] = "19 January 2026"
    assert shape_errors(undated) == ["plan.as_of: not a date"]


def test_shape_accepts_unknown_unplanned_reason(tmp_path):
    document = _states(tmp_path)
    entry = document["sites"][0]["unplanned"][0]
    entry["reason"] = "waits_on_weather"
    assert entry["reason"] not in planner.UNPLANNED_REASONS
    assert shape_errors(document) == []
    # Every enumerated field but status is text: any value passes.
    document["allocation_rule"] = "fair share by intended shares"
    document["pools"][0]["capacity_source"] = "devagent"
    document["leaves"][0]["tag"] = "guessed"
    assert shape_errors(document) == []


def test_shape_holds_on_plans_made_from_a_declaration(tmp_path):
    # A used declaration (#39) that fills every node it can at once: Monday's hours scaled and
    # capped, a lane's day capped, a ratio, a token budget and a paused site.
    devagent = declares(total=30, lane_hours=168, max_lanes=1, more=RATIO % 0.25, tail=PAUSED_C)
    document = document_of(plan_of(tmp_path, CAPACITY_INSTALL, devagent=devagent,
                                   portfolio=capacity_portfolio("one_day")))
    capacity = document["capacity"]
    assert (document["status"], capacity["declaration"], capacity["capacity_schema"]) == ("planned", "used", "1.0")
    assert capacity["attention"] == {"rule": "scaled", "total_hours": 30, "weekday_sum_hours": 8.0, "factor": 3.75,
                                     "weekdays_lost_to_cap": [{"weekday": "mon", "hours": 8.0}]}
    assert capacity["lanes"] == {"max_lanes": 1, "lane_hours_per_week": 168, "working_days": 1,
                                 "lane_hours_per_day": 24.0, "hours_lost_to_cap": 144.0}
    assert capacity["declared_ratio"] == {"value": 0.25, "used": True, "reason": None}
    assert capacity["token_budget"] == {"tokens_per_week": 500000000, "binding": "tokens_per_week",
                                        "unit": "weighted_input_token_equivalents"}
    assert capacity["paused"] == ["c"]
    assert [day["attention_hours_lost_to_cap"] for day in document["days"]] == [8.0] * 10
    assert {entry["reason"] for site in document["sites"] if site["name"] == "c"
            for entry in site["unplanned"]} == {"paused"}
    assert "declared" in {leaf["tag"] for leaf in document["leaves"]}
    assert shape_errors(document) == []
    # A ratio that is not used holds its reason, and unknown keys their lines.
    devagent = declares(more=RATIO % 30 + "max_lane = 3\n")
    document = document_of(plan_of(tmp_path, CAPACITY_INSTALL, devagent=devagent))
    assert (document["status"], document["capacity"]["declaration"]) == ("planned", "used")
    assert document["capacity"]["declared_ratio"] == {
        "value": 30, "used": False,
        "reason": "devagent declares 30 operator hours per agent hour, above 24; it is not used"}
    assert document["report"] == [{"kind": "capacity_unknown_key", "leaves": [],
                                   "text": "capacity: warning: capacity.max_lane: unknown key (ignored)"}]
    assert shape_errors(document) == []
    # An invalid declaration and no plan: devagent's lines are the report, and nothing it declares is held.
    document = document_of(plan_of(tmp_path, CAPACITY_INSTALL, devagent=capacity_config("order"),
                                   portfolio=capacity_portfolio("no_capacity")))
    assert (document["status"], document["capacity"]["declaration"]) == ("no_plan", "invalid")
    assert document["capacity"] == {"declaration": "invalid", "capacity_schema": "1.0", "attention": None,
                                    "lanes": None, "declared_ratio": None, "token_budget": None, "paused": []}
    assert [entry["kind"] for entry in document["report"]] == ["capacity_invalid"] * 9 + ["capacity_unknown_key"] * 2
    assert all(isinstance(entry["text"], str) and entry["leaves"] == [] for entry in document["report"])
    assert shape_errors(document) == []
    # A used one and no plan keeps its ratio, its budget and its paused site.
    document = document_of(plan_of(tmp_path, CAPACITY_INSTALL, devagent=declares(more=RATIO % 0.25, tail=PAUSED_C),
                                   config={"portfolio_file": None}))
    capacity = document["capacity"]
    assert (document["status"], capacity["declaration"], capacity["attention"], capacity["lanes"]) == (
        "no_plan", "used", None, None)
    assert capacity["declared_ratio"]["used"] and capacity["token_budget"] and capacity["paused"] == ["c"]
    assert shape_errors(document) == []


def test_shape_holds_a_declared_capacity(tmp_path):
    # Each node a declaration fills (#39), on a document filled in by hand, and what the shape
    # refuses in each. test_shape_holds_on_plans_made_from_a_declaration checks the planner's own.
    base = _states(tmp_path)
    assert base["capacity"]["declaration"] == "not_declared" and base["report"] == []
    assert (base["capacity"]["declared_ratio"], base["capacity"]["token_budget"], base["capacity"]["paused"]) == (
        None, None, [])
    base["capacity"] |= {
        "declaration": "used", "capacity_schema": "1.0",
        "attention": {"rule": "scaled", "total_hours": 30, "weekday_sum_hours": 20.0, "factor": 1.5,
                      "weekdays_lost_to_cap": [{"weekday": "tue", "hours": 6}]},
        "declared_ratio": {"value": 30, "used": False, "reason": "above 24"},
        "token_budget": {"tokens_per_week": 246000000, "binding": "tokens_per_week",
                         "unit": "weighted_input_token_equivalents"},
        "paused": ["b"],
    }
    base["capacity"]["lanes"] |= {"lane_hours_per_week": 0.001, "hours_lost_to_cap": 144}
    base["days"][0]["attention_hours_lost_to_cap"] = 8
    base["report"] = [{"kind": "capacity_unknown_key", "leaves": [], "text": "capacity: warning"},
                      {"kind": "cross_site_cycle", "leaves": [{"site": "a", "key": "Issue-1"}], "text": None}]
    assert shape_errors(base) == []

    used = copy.deepcopy(base)
    used["capacity"]["declared_ratio"] = {"value": 0.25, "used": True, "reason": None}
    assert shape_errors(used) == []

    for change, error in [
        (lambda d: d.pop("capacity"), "plan.capacity: missing"),
        (lambda d: d["capacity"].pop("paused"), "plan.capacity.paused: missing"),
        (lambda d: d["capacity"].update(declaration=None), "plan.capacity.declaration: not a string"),
        (lambda d: d["capacity"].update(capacity_schema=1.0), "plan.capacity.capacity_schema: not a string"),
        (lambda d: d["capacity"]["attention"].pop("factor"), "plan.capacity.attention.factor: missing"),
        (lambda d: d["capacity"]["attention"].update(rule=None), "plan.capacity.attention.rule: not a string"),
        (lambda d: d["capacity"]["attention"].pop("weekdays_lost_to_cap"),
         "plan.capacity.attention.weekdays_lost_to_cap: missing"),
        (lambda d: d["capacity"]["attention"].update(weekdays_lost_to_cap=None),
         "plan.capacity.attention.weekdays_lost_to_cap: not an array"),
        (lambda d: d["capacity"]["attention"]["weekdays_lost_to_cap"][0].update(weekday=1),
         "plan.capacity.attention.weekdays_lost_to_cap[0].weekday: not a string"),
        (lambda d: d["capacity"]["attention"]["weekdays_lost_to_cap"][0].update(hours="6"),
         "plan.capacity.attention.weekdays_lost_to_cap[0].hours: not a number"),
        (lambda d: d["capacity"]["attention"]["weekdays_lost_to_cap"][0].pop("hours"),
         "plan.capacity.attention.weekdays_lost_to_cap[0].hours: missing"),
        (lambda d: d["capacity"]["attention"]["weekdays_lost_to_cap"][0].update(date="2026-01-20"),
         "plan.capacity.attention.weekdays_lost_to_cap[0].date: not in the pinned shape"),
        (lambda d: d["capacity"]["lanes"].update(max_lanes=2.5), "plan.capacity.lanes.max_lanes: not an integer"),
        (lambda d: d["capacity"]["lanes"].update(working_days=None),
         "plan.capacity.lanes.working_days: not an integer"),
        (lambda d: d["capacity"]["lanes"].update(lane_hours_per_day=None),
         "plan.capacity.lanes.lane_hours_per_day: not a number"),
        (lambda d: d["capacity"]["lanes"].update(lane_hours_per_week="40"),
         "plan.capacity.lanes.lane_hours_per_week: not a number"),
        (lambda d: d["capacity"]["lanes"].update(hours_lost_to_cap=None),
         "plan.capacity.lanes.hours_lost_to_cap: not a number"),
        (lambda d: d["capacity"]["lanes"].pop("lane_hours_per_week"),
         "plan.capacity.lanes.lane_hours_per_week: missing"),
        (lambda d: d["capacity"]["declared_ratio"].update(used="no"),
         "plan.capacity.declared_ratio.used: not a boolean"),
        (lambda d: d["capacity"]["declared_ratio"].update(value=None),
         "plan.capacity.declared_ratio.value: not a number"),
        (lambda d: d["capacity"]["declared_ratio"].update(reason=24),
         "plan.capacity.declared_ratio.reason: not a string"),
        (lambda d: d["capacity"]["declared_ratio"].pop("reason"), "plan.capacity.declared_ratio.reason: missing"),
        (lambda d: d["capacity"]["token_budget"].update(tokens_per_week=1.5),
         "plan.capacity.token_budget.tokens_per_week: not an integer"),
        (lambda d: d["capacity"]["token_budget"].update(binding=None),
         "plan.capacity.token_budget.binding: not a string"),
        (lambda d: d["capacity"]["token_budget"].update(unit=None), "plan.capacity.token_budget.unit: not a string"),
        (lambda d: d["capacity"]["token_budget"].pop("unit"), "plan.capacity.token_budget.unit: missing"),
        (lambda d: d["capacity"].update(paused=[1]), "plan.capacity.paused[0]: not a string"),
        (lambda d: d["days"][0].pop("attention_hours_lost_to_cap"),
         "plan.days[0].attention_hours_lost_to_cap: missing"),
        (lambda d: d["days"][0].update(attention_hours_lost_to_cap=None),
         "plan.days[0].attention_hours_lost_to_cap: not a number"),
        (lambda d: d["report"][1].pop("text"), "plan.report[1].text: missing"),
        (lambda d: d["report"][0].update(text=3), "plan.report[0].text: not a string"),
    ]:
        changed = copy.deepcopy(base)
        change(changed)
        assert shape_errors(changed) == [error]


def test_shape_file_matches_plan_version():
    assert SHAPE["plan_version"] == PLAN_VERSION
    assert SHAPE["status_values"] == ["planned", "no_plan"]
    assert set(SHAPE) == {"plan_version", "status_values", "shape"}


def field_paths(node, path="plan"):
    """Every field path of a shape node, with its scalar type where it has one.

    A value that may be null is `null|<type>`. An object or an array that may
    be null is a path of its own, `null|object` or `null|array`, and each
    field under it keeps its own type: null marks the one field that may be."""
    if isinstance(node, str):
        return {path: node}
    if isinstance(node, list):  # ["null", <node>]
        inner = node[1]
        if isinstance(inner, str):
            return {path: f"null|{inner}"}
        (kind, _), = inner.items()
        return {path: f"null|{kind}"} | field_paths(inner, path)
    (kind, inner), = node.items()
    if kind == "array":
        return field_paths(inner, path + "[]")
    found = {}
    for name, child in inner.items():
        found |= field_paths(child, f"{path}.{name}")
    return found


# The field paths of plan_version 1, frozen: shape-v1.json may gain fields, never
# lose, rename or retype one. That takes shape-v2.json and PLAN_VERSION = 2.
V1_FIELDS = {
    "plan.plan_version": "integer", "plan.status": "string", "plan.reason": "null|string",
    "plan.as_of": "date", "plan.allocation_rule": "string", "plan.unestimated_planned": "integer",
    "plan.horizon_days[]": "date", "plan.planned_week[]": "date",
    "plan.mapping.values[]": "number", "plan.mapping.source": "string", "plan.mapping.rejected": "null|string",
    "plan.pools[].name": "string", "plan.pools[].capacity_source": "string",
    "plan.pools[].days_run_out[]": "date",
    "plan.binding.pool": "null|string", "plan.binding.first_day": "null|date", "plan.binding.text": "string",
    "plan.days[].date": "date", "plan.days[].weekday": "string",
    "plan.days[].attention_hours_declared": "number", "plan.days[].attention_hours": "number",
    "plan.days[].reserved[].start": "string", "plan.days[].reserved[].hours": "number",
    "plan.days[].reserved[].label": "null|string",
    "plan.days[].lane_hours": "number",
    "plan.days[].blocks[].number": "integer", "plan.days[].blocks[].site": "string",
    "plan.days[].blocks[].length_hours": "number", "plan.days[].blocks[].start_cost_hours": "number",
    "plan.days[].blocks[].leaves[].site": "string", "plan.days[].blocks[].leaves[].key": "string",
    "plan.days[].blocks[].leaves[].agent_hours": "number",
    "plan.days[].blocks[].leaves[].operator_hours": "number",
    "plan.days[].lanes_row[].site": "string", "plan.days[].lanes_row[].key": "string",
    "plan.days[].lanes_row[].agent_hours": "number",
    "plan.days[].left.attention_hours": "number", "plan.days[].left.lane_hours": "number",
    "plan.days[].ran_out[]": "string", "plan.days[].unestimated": "integer",
    "plan.leaves[].site": "string", "plan.leaves[].key": "string", "plan.leaves[].rate": "number",
    "plan.leaves[].need": "null|integer", "plan.leaves[].tag": "string", "plan.leaves[].basis": "string",
    "plan.leaves[].agent_hours": "number", "plan.leaves[].unestimated": "boolean",
    "plan.leaves[].state": "string", "plan.leaves[].hours_after_horizon": "number",
    "plan.leaves[].days[]": "date",
    "plan.sites[].name": "string", "plan.sites[].ratio_source": "string",
    "plan.sites[].attention_hours_charged": "number", "plan.sites[].lane_hours_charged": "number",
    "plan.sites[].unestimated_planned": "integer",
    "plan.sites[].unplanned[].key": "string", "plan.sites[].unplanned[].reason": "string",
    "plan.sites[].unplanned[].state": "string",
    # Added by #62: always written; a reader takes a missing one as empty or 0.
    "plan.report[].kind": "string", "plan.report[].leaves[].site": "string",
    "plan.report[].leaves[].key": "string", "plan.leaves[].unresolved_local": "integer",
    "plan.sites[].unplanned[].unresolved_local": "integer",
    # Added by #37: always written, read by the week view (#38) except share_note, which
    # stays provisional; a reader takes a missing list as empty and a missing share_note
    # or tier as not given.
    "plan.notes[].kind": "string", "plan.notes[].date": "date", "plan.notes[].projects[]": "string",
    "plan.notes[].text": "string", "plan.leaves[].tier": "string",
    "plan.sites[].shares[].date": "date", "plan.sites[].shares[].share": "null|number",
    "plan.sites[].shareless_days[]": "date", "plan.sites[].share_note": "null|string",
    "plan.sites[].p0[].key": "string", "plan.sites[].p0[].held_by[]": "string",
    "plan.sites[].attention_hours_target": "number", "plan.sites[].lane_hours_target": "number",
}


def test_field_paths_give_a_nullable_object_its_own_path():
    node = {"object": {
        "a": ["null", {"object": {"b": "string", "c": ["null", "number"],
                                  "d": {"array": {"object": {"e": "date"}}}}}],
        "f": ["null", "string"],
        "g": ["null", {"array": "integer"}],
        "h": {"object": {"i": "boolean"}},
    }}
    assert field_paths(node) == {
        "plan.a": "null|object", "plan.a.b": "string", "plan.a.c": "null|number", "plan.a.d[].e": "date",
        "plan.f": "null|string", "plan.g": "null|array", "plan.g[]": "integer", "plan.h.i": "boolean",
    }


def test_shape_file_keeps_every_v1_field():
    paths = field_paths(SHAPE["shape"])
    assert {path: paths.get(path) for path in V1_FIELDS} == V1_FIELDS
    assert len(V1_FIELDS) == 77


# --- the write ----------------------------------------------------------------------------


def written(tmp_path):
    """(plan, roots, the made output folder) of a built copy of states/."""
    result, portfolio, config = built(install(tmp_path, "states"))
    mkdir_confined(result.roots, config.output_dir, key="output_dir")
    return planner.plan_week(result, portfolio, config), result.roots, Path(config.output_dir)


def test_write_plan_writes_only_the_file(tmp_path):
    plan, roots, out = written(tmp_path)
    before = snapshot(tmp_path)
    assert write_plan(plan, out, roots=roots) is None
    after = snapshot(tmp_path)
    name = (out / PLAN_FILE).relative_to(tmp_path).as_posix()
    # The folder's own modification time moves with its new entry.
    folder = out.relative_to(tmp_path).as_posix()
    assert {k: v for k, v in after.items() if k not in (name, folder)} == \
        {k: v for k, v in before.items() if k != folder}
    assert name not in before
    assert (out / PLAN_FILE).read_bytes() == plan_to_json(plan).encode("utf-8")
    assert sorted(p.name for p in out.iterdir()) == [PLAN_FILE]


def test_write_plan_replaces_an_older_file(tmp_path):
    plan, roots, out = written(tmp_path)
    (out / PLAN_FILE).write_bytes(b'{"plan_version": 0}\n')
    write_plan(plan, out, roots=roots)
    assert (out / PLAN_FILE).read_bytes() == plan_to_json(plan).encode("utf-8")
    assert sorted(p.name for p in out.iterdir()) == [PLAN_FILE]


def test_write_plan_failure_leaves_no_partial_file(tmp_path):
    plan, roots, out = written(tmp_path)
    (out / PLAN_FILE).mkdir()
    before = sorted(p.name for p in out.iterdir())
    with pytest.raises(OSError):
        write_plan(plan, out, roots=roots)
    assert sorted(p.name for p in out.iterdir()) == before == [PLAN_FILE]
    assert list((out / PLAN_FILE).iterdir()) == []


def test_write_plan_makes_no_folder(tmp_path):
    result, portfolio, config = built(install(tmp_path, "states"))
    plan = planner.plan_week(result, portfolio, config)
    with pytest.raises(OSError):
        write_plan(plan, config.output_dir, roots=result.roots)
    assert not os.path.exists(config.output_dir)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes only")
def test_plan_file_mode_follows_umask(tmp_path):
    plan, roots, out = written(tmp_path)
    old = os.umask(0o022)
    try:
        write_plan(plan, out, roots=roots)
    finally:
        os.umask(old)
    assert (out / PLAN_FILE).stat().st_mode & 0o777 == 0o644


# --- the build writes it --------------------------------------------------------------------


def test_demo_builds_byte_identical(tmp_path, monkeypatch):
    first = demo_build(tmp_path / "a", monkeypatch, copy=copy_samples(tmp_path / "a"))
    second = demo_build(tmp_path / "b", monkeypatch, copy=copy_samples(tmp_path / "b"))
    assert first[0] == second[0] == 0
    one, two = (first[1] / PLAN_FILE).read_bytes(), (second[1] / PLAN_FILE).read_bytes()
    assert one == two
    document = json.loads(one.decode("utf-8"))
    assert (document["plan_version"], document["status"], document["as_of"]) == (1, "planned", "2026-01-20")
    assert shape_errors(document) == []
    assert str(tmp_path).encode("utf-8") not in one
    # A rebuild of the same copy changes nothing either.
    again = demo_build(tmp_path / "a", monkeypatch, copy=tmp_path / "a" / "samples")
    assert (again[1] / PLAN_FILE).read_bytes() == one


# The fixtures with a fire: cross_site and p0 hold open P0 leaves (#32).
# leaf_boxes plants the priority "urgent!" (#170), a WBS.md line not read as a priority: a second unread source.
FIXTURE_FIRES_LINES = {"cross_site": "fires: 1; sources not fully read: 1", "p0": "fires: 4; sources not fully read: 1",
                       "leaf_boxes": "fires: 0; sources not fully read: 2"}


@pytest.mark.parametrize("base", FIXTURE_NAMES)
def test_fixture_builds_byte_identical(tmp_path, base):
    files = []
    for _ in range(2):
        folder = install(tmp_path, base)
        code, stdout, stderr = run_main("--config", str(folder / "gantry.toml"))
        assert code == 0, stderr
        # The plan file, then the fires line (#32); no fixture sets fires_list.
        assert stdout.splitlines()[-2:] == [str(folder / "out" / PLAN_FILE), FIXTURE_FIRES_LINES.get(
            base, "fires: 0; sources not fully read: 1")]
        files.append((folder / "out" / PLAN_FILE).read_bytes())
    assert files[0] == files[1]
    document = json.loads(files[0].decode("utf-8"))
    assert (document["plan_version"], document["status"]) == (1, "planned")
    assert shape_errors(document) == []
    assert files[0].endswith(b"}\n") and b"\r" not in files[0]
    assert str(tmp_path).encode("utf-8") not in files[0]


def test_plan_written_only_in_output_folder(tmp_path):
    folder = install(tmp_path, "states")
    before = snapshot(folder)
    assert run_main("--config", str(folder / "gantry.toml"))[0] == 0
    after = snapshot(folder)
    assert {rel: stamp for rel, stamp in after.items() if rel != "out" and not rel.startswith("out/")} == before
    assert sorted(rel for rel in after if rel.endswith(".json")) == [f"out/{PLAN_FILE}"]
    assert sorted(written_bytes(folder / "out")) == ["index.html", "sites/a.html", "sites/b.html", "strategic.html",
                                                     PLAN_FILE, "week.html"]


def test_write_failure_exits_1(tmp_path, monkeypatch):
    from gantry import build

    folder = install(tmp_path, "states")
    out = folder / "out"
    (out / PLAN_FILE).mkdir(parents=True)
    seen = []
    real = build.render_index

    def render_index(result, **kw):
        seen.append(result.plan)
        return real(result, **kw)

    monkeypatch.setattr(build, "render_index", render_index)
    code, stdout, stderr = run_main("--config", str(folder / "gantry.toml"))
    assert code == 1
    (line,) = stderr.splitlines()
    assert line.startswith("gantry: error: cannot write week-plan.json: ")
    assert len(line) > len("gantry: error: cannot write week-plan.json: ")
    assert str(tmp_path) not in line and "/" not in line
    # No partial or temporary file; the pages, the week view, the strategic page and the index are
    # written and printed.
    assert sorted(p.name for p in out.iterdir()) == ["index.html", "sites", "strategic.html", PLAN_FILE, "week.html"]
    assert list((out / PLAN_FILE).iterdir()) == []
    assert stdout.splitlines() == [str(out / "sites" / "a.html"), str(out / "sites" / "b.html"),
                                   str(out / "week.html"), str(out / "strategic.html"), str(out / "index.html"),
                                   "fires: 0; sources not fully read: 1"]
    # The view still received the plan.
    assert len(seen) == 1 and isinstance(seen[0], planner.WeekPlan) and seen[0].status == "planned"


def test_write_refused_outside_roots_exits_1(tmp_path, monkeypatch):
    from gantry import cli
    from gantry.roots import OutsideRoots

    def refuse(plan, output_dir, *, roots, text=None):
        raise OutsideRoots("output_dir", "write")

    folder = install(tmp_path, "states")
    monkeypatch.setattr(cli, "write_plan", refuse)
    code, _, stderr = run_main("--config", str(folder / "gantry.toml"))
    assert code == 1
    (line,) = stderr.splitlines()
    assert line == f"gantry: error: cannot write week-plan.json: {OutsideRoots('output_dir', 'write')}"
    assert "output_dir" in line and str(tmp_path) not in line
