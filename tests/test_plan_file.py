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
from test_planner import FIXTURE_NAMES, FIXTURES, built, install, plan_of, portfolio_md, reserved

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


def test_shape_file_matches_plan_version():
    assert SHAPE["plan_version"] == PLAN_VERSION
    assert SHAPE["status_values"] == ["planned", "no_plan"]
    assert set(SHAPE) == {"plan_version", "status_values", "shape"}


def field_paths(node, path="plan"):
    """Every field path of a shape node, with its scalar type where it has one."""
    if isinstance(node, str):
        return {path: node}
    if isinstance(node, list):
        return {key: f"null|{kind}" for key, kind in field_paths(node[1], path).items()}
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
}


def test_shape_file_keeps_every_v1_field():
    paths = field_paths(SHAPE["shape"])
    assert {path: paths.get(path) for path in V1_FIELDS} == V1_FIELDS
    assert len(V1_FIELDS) == 64


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


@pytest.mark.parametrize("base", FIXTURE_NAMES)
def test_fixture_builds_byte_identical(tmp_path, base):
    files = []
    for _ in range(2):
        folder = install(tmp_path, base)
        code, stdout, stderr = run_main("--config", str(folder / "gantry.toml"))
        assert code == 0, stderr
        assert stdout.splitlines()[-1] == str(folder / "out" / PLAN_FILE)
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
    assert sorted(written_bytes(folder / "out")) == ["index.html", "sites/a.html", "sites/b.html", PLAN_FILE]


def test_write_failure_exits_1(tmp_path, monkeypatch):
    from gantry import build

    folder = install(tmp_path, "states")
    out = folder / "out"
    (out / PLAN_FILE).mkdir(parents=True)
    seen = []
    real = build.render_index

    def render_index(result):
        seen.append(result.plan)
        return real(result)

    monkeypatch.setattr(build, "render_index", render_index)
    code, stdout, stderr = run_main("--config", str(folder / "gantry.toml"))
    assert code == 1
    (line,) = stderr.splitlines()
    assert line.startswith("gantry: error: cannot write week-plan.json: ")
    assert len(line) > len("gantry: error: cannot write week-plan.json: ")
    assert str(tmp_path) not in line and "/" not in line
    # No partial or temporary file; the pages and the index are written and printed.
    assert sorted(p.name for p in out.iterdir()) == ["index.html", "sites", PLAN_FILE]
    assert list((out / PLAN_FILE).iterdir()) == []
    assert stdout.splitlines() == [str(out / "sites" / "a.html"), str(out / "sites" / "b.html"),
                                   str(out / "index.html")]
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
