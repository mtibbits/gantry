"""devagent's portfolio JSON as test data (#83): the tool that makes it, the gate and the fixture.

tests/fixtures/portfolio/written.json is the pinned devagent release's own
document over the WBS texts the parser tests read (#12 deleted Gantry's own). The
ungated tests read the committed bytes, so they run on every leg. The gated
tests run the pinned aggregator from a devagent checkout (GANTRY_DEVAGENT_DIR,
tests/helpers_devagent.py): they skip on Windows and locally without the
variable, and fail under CI without it.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import commit_check
import helpers_devagent as hd
import helpers_portfolio as hp
import sample_portfolio as sp
import update_expected_leaves as ue
from gantry import portfolio_json as pj
from gantry.portfolio_json import map_document, read_portfolio
from gantry.roots import directory_roots
from helpers_config import sample_projects, written_bytes
from helpers_docs import heading_anchors, markdown_text, section
from sample_roles import NO_WBS, READABLE, UNREADABLE
from test_devagent_contract import fake_checkout
from test_samples import ROOT, SAMPLES, git_work_tree, needs_git

# U1: what devagent says of the sample site whose WBS.md is not UTF-8.
MARSH_REASON = "WBS.md could not be read: UnicodeDecodeError: invalid continuation byte at byte 88"
# The fixture's totals, as the pinned aggregator wrote them.
FIXTURE_NODES = 154
FIXTURE_ENTRIES = 75
# The 13 sources the stopgap's tests read before #12 deleted it; `edges` is #83's own, `fires` #12's,
# `models` and `models_project` #14's.
EARLIER_SOURCES = 13
LATER_SOURCES = ("edges", "fires", "models", "models_project")


def resolved(path):
    return Path(path).resolve()


def checkout_like(tmp_path, script):
    """A folder with a scripts/portfolio.sh of its own: enough for run_aggregator."""
    fake = tmp_path / "da"
    (fake / "scripts").mkdir(parents=True)
    (fake / "scripts" / "portfolio.sh").write_bytes(script)
    return fake


def needs_bash():
    if os.name == "nt":
        pytest.skip(hd.TOOLCHAIN)


# --- the gate (Task 2) ------------------------------------------------------------------------


def test_sample_document_goes_through_the_gate(tmp_path):
    tag, version = hd.read_pin()
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(pytest.skip.Exception) as info:
        hp.build_sample_document(work, env={}, os_name="posix")
    assert str(info.value.msg) == hd.reason_unset(tag)
    with pytest.raises(pytest.fail.Exception) as info:
        hp.build_sample_document(work, env={"CI": "true"}, os_name="posix")
    assert str(info.value.msg) == hd.reason_unset(tag) + hd.CI_SUFFIX
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(pytest.fail.Exception) as info:
        hp.build_sample_document(work, env={"CI": "true", hd.VARIABLE: str(empty)}, os_name="posix")
    assert str(info.value.msg) == hd.NOT_CHECKOUT + hd.CI_SUFFIX
    other = fake_checkout(tmp_path / "other", "9.9.9")
    with pytest.raises(pytest.fail.Exception) as info:
        hp.build_sample_document(work, env={"CI": "true", hd.VARIABLE: str(other)}, os_name="posix")
    message = str(info.value.msg)
    assert "9.9.9" in message and tag in message and f"({version})" in message
    with pytest.raises(pytest.skip.Exception) as info:
        hp.build_sample_document(work, env={"CI": "true", hd.VARIABLE: str(other)}, os_name="nt")
    assert str(info.value.msg) == hd.TOOLCHAIN
    assert list(work.iterdir()) == []


# --- the tool (Task 2) ------------------------------------------------------------------------


def sample_tree(tmp_path):
    samples = tmp_path / "samples"
    (samples / "devagent").mkdir(parents=True)
    (samples / "devagent" / "config.toml").write_bytes(b'[project.a]\ndevdoc_dir = "../a"\n')
    return samples


def spy_aggregator(monkeypatch):
    calls = []

    def spy(devagent, cwd, da_home):
        calls.append((devagent, cwd, da_home))
        document = Path(da_home).joinpath(*sp.DOCUMENT)
        document.parent.mkdir(parents=True)
        document.write_bytes(b"{}\n")
        return document

    monkeypatch.setattr(sp, "run_aggregator", spy)
    return calls


def test_tool_resolves_relative_paths(tmp_path, monkeypatch, capsys):
    _, version = hd.read_pin()
    fake_checkout(tmp_path / "da", version)
    samples = sample_tree(tmp_path)
    calls = spy_aggregator(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert sp.main(["--devagent", "da", "--samples", "samples"]) == 0
    (call,) = calls
    assert all(Path(p).is_absolute() for p in call)
    assert [resolved(p) for p in call] == [
        resolved(tmp_path / "da"), resolved(samples / "devagent"), resolved(samples / "devagent" / "out")]
    out = samples / "devagent" / "out"
    assert (out / "config.toml").read_bytes() == (samples / "devagent" / "config.toml").read_bytes()
    assert (out / "home").is_dir()
    assert capsys.readouterr().out == "sample_portfolio: wrote devagent/out/portfolio/portfolio.json\n"


def test_devagent_defaults_to_the_variable_read_in_main(tmp_path, monkeypatch):
    _, version = hd.read_pin()
    checkout = fake_checkout(tmp_path / "da", version)
    samples = sample_tree(tmp_path)
    calls = spy_aggregator(monkeypatch)
    monkeypatch.setenv(hd.VARIABLE, str(checkout))
    assert sp.main(["--samples", str(samples)]) == 0
    assert resolved(calls[0][0]) == resolved(checkout)


def test_tool_usage_and_preflight_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(hd.VARIABLE, raising=False)
    for argv in ([], ["--check", "--devagent", "x"], ["--fixtures", "--samples", "x", "--devagent", "x"]):
        with pytest.raises(SystemExit) as info:
            sp.main(argv)
        assert info.value.code == 2, argv
    capsys.readouterr()
    assert sp.main(["--devagent", str(tmp_path)]) == 2
    assert sp.main(["--fixtures", "--devagent", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert err.count("sample_portfolio: --devagent is not a devagent checkout") == 2
    assert str(tmp_path) not in err
    # A checkout at another release stamps nothing with the pin.
    tag, _ = hd.read_pin()
    other = fake_checkout(tmp_path / "other", "9.9.9")
    assert sp.main(["--fixtures", "--check", "--devagent", str(other)]) == 2
    assert ue.main(["--check", "--devagent", str(other)]) == 2
    want = f"--devagent is devagent 9.9.9; tests/devagent.pin names {tag}"
    assert capsys.readouterr().err.splitlines() == [f"sample_portfolio: {want}", f"update_expected_leaves: {want}"]
    # No bash on PATH is a preflight failure too.
    _, version = hd.read_pin()
    checkout = fake_checkout(tmp_path / "da", version)
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    assert sp.main(["--devagent", str(checkout), "--samples", str(sample_tree(tmp_path))]) == 2
    assert "bash is not on PATH" in capsys.readouterr().err


def test_existing_out_is_deleted_only_when_git_ignores_it(tmp_path, monkeypatch):
    _, version = hd.read_pin()
    checkout = fake_checkout(tmp_path / "da", version)
    samples = sample_tree(tmp_path)
    spy_aggregator(monkeypatch)
    out = samples / "devagent" / "out"
    sp.make_sample(checkout, samples)
    (out / "kept.txt").write_bytes(b"x")
    # A second run would delete out/; outside a checkout git cannot say it is ignored.
    with pytest.raises(sp.Refused) as info:
        sp.make_sample(checkout, samples)
    assert "ignore" in str(info.value) and str(tmp_path) not in str(info.value)
    assert (out / "kept.txt").read_bytes() == b"x"


@needs_git
def test_real_sample_home_passes_the_guard():
    # .gitignore's out/ rule covers samples/devagent/out, and nothing in it is tracked.
    if not git_work_tree(ROOT):
        pytest.skip("the gantry root is not a git work tree")
    assert sp.guard_out(SAMPLES) == SAMPLES / "devagent" / "out"


def test_aggregator_failure_is_reported(tmp_path):
    needs_bash()
    fake = checkout_like(tmp_path, b"echo boom >&2\nexit 3\n")
    with pytest.raises(sp.Refused) as info:
        sp.run_aggregator(fake, tmp_path, tmp_path / "home")
    assert "exited 3" in str(info.value) and "boom" in str(info.value)
    # Exit 0 with no document is a failure too.
    (fake / "scripts" / "portfolio.sh").write_bytes(b"exit 0\n")
    with pytest.raises(sp.Refused) as info:
        sp.run_aggregator(fake, tmp_path, tmp_path / "home")
    assert "wrote no document" in str(info.value)


def test_aggregator_gets_five_names_the_cwd_and_no_argument(tmp_path, monkeypatch):
    needs_bash()
    script = (b'mkdir -p "$DA_HOME/portfolio"\n'
              b'env > "$DA_HOME/env.txt"\n'
              b'pwd -P > "$DA_HOME/pwd.txt"\n'
              b'echo "$#" > "$DA_HOME/argc.txt"\n'
              b'echo "{}" > "$DA_HOME/portfolio/portfolio.json"\n')
    checkout_like(tmp_path, script)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("GANTRY_PROBE", "must not reach the aggregator")
    monkeypatch.chdir(tmp_path)
    document = sp.run_aggregator("da", "cwd", "home")
    assert document.is_absolute() and resolved(document) == resolved(tmp_path / "home" / "portfolio" / "portfolio.json")
    names = {line.split("=", 1)[0] for line in (tmp_path / "home" / "env.txt").read_bytes().decode("utf-8").splitlines()}
    # bash sets these four itself.
    assert names - {"PWD", "OLDPWD", "SHLVL", "_"} == {"PATH", "HOME", "DA_HOME", "TZ", "LC_ALL"}
    env = dict(line.split("=", 1) for line in (tmp_path / "home" / "env.txt").read_bytes().decode("utf-8").splitlines())
    assert resolved(env["DA_HOME"]) == resolved(tmp_path / "home")
    assert resolved(env["HOME"]) == resolved(tmp_path / "home" / "home")
    assert (env["TZ"], env["LC_ALL"]) == ("UTC", "C.UTF-8")
    assert all(os.path.isabs(entry) for entry in env["PATH"].split(os.pathsep))
    assert resolved((tmp_path / "home" / "pwd.txt").read_bytes().decode("utf-8").strip()) == resolved(cwd)
    assert (tmp_path / "home" / "argc.txt").read_bytes() == b"0\n"
    # Nothing is written beside the aggregator's own files: not in the cwd, not in the checkout.
    assert list(cwd.iterdir()) == []
    assert [p.name for p in (tmp_path / "da" / "scripts").iterdir()] == ["portfolio.sh"]


def test_fixtures_mode_writes_or_checks(tmp_path, monkeypatch, capsys):
    _, version = hd.read_pin()
    checkout = fake_checkout(tmp_path / "da", version)
    monkeypatch.setattr(sp, "WRITTEN", tmp_path / "fx" / "written.json")
    monkeypatch.setattr(sp, "PROVENANCE", tmp_path / "fx" / "provenance.json")
    document = (json.dumps({"schema_version": "1.2", "generated_at": sp.FIXED_TIME, "projects": [],
                            "parse_report": []}) + "\n").encode("utf-8")
    monkeypatch.setattr(sp, "fixture_document", lambda devagent: document)
    monkeypatch.setattr(sp, "SAMPLES_WRITTEN", tmp_path / "fx" / "samples.json")
    monkeypatch.setattr(sp, "SAMPLES_PROVENANCE", tmp_path / "fx" / "samples_provenance.json")
    monkeypatch.setattr(sp, "sample_fixture_document", lambda devagent: document)
    argv = ["--fixtures", "--devagent", str(checkout)]
    assert sp.main(argv + ["--check"]) == 1
    assert capsys.readouterr().out.splitlines() == ["sample_portfolio: written.json: missing",
                                                    "sample_portfolio: provenance.json: missing",
                                                    "sample_portfolio: samples.json: missing",
                                                    "sample_portfolio: samples_provenance.json: missing"]
    assert not (tmp_path / "fx").exists()
    assert sp.main(argv) == 0
    assert sp.WRITTEN.read_bytes() == document
    assert sp.SAMPLES_WRITTEN.read_bytes() == document
    made = json.loads(sp.PROVENANCE.read_bytes().decode("utf-8"))
    assert list(made) == ["pin", "devagent_version", "schema_version", "generated_at", "sources", "step_models"]
    made = json.loads(sp.SAMPLES_PROVENANCE.read_bytes().decode("utf-8"))
    assert list(made) == ["pin", "devagent_version", "schema_version", "generated_at", "inputs"]
    capsys.readouterr()
    assert sp.main(argv + ["--check"]) == 0
    assert capsys.readouterr().out == "sample_portfolio: 4 fixture files match\n"
    sp.WRITTEN.write_bytes(document + b"\n")
    assert sp.main(argv + ["--check"]) == 1
    assert capsys.readouterr().out == "sample_portfolio: written.json: differs from what devagent writes\n"


# --- the written fixture (Task 2) -------------------------------------------------------------


def test_fixture_sources_are_listed(tmp_path):
    assert list(sp.FIXTURE_SOURCES) == ["cycle", "empty", "fences", "ids", "nesting", "not_utf8", "page", "priority",
                                        "refs", "rows", "states", "est", "h_values", "edges", "fires", "models",
                                        "models_project"]
    assert sp.FIXTURE_SOURCES["fires"] == "tests/fixtures/portfolio/fires.md"
    for name, source in sp.FIXTURE_SOURCES.items():
        assert (ROOT / source).is_file(), name
    assert sp.FIXTURE_SOURCES["est"] == "tests/fixtures/inputs/est.md"
    assert sp.FIXTURE_SOURCES["h_values"] == "tests/fixtures/attention/h_values.md"
    assert sp.FIXTURE_SOURCES["edges"] == "tests/fixtures/portfolio/edges.md"
    assert b"\r" not in (ROOT / sp.FIXTURE_SOURCES["edges"]).read_bytes()


def test_written_fixture_shape():
    tag, version = hd.read_pin()
    raw = sp.WRITTEN.read_bytes()
    assert raw.endswith(b"}\n") and b"\r" not in raw
    doc = hp.written_doc()
    assert hp.written_doc() is not doc
    assert [p["project"] for p in doc["projects"]] == list(sp.FIXTURE_SOURCES)
    assert len(doc["projects"]) == 17
    assert sum(len(p["nodes"]) for p in doc["projects"]) == FIXTURE_NODES
    assert len(doc["parse_report"]) == FIXTURE_ENTRIES
    # The 13 earlier sources give 122 nodes and 46 entries; `fires` adds 8 nodes and 15 entries (#12).
    assert sum(len(p["nodes"]) for p in doc["projects"] if p["project"] not in LATER_SOURCES) == 122
    assert sum(e["project"] not in LATER_SOURCES for e in doc["parse_report"]) == 46
    assert sum(len(p["nodes"]) for p in doc["projects"] if p["project"] == "fires") == 8
    assert sum(e["project"] == "fires" for e in doc["parse_report"]) == 15
    # `models` and `models_project` (#14): 8 nodes and 2 bad_model entries each.
    for name in ("models", "models_project"):
        assert sum(len(p["nodes"]) for p in doc["projects"] if p["project"] == name) == 8
        assert sum(e["project"] == name for e in doc["parse_report"]) == 2
    assert doc["generated_at"] == sp.FIXED_TIME == "2026-01-12T09:30:00Z"
    by_name = {p["project"]: p for p in doc["projects"]}
    assert by_name["not_utf8"]["status"] == "unreadable"
    assert by_name["not_utf8"]["status_reason"].startswith("WBS.md could not be read: UnicodeDecodeError")
    assert {p["status"] for p in doc["projects"] if p["project"] != "not_utf8"} == {"read"}
    made = json.loads(sp.PROVENANCE.read_bytes().decode("utf-8"))
    assert list(made) == ["pin", "devagent_version", "schema_version", "generated_at", "sources", "step_models"]
    assert (made["pin"], made["devagent_version"]) == (tag, version)
    assert (made["schema_version"], made["generated_at"]) == (doc["schema_version"], sp.FIXED_TIME)
    assert made["sources"] == dict(sp.FIXTURE_SOURCES)
    assert made["step_models"] == sp.FIXTURE_STEP_MODELS == {"models_project": {"checking": "opus"}}
    earlier = [s for s in made["sources"].values() if not s.startswith("tests/fixtures/portfolio/")]
    assert len(earlier) == EARLIER_SOURCES and all((ROOT / s).is_file() for s in made["sources"].values())
    for path in (sp.WRITTEN, sp.PROVENANCE):
        assert commit_check.path_form_hits(path.read_bytes().decode("utf-8")) == [], path.name


# --- gated: the pinned aggregator -------------------------------------------------------------


def test_written_fixture_regenerates():
    # --check compares written.json, provenance.json, samples.json and samples_provenance.json,
    # with one aggregator run per document.
    checkout = hd.require_devagent()
    assert sp.main(["--fixtures", "--check", "--devagent", str(checkout)]) == 0


def test_written_fixture_passes_devagents_checker():
    checkout = hd.require_devagent()
    checker = checkout / "scripts" / "lib" / "portfolio-check.py"
    ran = subprocess.run([sys.executable, str(checker), str(sp.WRITTEN)], capture_output=True)
    assert ran.returncode == 0, ran.stderr.decode("utf-8", "replace")


def test_samples_fixture_passes_devagents_checker():
    checkout = hd.require_devagent()
    checker = checkout / "scripts" / "lib" / "portfolio-check.py"
    ran = subprocess.run([sys.executable, str(checker), str(sp.SAMPLES_WRITTEN)], capture_output=True)
    assert ran.returncode == 0, ran.stderr.decode("utf-8", "replace")


def test_sample_document_shape(sample_document):
    document, _ = sample_document
    doc = json.loads(document.read_bytes().decode("utf-8"))
    assert [p["project"] for p in doc["projects"]] == sample_projects()
    assert len(doc["projects"]) == 6
    status = {p["project"]: (p["status"], p["status_reason"]) for p in doc["projects"]}
    assert {status[site] for site in READABLE} == {("read", None)}
    assert status[NO_WBS][0] == "no_wbs" and status[NO_WBS][1]
    assert status[UNREADABLE] == ("unreadable", MARSH_REASON)
    assert commit_check.path_form_hits(document.read_bytes().decode("utf-8")) == []


def test_aggregator_writes_only_under_out(sample_document):
    document, copy = sample_document
    out = copy / "devagent" / "out"
    assert document == out / "portfolio" / "portfolio.json"
    inside = sorted(p.relative_to(out).as_posix() for p in out.rglob("*"))
    assert inside == ["config.toml", "home", "portfolio", "portfolio/portfolio.json"]
    assert (out / "config.toml").read_bytes() == (SAMPLES / "devagent" / "config.toml").read_bytes()
    # Everything else is the samples as committed: no file added, changed or removed.
    rest = {rel: data for rel, data in written_bytes(copy).items() if not rel.startswith("devagent/out/")}
    source = {rel: data for rel, data in written_bytes(SAMPLES).items() if "out" not in Path(rel).parts}
    assert rest == source and len(rest) >= 10
    folders = {p.relative_to(copy).as_posix() for p in copy.rglob("*") if p.is_dir()}
    kept = {p.relative_to(SAMPLES).as_posix() for p in SAMPLES.rglob("*")
            if p.is_dir() and "out" not in p.relative_to(SAMPLES).parts}
    assert folders - {"devagent/out", "devagent/out/home", "devagent/out/portfolio"} == kept


# --- the expected-leaves snapshots (Task 6) --------------------------------------------------

SNAPSHOT_KEYS = ["pin", "schema_version", "site", "status", "reason", "nodes", "links", "held_out", "report",
                 "devagent"]
NODE_KEYS = ["line", "row", "key", "issue", "outline", "title", "glyph", "state", "open", "priority", "epic",
             "parent", "meta", "depends_on", "rank", "est", "h", "satisfies", "objective", "models"]


def committed(name, fixture=False):
    path = (ue.FIXTURE_OUT if fixture else ue.OUT) / f"{name}.json"
    return json.loads(path.read_bytes().decode("utf-8"))


def test_snapshot_set_is_the_sample_config():
    tag, _ = hd.read_pin()
    assert sorted(p.name for p in ue.OUT.iterdir()) == sorted(
        [f"{site}.json" for site in sample_projects()] + ["fixture"])
    assert len(sample_projects()) == 6
    assert sorted(p.name for p in ue.FIXTURE_OUT.iterdir()) == sorted(f"{name}.json" for name in sp.FIXTURE_SOURCES)
    assert len(sp.FIXTURE_SOURCES) == 17
    for name, fixture in [(s, False) for s in sample_projects()] + [(n, True) for n in sp.FIXTURE_SOURCES]:
        snapshot = committed(name, fixture)
        assert list(snapshot) == SNAPSHOT_KEYS, name
        assert (snapshot["pin"], snapshot["site"], snapshot["schema_version"]) == (tag, name, "1.2")
        assert all(list(node) == NODE_KEYS for node in snapshot["nodes"]), name
        assert [n["line"] for n in snapshot["nodes"]] == sorted(n["line"] for n in snapshot["nodes"])
        raw = ((ue.FIXTURE_OUT if fixture else ue.OUT) / f"{name}.json").read_bytes()
        assert raw.endswith(b"}\n") and b"\r" not in raw
        assert commit_check.path_form_hits(raw.decode("utf-8")) == [], name
    # bakery has no WBS.md: the empty form.
    empty = committed(NO_WBS)
    assert empty["status"] == "no_wbs" and empty["reason"]
    assert [empty[key] for key in ("nodes", "links", "held_out", "report", "devagent")] == [[], [], [], [], []]
    marsh = committed(UNREADABLE)
    assert (marsh["status"], marsh["reason"]) == ("unreadable", MARSH_REASON)
    assert marsh["report"] == [[None, "site_unreadable", MARSH_REASON]]


def test_snapshot_records_what_each_consumer_reads():
    # The plan's example: quarry's line 8, a leaf that waits on another site.
    (node,) = [n for n in committed("quarry")["nodes"] if n["line"] == 8]
    assert node == {
        "line": 8, "row": False, "key": "Issue-9", "issue": "Issue-9", "outline": "1.5",
        "title": "Calibrate the weighbridge", "glyph": " ", "state": "pending", "open": True,
        "priority": None, "epic": "1", "parent": None,
        "meta": {"issue": "Issue-9", "h": "4", "depends_on": ["harbor:Issue-7"]},
        "depends_on": [["harbor:Issue-7", "foreign", "harbor", "Issue-7"]],
        "rank": 3, "est": ["absent", None, None, None, None], "h": 4, "satisfies": [], "objective": [],
        "models": None}
    assert list(node["meta"]) == ["issue", "h", "depends_on"]
    # A row: what only a leaf has is null.
    rows = [n for n in committed("rows", fixture=True)["nodes"] if n["row"]]
    assert len(rows) == 5
    for row in rows:
        assert [row[key] for key in ("open", "epic", "rank", "est", "h", "models")] == [None] * 6
        assert (row["satisfies"], row["objective"]) == ([], [])
    build = next(r for r in rows if r["line"] == 5)
    assert (build["priority"], build["parent"], build["depends_on"]) == ("high", None, [["Issue-1", "local", "rows",
                                                                                        "Issue-1"]])
    assert next(n for n in committed("nesting", fixture=True)["nodes"] if n["line"] == 3)["parent"] == "Issue-1"
    # satisfies and objective, with the configured project names as the known namespaces.
    lighthouse = committed("lighthouse")["nodes"]
    assert sum(bool(n["satisfies"]) for n in lighthouse) == SATISFYING and any(n["objective"] for n in lighthouse)
    est = {n["line"]: n["est"] for n in committed("est", fixture=True)["nodes"]}
    assert est[4] == ["time", 3.0, 3.0, 3.0, "h"] and est[6] == ["non_time", None, None, 2.5, "pr"]
    assert est[7] == ["unparseable", None, None, None, None] and est[20] == ["placeholder", None, None, None, None]
    # A leaf's tiers (#14): [thinking, checking, source, each class's source]; a row of models.md has none.
    models = {n["line"]: n["models"] for n in committed("models_project", fixture=True)["nodes"]}
    assert models[1] is None and models[4] is None and models[8] is None
    assert models[3] == ["sonnet", "opus", "wbs", ["wbs", "project"]]
    # The entries devagent's reader shows, per site.
    assert committed("cycle", fixture=True)["devagent"] == [
        [7, "dep_cycle", "dependency cycle: Issue-6 (line 7), Issue-7 (line 8)"]]
    assert committed("quarry")["devagent"] == []


SATISFYING = 8  # lighthouse's leaves that name a requirement


def test_reader_matches_the_fixture_snapshots():
    # Ungated, and it survives #12: the reader against its committed output on every fixture project.
    made = ue.snapshots(map_document(hp.written_doc()), hd.read_pin()[0])
    assert list(made) == list(sp.FIXTURE_SOURCES)
    for name, data in made.items():
        assert data == (ue.FIXTURE_OUT / f"{name}.json").read_bytes(), name
    assert made == ue.expected_fixtures()
    assert sum(len(committed(name, fixture=True)["nodes"]) for name in made) == FIXTURE_NODES


def test_dump_is_devagents_form():
    assert sp.dumps({"a": [1, None], "é": "ü"}) == '{\n  "a": [\n    1,\n    null\n  ],\n  "é": "ü"\n}\n'.encode("utf-8")


def test_snapshot_tool_checks_and_writes(tmp_path, monkeypatch, capsys):
    _, version = hd.read_pin()
    checkout = fake_checkout(tmp_path / "da", version)
    samples = {site: (ue.OUT / f"{site}.json").read_bytes() for site in sample_projects()}
    monkeypatch.setattr(ue, "expected", lambda devagent: dict(samples))
    argv = ["--devagent", str(checkout)]
    assert ue.main(argv + ["--check"]) == 0
    assert capsys.readouterr().out == "update_expected_leaves: 23 snapshots match\n"
    # A copy of the folder with one file changed, one missing and one extra in each set.
    out = tmp_path / "expected_leaves"
    shutil.copytree(ue.OUT, out)
    monkeypatch.setattr(ue, "OUT", out)
    monkeypatch.setattr(ue, "FIXTURE_OUT", out / "fixture")
    (out / "harbor.json").write_bytes(samples["harbor"] + b"\n")
    (out / "marsh.json").unlink()
    (out / "ferry.json").write_bytes(b"{}\n")
    (out / "fixture" / "ids.json").unlink()
    (out / "fixture" / "old.json").write_bytes(b"{}\n")
    before = written_bytes(out)
    assert ue.main(argv + ["--check"]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "update_expected_leaves: harbor.json: differs",
        "update_expected_leaves: marsh.json: missing",
        "update_expected_leaves: ferry.json: extra",
        "update_expected_leaves: fixture/ids.json: missing",
        "update_expected_leaves: fixture/old.json: extra",
    ]
    assert written_bytes(out) == before
    # Writing restores every expected file and leaves the list and the extra files alone.
    assert ue.main(argv) == 1
    assert capsys.readouterr().out.splitlines() == [
        "update_expected_leaves: wrote 23 snapshots",
        "update_expected_leaves: ferry.json: extra",
        "update_expected_leaves: fixture/old.json: extra",
    ]
    (out / "ferry.json").unlink()
    (out / "fixture" / "old.json").unlink()
    assert written_bytes(out) == written_bytes(ROOT / "tests" / "fixtures" / "expected_leaves")
    assert ue.main(argv + ["--check"]) == 0


def test_snapshot_tool_usage_and_preflight_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(hd.VARIABLE, raising=False)
    with pytest.raises(SystemExit) as info:
        ue.main([])
    assert info.value.code == 2
    capsys.readouterr()
    assert ue.main(["--check", "--devagent", str(tmp_path)]) == 2
    assert "update_expected_leaves: --devagent is not a devagent checkout" in capsys.readouterr().err


@pytest.mark.parametrize("tool", ["sample_portfolio.py", "update_expected_leaves.py"])
def test_tools_run_as_scripts_from_any_folder(tool, tmp_path):
    # -P leaves the script's folder off sys.path; each tool puts what it imports there itself.
    ran = subprocess.run([sys.executable, "-P", str(ROOT / "tools" / tool), "--help"], cwd=tmp_path,
                         capture_output=True)
    assert ran.returncode == 0, ran.stderr.decode("utf-8", "replace")
    assert ran.stdout.decode("utf-8").startswith(f"usage: {tool.removesuffix('.py')}")


# --- gated: the snapshots against the pinned aggregator --------------------------------------


def read_sample(sample_document):
    document, copy = sample_document
    return read_portfolio(directory_roots([copy]), document)


def test_reader_matches_the_snapshots(sample_document):
    made = ue.snapshots(read_sample(sample_document), hd.read_pin()[0])
    assert list(made) == sample_projects()
    for site, data in made.items():
        assert data == (ue.OUT / f"{site}.json").read_bytes(), site


def test_snapshots_regenerate_byte_for_byte():
    # test_reader_matches_the_snapshots compares each site; --check adds the fixture set and extra files.
    checkout = hd.require_devagent()
    assert ue.main(["--check", "--devagent", str(checkout)]) == 0


def test_map_document_equals_read_portfolio_on_the_sample_document(sample_document):
    document, _ = sample_document
    read = read_sample(sample_document)
    again = map_document(json.loads(document.read_bytes().decode("utf-8")))
    assert again == read
    assert len(read.projects) == 6 and sum(len(p.data.leaves) + len(p.data.rows) for p in read.projects) == 52
    assert len(read.devagent_report) == SAMPLE_SHOWN


SAMPLE_SHOWN = 0  # each of the 12 sample entries is mapped or suppressed


# --- the documentation (Task 7) ---------------------------------------------------------------

WORKSITE_DOC = ROOT / "docs" / "worksite-page.md"
FORMATS_DOC = ROOT / "docs" / "devagent-formats.md"
READER_HEADING = "## Reading devagent's portfolio JSON"
TOOL_ARGS = {"sample_portfolio.py": sp.parse_args, "update_expected_leaves.py": ue.parse_args}


def tool_commands(text):
    """Each `python3 tools/<tool> ...` line of a doc that runs one of this issue's tools, as words."""
    found = []
    for line in text.split("\n"):
        if line.strip().startswith("python3 tools/"):
            words = shlex.split(line.strip())
            if Path(words[1]).name in TOOL_ARGS:
                found.append(words)
    return found


