"""devagent's portfolio JSON as test data (#83): the tool that makes it, the gate and the fixture.

tests/fixtures/portfolio/written.json is the pinned devagent release's own
document over WBS texts the stopgap parser is already tested with. The
ungated tests read the committed bytes, so they run on every leg. The gated
tests run the pinned aggregator from a devagent checkout (GANTRY_DEVAGENT_DIR,
tests/helpers_devagent.py): they skip on Windows and locally without the
variable, and fail under CI without it.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import commit_check
import helpers_devagent as hd
import helpers_portfolio as hp
import sample_portfolio as sp
from helpers_config import sample_projects, written_bytes
from sample_roles import NO_WBS, READABLE, UNREADABLE
from test_devagent_contract import fake_checkout
from test_samples import ROOT, SAMPLES

# U1: what devagent says of the sample site whose WBS.md is not UTF-8.
MARSH_REASON = "WBS.md could not be read: UnicodeDecodeError: invalid continuation byte at byte 88"
# The fixture's totals, as the pinned aggregator wrote them.
FIXTURE_NODES = 125
FIXTURE_ENTRIES = 54
# The 13 sources the stopgap's tests already read; `edges` is this issue's own.
EARLIER_SOURCES = 13


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


def test_real_sample_home_passes_the_guard():
    # .gitignore's out/ rule covers samples/devagent/out, and nothing in it is tracked.
    sp.guard_out(SAMPLES)


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
    document = (json.dumps({"schema_version": "1.1", "generated_at": sp.FIXED_TIME, "projects": [],
                            "parse_report": []}) + "\n").encode("utf-8")
    monkeypatch.setattr(sp, "fixture_document", lambda devagent: document)
    argv = ["--fixtures", "--devagent", str(checkout)]
    assert sp.main(argv + ["--check"]) == 1
    assert capsys.readouterr().out.splitlines() == ["sample_portfolio: written.json: missing",
                                                    "sample_portfolio: provenance.json: missing"]
    assert not (tmp_path / "fx").exists()
    assert sp.main(argv) == 0
    assert sp.WRITTEN.read_bytes() == document
    made = json.loads(sp.PROVENANCE.read_bytes().decode("utf-8"))
    assert list(made) == ["pin", "devagent_version", "schema_version", "generated_at", "sources"]
    capsys.readouterr()
    assert sp.main(argv + ["--check"]) == 0
    assert capsys.readouterr().out == "sample_portfolio: 2 fixture files match\n"
    sp.WRITTEN.write_bytes(document + b"\n")
    assert sp.main(argv + ["--check"]) == 1
    assert capsys.readouterr().out == "sample_portfolio: written.json: differs from what devagent writes\n"


# --- the written fixture (Task 2) -------------------------------------------------------------


def test_fixture_sources_are_the_stopgaps_own(tmp_path):
    assert list(sp.FIXTURE_SOURCES) == ["cycle", "empty", "fences", "ids", "nesting", "not_utf8", "page", "priority",
                                        "refs", "rows", "states", "est", "h_values", "edges"]
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
    assert len(doc["projects"]) == 14
    assert sum(len(p["nodes"]) for p in doc["projects"]) == FIXTURE_NODES
    assert len(doc["parse_report"]) == FIXTURE_ENTRIES
    # Before `edges` the 13 earlier sources gave 122 nodes and 47 entries.
    assert sum(len(p["nodes"]) for p in doc["projects"] if p["project"] != "edges") == 122
    assert sum(e["project"] != "edges" for e in doc["parse_report"]) == 47
    assert doc["generated_at"] == sp.FIXED_TIME == "2026-01-12T09:30:00Z"
    by_name = {p["project"]: p for p in doc["projects"]}
    assert by_name["not_utf8"]["status"] == "unreadable"
    assert by_name["not_utf8"]["status_reason"].startswith("WBS.md could not be read: UnicodeDecodeError")
    assert {p["status"] for p in doc["projects"] if p["project"] != "not_utf8"} == {"read"}
    made = json.loads(sp.PROVENANCE.read_bytes().decode("utf-8"))
    assert list(made) == ["pin", "devagent_version", "schema_version", "generated_at", "sources"]
    assert (made["pin"], made["devagent_version"]) == (tag, version)
    assert (made["schema_version"], made["generated_at"]) == (doc["schema_version"], sp.FIXED_TIME)
    assert made["sources"] == dict(sp.FIXTURE_SOURCES)
    earlier = [s for s in made["sources"].values() if not s.startswith("tests/fixtures/portfolio/")]
    assert len(earlier) == EARLIER_SOURCES and all((ROOT / s).is_file() for s in made["sources"].values())
    for path in (sp.WRITTEN, sp.PROVENANCE):
        assert commit_check.path_form_hits(path.read_bytes().decode("utf-8")) == [], path.name


# --- gated: the pinned aggregator -------------------------------------------------------------


def test_written_fixture_regenerates():
    checkout = hd.require_devagent()
    document = sp.fixture_document(checkout)
    assert document == sp.WRITTEN.read_bytes()
    assert sp.provenance(checkout, document) == sp.PROVENANCE.read_bytes()
    assert sp.main(["--fixtures", "--check", "--devagent", str(checkout)]) == 0


def test_written_fixture_passes_devagents_checker():
    checkout = hd.require_devagent()
    checker = checkout / "scripts" / "lib" / "portfolio-check.py"
    ran = subprocess.run([sys.executable, str(checker), str(sp.WRITTEN)], capture_output=True)
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
