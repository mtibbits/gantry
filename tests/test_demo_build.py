"""tools/demo_build.py: the demo build, its clean guard and its steps (#45, #12).

The guard and step tests run against a throwaway git tree with fake steps;
the devagent fetch tests replace `git clone` with a local fake (`fetches`);
the clone tests build the real demo from committed HEAD in a child process
under a frozen clock (tests/demo_build_runner.py, tests/clock.py), with the
devagent checkout GANTRY_DEVAGENT_DIR names (hd.require_devagent).
"""

import json as _json
import os
import shutil
import subprocess as _subprocess
from pathlib import Path

import pytest

import commit_check
import demo_build
import helpers_devagent as hd
from devagent_record_fixtures import read_pin
from helpers_history import git as _hgit
from helpers_history import make_repo, write
from helpers_links import link_dir

# The real preconditions: the tree fixture replaces them, so the guard tests run on every host.
PRECONDITIONS = demo_build.preconditions

CONFIG = "samples/gantry.sample.toml"
SENTINEL = "keep.txt"


def config_bytes(output_dir='"out"'):
    line = "" if output_dir is None else f"output_dir = {output_dir}\n"
    return f"config_version = 2\n{line}as_of = 2026-01-20\n".encode("utf-8")


def plant(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / SENTINEL).write_bytes(b"sentinel\n")
    return folder / SENTINEL


@pytest.fixture
def tree(tmp_path, git_ready, monkeypatch):
    root = make_repo(tmp_path / "tree")
    write(root, ".gitignore", "out/\n")
    write(root, CONFIG, config_bytes())
    plant(root / "samples" / "out")
    monkeypatch.setattr(demo_build, "preconditions", lambda *args: None)
    return root


def step(name, calls, result=0, check=None):
    def run(root):
        calls.append(name)
        if check is not None:
            check(root)
        if isinstance(result, BaseException):
            raise result
        return result

    return demo_build.Step(name, run)


def last_line(text):
    lines = text.splitlines()
    return lines[-1] if lines else ""


def test_run_cleans_then_steps_then_prints_folder(tree, monkeypatch, capsys):
    seen, calls = [], []

    def look(root):
        out = root / "samples" / "out"
        seen.append((out.is_dir(), sorted(p.name for p in out.iterdir()) if out.is_dir() else None))

    monkeypatch.setattr(demo_build, "STEPS", (step("one", calls, check=look), step("two", calls, None, look)))
    assert demo_build.run(tree) == 0
    assert calls == ["one", "two"]
    assert seen == [(True, []), (True, [])]
    out = capsys.readouterr()
    assert last_line(out.out) == "samples/out"
    assert out.err == ""


def link_out(root):
    target = plant(root.parent / "outside")
    shutil.rmtree(root / "samples" / "out")
    link_dir(target.parent, root / "samples" / "out")
    return target


def link_in(root):
    target = plant(root / "samples" / "history" / "out")
    shutil.rmtree(root / "samples" / "out")
    link_dir(target.parent, root / "samples" / "out")
    return target


def no_git(root):
    from sample_history import _delete

    _delete(root / ".git")
    return root / "samples" / "out" / SENTINEL


def set_output(value, where):
    def edit(root):
        write(root, CONFIG, config_bytes(value))
        return plant(root / where)

    return edit