def parses(words):
    """Whether the tool's own argument rules take the command line."""
    try:
        TOOL_ARGS[Path(words[1]).name](words[2:])
    except SystemExit:
        return False
    return True


def test_doc_states_the_reader_rules(capsys):
    # markdown_text: docs/ is CRLF in a Windows working tree.
    text = markdown_text(WORKSITE_DOC)
    part = section(text, READER_HEADING)
    assert part is not None
    flat = " ".join(part.split())  # a phrase may wrap
    lines = text.split("\n")
    assert lines.index("## The report") < lines.index(READER_HEADING) < lines.index("## The optional inputs")
    assert "reading-devagents-portfolio-json" in heading_anchors(text)
    for name in ("tools/sample_portfolio.py", "tools/update_expected_leaves.py", "tests/devagent.pin",
                 "GANTRY_DEVAGENT_DIR", "tests/fixtures/expected_leaves/",
                 "tests/fixtures/portfolio/written.json", "`ParsedValue`", "`read_portfolio(roots, path)`",
                 "`map_document(doc)`", "`PortfolioLoadError`", "`devagent_report`", "`meta`",
                 "devagent-formats.md#fixtures-and-the-contract-test", "#12"):
        assert name in flat, name
    for devagent_kind, gantry_kind in pj.MAPPED.items():
        assert f"`{devagent_kind}`" in part and f"`{gantry_kind}`" in part, devagent_kind
    for kind in pj.SUPPRESSED_ALWAYS + pj.SUPPRESSED_BY_RULE + ("site_unreadable",):
        assert f"`{kind}`" in part, kind
    for kind in ("unreadable", "not_json", "version", "schema"):
        assert f"`{kind}`" in part, kind
    for status in pj.STATUSES:
        assert f"`{status}`" in part, status
    # devagent's behaviour is stated at the pin, including what it does not emit.
    assert "At the release `tests/devagent.pin` names, devagent emits no warning about" in flat
    assert all(f"`{key}`" in flat for key in ("priority", "satisfies:", "objective:"))
    # Every command line parses with its tool's own argument rules, and both tools and both modes appear.
    commands = tool_commands(part)
    assert all(parses(words) for words in commands), [w for w in commands if not parses(w)]
    assert {(Path(w[1]).name, "--check" in w, "--fixtures" in w) for w in commands} == {
        ("sample_portfolio.py", False, False), ("sample_portfolio.py", False, True),
        ("sample_portfolio.py", True, True), ("update_expected_leaves.py", False, False),
        ("update_expected_leaves.py", True, False)}
    # The planted control: a line the tool refuses does not parse.
    assert not parses(shlex.split("python3 tools/sample_portfolio.py --check --devagent x"))
    assert not parses(shlex.split("python3 tools/update_expected_leaves.py --fixtures"))
    capsys.readouterr()
    # No known-differences list remains: there is one reader (#12).
    gone = "known_" + "differences.json"
    assert gone not in flat and "provisional" not in flat and "does not use it yet" not in flat
    # The skipped_line row states devagent's near-miss rule.
    (row,) = [line for line in lines if line.startswith("| `skipped_line` |")]
    assert "devagent" in row and gone not in row


def test_pin_move_recipe_names_the_new_tools_and_files(capsys):
    text = markdown_text(FORMATS_DOC)
    part = section(text, "## Fixtures and the contract test")
    assert part is not None
    commands = tool_commands(part)
    assert all(parses(words) for words in commands)
    assert {(Path(w[1]).name, "--check" in w) for w in commands} == {
        ("sample_portfolio.py", False), ("sample_portfolio.py", True),
        ("update_expected_leaves.py", False), ("update_expected_leaves.py", True)}
    assert all("--fixtures" in w for w in commands if Path(w[1]).name == "sample_portfolio.py")
    capsys.readouterr()
    for name in ("tests/fixtures/portfolio/written.json", "tests/fixtures/portfolio/provenance.json",
                 "tests/fixtures/portfolio/samples.json", "tests/fixtures/portfolio/samples_provenance.json",
                 "tests/fixtures/expected_leaves/", "tests/fixtures/devagent_records/written/"):
        assert name in part, name
    # #83 moved no pin; #12 reads the JSON's nodes, and reading projects[].issues is a follow-up of #12's.
    assert "#83" not in text
    flat = " ".join(text.split())
    assert "Gantry reads that JSON for every site's nodes (#12)" in flat and "a follow-up issue filed from #12" in flat


def test_readmes_cover_the_portfolio_tests():
    readme = " ".join(markdown_text(ROOT / "README.md").split())
    assert "tests/test_portfolio_json.py" in readme or "portfolio tests" in readme
    assert "the `WBS.md` and config formats are not yet tested against it" not in readme
    assert "the config format is not yet tested against it" in readme
    samples = " ".join(markdown_text(SAMPLES / "README.md").split())
    assert "`devagent/out/`" in samples and "tools/sample_portfolio.py" in samples and "git-ignored" in samples


def test_changelog_entry_points_at_the_section():
    # Read CHANGELOG.md: a release compiles changelog.d/83.added.md into it and deletes the file.
    entry = markdown_text(ROOT / "CHANGELOG.md")
    assert "gantry/portfolio_json.py" in entry and "`gantry build` does not use it yet" in " ".join(entry.split())
    assert "docs/worksite-page.md#reading-devagents-portfolio-json" in entry