def clean_fails(root, monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(shutil, "rmtree", refuse)
    return root / "samples" / "out" / SENTINEL


def tracked(root):
    from helpers_history import git

    sentinel = root / "samples" / "out" / SENTINEL
    git(root, "add", "-f", "--", f"samples/out/{SENTINEL}")
    return sentinel


REFUSALS = {
    "outside": (set_output('"../elsewhere"', "elsewhere"), "the demo output folder is not inside samples/"),
    "is-samples": (set_output('"."', "samples"), "the demo output folder is not inside samples/"),
    "not-ignored": (set_output('"built"', "samples/built"), "git does not ignore the demo output folder"),
    "no-git": (no_git, "not a gantry checkout"),
    "no-config": (set_output(None, "samples/out"), "cannot read output_dir from samples/gantry.sample.toml"),
    "link-out": (link_out, "the demo output folder is a link"),
    "link-in": (link_in, "the demo output folder is a link"),
    "clean-fails": (None, "cannot clean samples/out: Permission denied"),
    "tracked": (tracked, "git tracks a file in the demo output folder"),
}


@pytest.mark.parametrize("case", list(REFUSALS))
def test_refusals(case, tree, monkeypatch, capsys):
    edit, message = REFUSALS[case]
    sentinel = clean_fails(tree, monkeypatch) if edit is None else edit(tree)
    assert sentinel.is_file()
    calls = []
    monkeypatch.setattr(demo_build, "STEPS", (step("never", calls),))
    assert demo_build.run(tree) == 1
    out = capsys.readouterr()
    assert out.err.splitlines() == [f"demo_build: {message}"]
    assert out.out == ""
    assert calls == []
    assert sentinel.is_file(), "the guard deleted the folder it refused"
    assert str(tree.parent) not in out.err


def test_stops_at_first_failure(tree, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(demo_build, "STEPS", (step("ok", calls), step("fail", calls, 7), step("never", calls)))
    assert demo_build.run(tree) == 1
    out = capsys.readouterr()
    assert out.err.splitlines()[-1] == "demo_build: step fail failed (exit 7)"
    assert calls == ["ok", "fail"]
    assert last_line(out.out) != "samples/out"


def test_step_exception_is_a_failure(tree, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(demo_build, "STEPS", (step("boom", calls, ValueError("bad")), step("never", calls)))
    assert demo_build.run(tree) == 1
    err = capsys.readouterr().err.splitlines()
    assert err[0] == "demo_build: step boom failed (ValueError)"
    assert err[1].startswith("Traceback")
    assert calls == ["boom"]


@pytest.mark.parametrize("code, rc, line", [
    (0, 0, None),
    (None, 0, None),
    (4, 1, "demo_build: step exits failed (exit 4)"),
    ("why it stopped", 1, "why it stopped"),
])
def test_step_system_exit(code, rc, line, tree, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(demo_build, "STEPS", (step("exits", calls, SystemExit(code)),))
    assert demo_build.run(tree) == rc
    out = capsys.readouterr()
    if line is None:
        assert last_line(out.out) == "samples/out"
        assert out.err == ""
    else:
        assert line in out.err.splitlines()
        assert out.out == ""


def test_main_takes_no_arguments(monkeypatch):
    monkeypatch.setattr(demo_build, "run", lambda root: pytest.fail("run was called"))
    with pytest.raises(SystemExit) as raised:
        demo_build.main(["x"])
    assert raised.value.code == 2


def test_steps_are_history_devagent_portfolio_build():
    assert [s.name for s in demo_build.STEPS] == ["sample history", "devagent", "sample portfolio", "build"]
    assert demo_build.Step._fields == ("name", "run")


# --- the preconditions and the devagent fetch (#12) ---------------------------------------

TAG = read_pin()
VERSION = TAG.removeprefix("devagent--v")
CLONE_ARGS = ("clone", "-q", "--depth", "1", "--branch", TAG, demo_build.REPO, demo_build.FETCH)


def checkout_at(folder, version=VERSION):
    """A devagent-shaped git checkout at `version`, committed at a fixed time: its HEAD."""
    make_repo(folder)
    write(folder, ".claude-plugin/plugin.json", _json.dumps({"name": "devagent", "version": version}) + "\n")
    write(folder, "scripts/portfolio.sh", "exit 0\n")
    _hgit(folder, "add", "-A")
    _hgit(folder, "commit", "-q", "-m", "release", when="2026-01-12T09:30:00+00:00")
    return _hgit(folder, "rev-parse", "HEAD").strip()


@pytest.fixture
def fetches(tree, tmp_path, monkeypatch):
    """`git clone` replaced by a copy of a local fake checkout; the fixture's state:
    `calls` (each clone's arguments), `commit` (what recorded_commit returns),
    `head` (the fake's HEAD), `fail` (a clone exit code to return instead)."""
    monkeypatch.delenv(demo_build.VARIABLE, raising=False)
    template = tmp_path / "template"
    state = {"calls": [], "head": checkout_at(template), "fail": None}
    state["commit"] = state["head"]
    real = demo_build._git

    def fake(root, *args):
        if args[:1] == ("clone",):
            state["calls"].append(args)
            if state["fail"] is not None:
                return _subprocess.CompletedProcess(args, state["fail"], b"", b"fatal: " + str(tmp_path).encode())
            shutil.copytree(template, Path(root) / args[-1])
            return _subprocess.CompletedProcess(args, 0, b"", b"")
        return real(root, *args)

    monkeypatch.setattr(demo_build, "_git", fake)
    monkeypatch.setattr(demo_build, "recorded_commit", lambda root: state["commit"])
    return state


def test_windows_is_refused_before_clean_or_fetch(tree, fetches, monkeypatch, capsys):
    monkeypatch.setattr(demo_build, "preconditions", PRECONDITIONS)
    sentinel = tree / "samples" / "out" / SENTINEL
    calls = []
    monkeypatch.setattr(demo_build, "STEPS", (step("never", calls),))
    assert demo_build.run(tree, os_name="nt") == 1
    err = capsys.readouterr().err
    assert err.splitlines() == ["demo_build: the sample portfolio step needs devagent's own toolchain (bash and GNU "
                                "coreutils), which Windows does not provide; see docs/demo.md"]
    assert hd.TOOLCHAIN in err
    assert sentinel.is_file() and calls == [] and fetches["calls"] == []


def test_missing_bash_stops_before_clean_and_fetch(tree, fetches, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(demo_build, "preconditions", PRECONDITIONS)
    empty = tmp_path / "no-programs"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    sentinel = tree / "samples" / "out" / SENTINEL
    calls = []
    monkeypatch.setattr(demo_build, "STEPS", (step("never", calls),))
    assert demo_build.run(tree, os_name="posix") == 1
    assert capsys.readouterr().err.splitlines() == [f"demo_build: {demo_build.NO_BASH}"]
    assert sentinel.is_file() and calls == [] and fetches["calls"] == []


def test_checkout_variable_is_used_and_nothing_is_fetched(tree, fetches, tmp_path, monkeypatch):
    import sample_portfolio

    named = tmp_path / "named-checkout"
    monkeypatch.setenv(demo_build.VARIABLE, str(named))
    assert demo_build.devagent_dir(tree, os.environ) == named
    assert demo_build._devagent(tree) == 0
    seen = []
    monkeypatch.setattr(sample_portfolio, "main", lambda argv: seen.append(argv) or 0)
    assert demo_build._sample_portfolio(tree) == 0
    assert seen == [["--devagent", str(named), "--samples", str(tree / "samples")]]
    assert fetches["calls"] == [] and not (tree / demo_build.FETCH).exists()


def test_fetch_clones_the_pinned_tag_into_the_ignored_folder(tree, fetches):
    assert demo_build.devagent_dir(tree, {}) == tree / demo_build.FETCH
    assert demo_build._devagent(tree) == 0
    assert fetches["calls"] == [CLONE_ARGS]
    assert (tree / demo_build.FETCH / ".claude-plugin" / "plugin.json").is_file()
    # A second run reuses the checkout: no fetch.
    assert demo_build._devagent(tree) == 0
    assert fetches["calls"] == [CLONE_ARGS]


def test_unignored_folder_refuses_before_first_clone(tree, fetches, monkeypatch, capsys):
    monkeypatch.setattr(demo_build, "preconditions", PRECONDITIONS)
    write(tree, ".gitignore", "samples/out/\n")
    sentinel = tree / "samples" / "out" / SENTINEL
    calls = []
    monkeypatch.setattr(demo_build, "STEPS", (step("never", calls),))
    assert demo_build.run(tree, os_name="posix") == 1
    assert capsys.readouterr().err.splitlines() == [
        "demo_build: git does not ignore out/devagent, the devagent fetch folder"]
    assert sentinel.is_file() and calls == [] and fetches["calls"] == []


def test_dirty_reused_checkout_is_refused(tree, fetches):
    assert demo_build._devagent(tree) == 0
    changed = write(tree / demo_build.FETCH, "scripts/portfolio.sh", "echo edited\n")
    with pytest.raises(demo_build.Refused, match="out/devagent holds a change; it is not used or deleted"):
        demo_build._devagent(tree)
    assert changed.read_bytes() == b"echo edited\n"
    assert fetches["calls"] == [CLONE_ARGS]


def test_fetched_commit_mismatch_fails_closed(tree, fetches):
    fetches["commit"] = "0" * 40
    with pytest.raises(demo_build.Refused) as refused:
        demo_build._devagent(tree)
    assert str(refused.value) == f"{TAG} is at {fetches['head']}; the fixtures were written at {'0' * 40}"


def test_stale_fetch_folder_is_replaced_only_under_the_guard(tree, fetches, tmp_path):
    old = tree / demo_build.FETCH
    checkout_at(old, version="0.0.1")
    assert demo_build._devagent(tree) == 0
    assert fetches["calls"] == [CLONE_ARGS]
    assert _hgit(old, "rev-parse", "HEAD").strip() == fetches["head"]
    # A stale folder that is a link is never deleted, nor what it points at.
    shutil.rmtree(old)
    outside = tmp_path / "elsewhere"
    checkout_at(outside, version="0.0.1")
    link_dir(outside, old)
    with pytest.raises(demo_build.Refused, match="out/devagent is, or is under, a link; it is not deleted"):
        demo_build._devagent(tree)
    assert (outside / ".claude-plugin" / "plugin.json").is_file()
    assert fetches["calls"] == [CLONE_ARGS]


def test_failed_fetch_names_no_path(tree, fetches, tmp_path, monkeypatch, capsys):
    fetches["fail"] = 128
    calls = []
    monkeypatch.setattr(demo_build, "STEPS", (demo_build.Step("devagent", demo_build._devagent), step("never", calls)))
    assert demo_build.run(tree) == 1
    err = capsys.readouterr().err
    assert err.splitlines() == [f"demo_build: cannot fetch {TAG} from https://github.com/mtibbits/devagent (git exit 128)",
                                "demo_build: step devagent failed"]
    assert str(tmp_path) not in err and calls == []


def test_commit_check_probes_out_devagent():
    assert "out/devagent" in commit_check.FIXED_PROBE_DIRS


# --- the frozen clock and the clone runs --------------------------------------------

import json
import subprocess
import sys
import tomllib
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser

from helpers_config import written_bytes
from helpers_demo import ROOT, child_env, clean_clone, run_child, stderr_of
from helpers_history import require_git
from sample_roles import NOT_BUILT

PROBE_DAY = date(2031, 5, 6)
DAY_ONE, DAY_TWO, DAY_THREE = date(2031, 5, 6), date(2032, 5, 6), date(2033, 9, 1)


def test_frozen_clock_probe():
    ran = run_child(ROOT, PROBE_DAY, "probe")
    assert ran.returncode == 0, stderr_of(ran)
    values = json.loads(ran.stdout)
    assert values["date.today"] == "2031-05-06"
    assert values["datetime.now"] == values["datetime.today"] == values["datetime.utcnow"] == "2031-05-06T12:00:00"
    assert values["datetime.now(utc)"] == "2031-05-06T12:00:00+00:00"
    assert values["datetime.now(-5)"] == "2031-05-06T07:00:00-05:00"
    assert values["time.time"] == datetime(2031, 5, 6, 12, tzinfo=timezone.utc).timestamp()
    assert values["isinstance"] and all(values["isinstance"].values()), values["isinstance"]
    assert values["restored"] is True


def test_frozen_clock_refuses_after_gantry():
    script = "\n".join(["import gantry", "from clock import frozen_clock", "from datetime import date",
                        "try:", "    frozen_clock(date(2031, 5, 6)).__enter__()",
                        "except RuntimeError as exc:", "    print(exc)", ""])
    env = child_env(ROOT)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + str(ROOT / "tests")
    ran = subprocess.run([sys.executable, "-P", "-B", "-c", script], cwd=ROOT, env=env, capture_output=True,
                         timeout=60)
    assert ran.returncode == 0, stderr_of(ran)
    assert ran.stdout.decode("utf-8").strip() == "frozen_clock: gantry or tomllib is already imported"


def test_runner_refuses_wrong_root(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    ran = run_child(empty, DAY_ONE, "main")
    assert ran.returncode == 3
    assert stderr_of(ran).splitlines() == ["demo_build_runner: gantry would not be imported from GANTRY_ROOT"]
    assert list(empty.iterdir()) == []


def sample_config(clone):
    return tomllib.loads((clone / "samples" / "gantry.sample.toml").read_bytes().decode("utf-8"))


class _Stamps(HTMLParser):
    """The text of every element whose class list holds `stamp`, children's text included."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stamps, self.inside = [], None  # inside: [tag, nesting of that tag]

    def handle_starttag(self, tag, attrs):
        if self.inside is not None:
            self.inside[1] += tag == self.inside[0]
        elif "stamp" in (dict(attrs).get("class") or "").split():
            self.inside = [tag, 1]
            self.stamps.append("")

    def handle_endtag(self, tag):
        if self.inside is not None and tag == self.inside[0]:
            self.inside[1] -= 1
            if not self.inside[1]:
                self.inside = None

    def handle_data(self, data):
        if self.inside is not None:
            self.stamps[-1] += data


def stamps(data):
    parser = _Stamps()
    parser.feed(data.decode("utf-8"))
    return [s.strip() for s in parser.stamps]


@pytest.fixture(scope="module")
def two_days(tmp_path_factory):
    """The clone and the two `main` runs, a year apart; each run's aggregator writes the sample document again."""
    hd.require_devagent()
    require_git()
    clone = clean_clone(tmp_path_factory.mktemp("demo"))
    config = sample_config(clone)
    out = clone / "samples" / config["output_dir"]
    runs = []
    for day in (DAY_ONE, DAY_TWO):
        ran = run_child(clone, day, "main")
        runs.append((ran, written_bytes(out) if out.is_dir() else {}))
    return clone, config, runs


def test_two_days_byte_identical(two_days):
    clone, config, runs = two_days
    as_of = config["as_of"]
    assert DAY_ONE > as_of and DAY_TWO - DAY_ONE >= timedelta(days=365)
    for ran, _ in runs:
        assert ran.returncode == 0, stderr_of(ran)
        assert ran.stdout.decode("utf-8").splitlines()[-1] == f"samples/{config['output_dir']}"
    (_, first), (_, second) = runs
    # The identity covers the thieves' row (#29): one neglected-work figure per built panel rendered (a site
    # serving two requirements is rendered twice); each built panel opens with its linked name.
    strategic_page = first["strategic.html"]
    assert strategic_page.count(b'data-figure="thief-neglect"') == strategic_page.count(b'<a class="name"') >= 4
    assert first and first == second
    devagent = tomllib.loads((clone / "samples" / "devagent" / "config.toml").read_bytes().decode("utf-8"))
    sites = set(devagent["project"]) - NOT_BUILT
    assert set(first) == ({"index.html", "strategic.html", "week.html", "week-plan.json"}
                          | {f"sites/{s}.html" for s in sites})
    for name, data in first.items():
        assert b" UTC" not in data, name
        if name == "week-plan.json":
            assert json.loads(data.decode("utf-8"))["as_of"] == str(as_of)
        else:
            assert stamps(data) == [f"sample data as of {as_of}"], name


def test_runner_last_line_is_lf(two_days):
    _, config, runs = two_days
    out = runs[0][0].stdout
    assert out.endswith(f"samples/{config['output_dir']}\n".encode("utf-8"))
    assert b"\r" not in out.splitlines(keepends=True)[-1]


def test_control_run_stamps_injected_day(two_days):
    clone, config, _ = two_days
    sample = clone / "samples" / "gantry.sample.toml"
    before = sample.read_bytes()
    after = b"".join(line for line in before.splitlines(keepends=True) if not line.startswith(b"as_of"))
    assert after != before
    sample.write_bytes(after)
    assert "as_of" not in tomllib.loads(after.decode("utf-8"))
    ran = run_child(clone, DAY_THREE, "build-step")
    assert ran.returncode == 0, stderr_of(ran)
    pages = {k: v for k, v in written_bytes(clone / "samples" / config["output_dir"]).items() if k.endswith(".html")}
    assert len(pages) >= 5
    for name, data in pages.items():
        assert stamps(data) == [f"sample data as of {DAY_THREE}"], name
        assert f"sample data as of {config['as_of']}" not in stamps(data), name


def test_steps_import_without_script_folder_on_path():
    """`python -P tools/demo_build.py` leaves tools/ off sys.path; the history step must still import."""
    code = ("import importlib.util, sys; spec = importlib.util.spec_from_file_location('demo_build', sys.argv[1]); "
            "spec.loader.exec_module(importlib.util.module_from_spec(spec)); import sample_history")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    ran = subprocess.run([sys.executable, "-P", "-B", "-c", code, str(ROOT / "tools" / "demo_build.py")],
                         cwd=ROOT.parent, env=env, capture_output=True, timeout=60)
    assert ran.returncode == 0, ran.stderr.decode("utf-8", "replace")


# --- final review fixes (#12) -------------------------------------------------------------


def test_file_at_the_fetch_folder_is_refused_naming_no_path(tree, fetches, tmp_path, capsys, monkeypatch):
    """A file where out/devagent goes is refused in one line, never a traceback with a path."""
    write(tree, demo_build.FETCH, "not a checkout\n")
    calls = []
    monkeypatch.setattr(demo_build, "STEPS", (demo_build.Step("devagent", demo_build._devagent), step("never", calls)))
    assert demo_build.run(tree) == 1
    err = capsys.readouterr().err
    assert err.splitlines() == ["demo_build: out/devagent is not a folder; remove it to fetch devagent",
                                "demo_build: step devagent failed"]
    assert str(tmp_path) not in err and calls == [] and fetches["calls"] == []
    assert (tree / demo_build.FETCH).read_bytes() == b"not a checkout\n"


def test_changed_checkout_at_another_commit_is_refused_not_deleted(tree, fetches):
    """A checkout holding a change is never deleted, whatever version it is at."""
    old = tree / demo_build.FETCH
    checkout_at(old, version="0.0.1")
    edited = write(old, "scripts/portfolio.sh", "echo mine\n")
    with pytest.raises(demo_build.Refused, match="out/devagent holds a change; it is not used or deleted"):
        demo_build._devagent(tree)
    assert edited.read_bytes() == b"echo mine\n" and fetches["calls"] == []


def test_clone_never_prompts_and_a_missing_git_is_named(tree, monkeypatch):
    seen = []

    def run(argv, **kwargs):
        seen.append(kwargs["env"])
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(demo_build.subprocess, "run", run)
    # _run_git is the git runner itself; tests/conftest.py guards the _git name the steps call.
    with pytest.raises(demo_build.Refused) as refused:
        demo_build._run_git(tree, "clone", "-q", demo_build.REPO, demo_build.FETCH)
    assert str(refused.value) == "cannot run git (clone)"
    assert seen[0]["GIT_TERMINAL_PROMPT"] == "0"
    with pytest.raises(demo_build.Refused, match="cannot ask git whether the demo output folder is ignored"):
        demo_build._run_git(tree, "check-ignore", "-q", "x")
