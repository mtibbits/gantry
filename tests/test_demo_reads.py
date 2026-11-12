"""The demo build reads nothing outside the sample directory and the package (#43).

The build runs in a child process (`python -P -B`, PYTHONPATH the gantry
root) through tests/demo_reads_runner.py, which installs an audit hook before
gantry is imported. The child's environment and working directory hold a
planted config at every place a default lookup could look. One pure
classifier, `violations`, judges the demo run, the control run over a copy of
the samples, and a table of synthetic controls. Every path is built at run
time.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

import pytest

from gantry.roots import gantry_root, sample_dir
from helpers_config import written_bytes
from test_samples import GANTRY_SAMPLE

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "demo_reads_runner.py"
PACKAGE = ROOT / "gantry"
CHILD_TIMEOUT = 120  # seconds; a measured run takes about 5
LOOKUP_NAMES = ("HOME", "USERPROFILE", "XDG_CONFIG_HOME", "GANTRY_CONFIG", "DA_HOME")
IMPORT_FRAMES = frozenset({"<frozen importlib._bootstrap>", "<frozen importlib._bootstrap_external>"})
PROCESS_EVENTS = frozenset({"os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.startfile", "os.fork",
                            "os.forkpty"})
# Popen, and on Windows the CreateProcess call it makes: both carry the command line and cwd.
SPAWNS = frozenset({"subprocess.Popen", "_winapi.CreateProcess"})
LISTINGS = frozenset({"os.listdir", "os.scandir"})
RUN_GIT = ("wbs_history.py", "run_git")

# Reads Python itself makes for gantry, allowed by name: (event, kind, innermost
# frame's function, innermost gantry frame). `kind` is "descriptor" for an
# integer argument and "devnull" for os.devnull.
BY_NAME = {
    ("open", "descriptor", "fdopen", ("roots.py", "write_confined")):
        "os.fdopen on the descriptor tempfile.mkstemp opened under output_dir raises `open` "
        "with the integer descriptor, not a path; the mkstemp `open` just before it carries "
        "the path and is checked by rule 4",
}
GIT_HELPER_BY_NAME = {
    ("open", "devnull", "_get_devnull", RUN_GIT):
        "run_git passes stdin=subprocess.DEVNULL, so subprocess opens os.devnull (nul on "
        "Windows, /dev/null on POSIX): a device, not a file of any installation",
    ("open", "descriptor", "__init__", RUN_GIT):
        "capture_output: Popen wraps the read ends of its stdout and stderr pipes in file "
        "objects, which raises `open` with each pipe's integer descriptor",
}
ALL_BY_NAME = {**BY_NAME, **GIT_HELPER_BY_NAME}


# --- the one classifier ---------------------------------------------------------------


# Deliberately independent of gantry.roots' own resolution: the spy does not
# trust the code it checks. Cached: no test re-points a link it has resolved.
@lru_cache(maxsize=None)
def canon(path):
    return os.path.normcase(os.path.realpath(path))


def inside(path, folder):
    """`path` is `folder` or lies under it, both resolved and case-normalised;
    False for paths on different drives or a relative and an absolute path."""
    try:
        a, b = canon(path), canon(folder)
        return os.path.commonpath([a, b]) == b
    except ValueError:
        return False


def _path(item, record):
    arg = item["arg"]
    if arg is None and item["event"] in LISTINGS:
        return record["cwd"]
    if not isinstance(arg, str) or arg == os.devnull:
        # A descriptor, or the null device (relative `nul` on Windows, which
        # joined to a planted working directory would read as planted): no
        # path rule applies; the by-name rule decides.
        return None
    return arg if os.path.isabs(arg) else os.path.join(record["cwd"], arg)


def by_name_key(item):
    arg = item["arg"]
    kind = "descriptor" if isinstance(arg, int) else "devnull" if arg == os.devnull else arg
    return (item["event"], kind, item["inner"][1], tuple(item["gantry"][0]))


def _program(argv):
    """The program a Popen ran: argv[0] of a POSIX list, the first token of a
    Windows command line."""
    if isinstance(argv, list):
        first = argv[0] if argv else ""
    else:
        text = (argv or "").lstrip()
        first = text[1:].split('"', 1)[0] if text.startswith('"') else (text.split() or [""])[0]
    name = os.path.basename(first)
    return os.path.normcase(name[:-4] if name.lower().endswith(".exe") else name)


def classify(record, *, allowed, planted, by_name):
    """[(verdict, reason, event)] for every recorded event; verdict "ok" or
    "violation". Rules, first match wins: process events,
    Popen, planted, allowed folders, the import system, by name, else outside."""
    stdlib = record["stdlib"]
    sys_path = {canon(p) for p in record["sys_path"] if p}
    out = []
    git_popen = None  # the latest spawn event, when it was a git-helper Popen
    for item in record["events"]:
        event = item["event"]
        if event == "os.chdir":
            out.append(("violation", "changes directory", item))
            continue
        if event in PROCESS_EVENTS:
            out.append(("violation", "starts a process", item))
            continue
        if event == "subprocess.Popen":
            ok = (list(RUN_GIT) in item["gantry"] and _program(item.get("argv")) == "git"
                  and isinstance(item.get("popen_cwd"), str) and os.path.isabs(item["popen_cwd"])
                  and inside(item["popen_cwd"], sample_dir()))
            git_popen = item if ok else None
            out.append(("ok", "git helper", item) if ok else ("violation", "starts a process", item))
            continue
        if event == "_winapi.CreateProcess":
            # Its command line arrives garbled, so it is the git helper only as
            # the CreateProcess of the latest spawn event, a git-helper Popen.
            ok = (git_popen is not None and item["gantry"] == git_popen["gantry"]
                  and item.get("popen_cwd") == git_popen["popen_cwd"])
            git_popen = None
            out.append(("ok", "git helper", item) if ok else ("violation", "starts a process", item))
            continue
        path = _path(item, record)
        if path is not None:
            if any(inside(path, f) for f in planted):
                out.append(("violation", "planted", item))
                continue
            if any(inside(path, f) for f in allowed):
                out.append(("ok", "allowed folder", item))
                continue
            if item["inner"][0] in IMPORT_FRAMES and (
                    any(inside(path, s) for s in stdlib)
                    or (event in LISTINGS and canon(path) in sys_path)):
                out.append(("ok", "import system", item))
                continue
        key = by_name_key(item)
        if key in by_name:
            out.append(("ok", ("by name", key), item))
            continue
        out.append(("violation", "outside", item))
    return out


def violations(record, **kwargs):
    """[(reason, event)] for every event the classifier does not allow."""
    return [(reason, item) for verdict, reason, item in classify(record, **kwargs) if verdict == "violation"]


def used_by_name(record, **kwargs):
    return {reason[1] for verdict, reason, _ in classify(record, **kwargs)
            if verdict == "ok" and isinstance(reason, tuple)}


def describe(found):
    return "\n".join(f"{reason}: {item['event']} {item['arg']!r} inner={item['inner']} gantry={item['gantry'][:3]}"
                     for reason, item in found)


# --- the child runs ---------------------------------------------------------------------


def child_env(**extra):
    """os.environ minus PYTHON* and the five lookup names; PYTHONPATH the gantry root."""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("PYTHON") and k.upper() not in LOOKUP_NAMES}
    env["PYTHONPATH"] = str(ROOT)
    env.update({k: str(v) for k, v in extra.items()})
    return env


def launch(args, *, cwd, env):
    try:
        return subprocess.run([sys.executable, "-P", "-B", str(RUNNER), *map(str, args)], cwd=cwd, env=env,
                              capture_output=True, timeout=CHILD_TIMEOUT)
    except subprocess.TimeoutExpired:
        pytest.fail("the child did not finish: an audit hook that recurses (U2)", pytrace=False)


def read_record(ran, record):
    if ran.returncode != 0 or not Path(record).is_file():
        pytest.fail(f"the runner exited {ran.returncode}; stderr:\n" + ran.stderr.decode("utf-8", "replace"),
                    pytrace=False)
    return json.loads(Path(record).read_text(encoding="utf-8"))


class Build(NamedTuple):
    code: int
    stdout: bytes
    stderr: bytes
    tree: dict
    record: dict


def build(record, config, *, cwd, env, out):
    """One build: its exit, output, output tree and record."""
    ran = launch([record, ROOT, "build", "--config", Path(config).resolve()], cwd=cwd, env=env)
    data = read_record(ran, record)
    return Build(ran.returncode, ran.stdout, ran.stderr, written_bytes(Path(out)), data)


PLANT_FOLDERS = ("home", "da_home", "env", "xdg", "cwd")
PLANTED_WBS = "# Plan\n- [ ] PLANTED {issue: Issue-1}\n"


def _devagent(config_file):
    """A devagent config naming one site `planted` whose WBS.md holds PLANTED."""
    config_file = Path(config_file)
    planning = config_file.parent / "planted"
    planning.mkdir(parents=True, exist_ok=True)
    (planning / "WBS.md").write_bytes(PLANTED_WBS.encode("utf-8"))
    config_file.write_bytes(b'[project.planted]\ndevdoc_dir = "planted"\n')


def _installation(config_file, devagent_rel):
    config_file = Path(config_file)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_bytes(f'config_version = 1\ndevagent_config = "{devagent_rel}"\noutput_dir = "out"\n'
                            .encode("utf-8"))


def plant(p):
    """The five planted folders under `p`; returns (folders, installation configs)."""
    folders = {name: p / name for name in PLANT_FOLDERS}
    for folder in folders.values():
        folder.mkdir(parents=True)
    _devagent(folders["home"] / ".claude" / "devagent" / "config.toml")  # devagent's default
    _installation(folders["home"] / "gantry.toml", ".claude/devagent/config.toml")
    _devagent(folders["da_home"] / "config.toml")
    _installation(folders["env"] / "gantry.toml", "../da_home/config.toml")
    _devagent(folders["xdg"] / "gantry" / "devagent.toml")
    _installation(folders["xdg"] / "gantry" / "config.toml", "devagent.toml")
    _devagent(folders["cwd"] / "devagent.toml")
    _installation(folders["cwd"] / "gantry.toml", "devagent.toml")
    configs = {"home": folders["home"] / "gantry.toml", "env": folders["env"] / "gantry.toml",
               "xdg": folders["xdg"] / "gantry" / "config.toml", "cwd": folders["cwd"] / "gantry.toml"}
    return folders, configs


def planted_env(folders):
    return child_env(HOME=folders["home"], USERPROFILE=folders["home"], DA_HOME=folders["da_home"],
                     GANTRY_CONFIG=folders["env"] / "gantry.toml", XDG_CONFIG_HOME=folders["xdg"])


def copy_with_history(folder):
    """A copy of the samples, sample history included, without the demo's output."""
    copy = Path(folder) / "samples"
    shutil.copytree(sample_dir(), copy, ignore=lambda d, names: ["out"] if Path(d) == sample_dir() else [])
    return copy


def wbs_files(config_path):
    """The resolved WBS.md files of the config's sites (devagent Issue-118)."""
    from gantry.config import load_config
    from gantry.sites import load_sites

    sites, _ = load_sites(load_config(config_path))
    return {canon(Path(s.planning_dir) / "WBS.md") for s in sites
            if s.planning_dir is not None and (Path(s.planning_dir) / "WBS.md").is_file()}


def wbs_opens(record):
    return {canon(_path(e, record)) for e in record["events"]
            if e["event"] == "open" and _path(e, record) is not None and os.path.basename(e["arg"]) == "WBS.md"}


@pytest.fixture(scope="module")
def demo_runs(tmp_path_factory, built_sample_history):
    """The baseline, planted and control runs.

    Builds into the real samples/out, so one pytest session per checkout at a
    time, as tests/conftest.py's built_sample_history says. The build is not
    deleted between runs."""
    base = tmp_path_factory.mktemp("demo_reads")
    b, p, c = base / "b", base / "p", base / "c"
    for folder in (b / "home", b / "cwd"):
        folder.mkdir(parents=True)
    folders, configs = plant(p)
    out = sample_dir() / "out"
    config = GANTRY_SAMPLE
    empty_home = child_env(HOME=b / "home", USERPROFILE=b / "home")
    baseline = build(base / "baseline.json", config, cwd=b / "cwd", env=empty_home, out=out)
    planted = build(base / "planted.json", config, cwd=folders["cwd"], env=planted_env(folders), out=out)
    copy = copy_with_history(c)
    control = build(base / "control.json", copy / "gantry.sample.toml", cwd=folders["cwd"], env=planted_env(folders),
                    out=copy / "out")
    return {"baseline": baseline, "planted": planted, "control": control, "folders": folders, "configs": configs,
            "copy": copy}


def demo_kwargs(planted=()):
    """The classifier's arguments for a demo-shaped run. The git-helper entries
    are in: when #29 binds the history reader, the tripwire is the only
    assertion that changes."""
    return {"allowed": [sample_dir(), PACKAGE], "planted": list(planted), "by_name": ALL_BY_NAME}


# --- the demo build ---------------------------------------------------------------------


def test_demo_build_reads_only_samples_and_the_package(demo_runs):
    planted = demo_runs["planted"]
    record = planted.record
    assert planted.code == 0
    assert record["hook_errors"] == 0
    assert record["safe_path"] is True and record["dont_write_bytecode"] is True
    kwargs = demo_kwargs(demo_runs["folders"].values())
    found = violations(record, **kwargs)
    assert found == [], describe(found)
    assert set(BY_NAME) <= used_by_name(record, **kwargs)
    expected = wbs_files(GANTRY_SAMPLE)
    assert expected
    assert wbs_opens(record) == expected
    assert any(e["event"] == "open" and _path(e, record) is not None and inside(_path(e, record), PACKAGE)
               and any(g[0] == "roots.py" for g in e["gantry"]) for e in record["events"])


def test_planted_configs_change_nothing(demo_runs):
    baseline, planted = demo_runs["baseline"], demo_runs["planted"]
    # All but the record. Both builds write samples/out uncleared, so the tree
    # alone would not show a page the planted run failed to write; stdout does.
    assert baseline[:4] == planted[:4]
    tree = planted.tree
    assert "index.html" in tree
    assert any(re.fullmatch(r"sites/[^/]+\.html", name) for name in tree)
    for blob in (planted.stdout, planted.stderr, *tree.values()):
        assert b"PLANTED" not in blob and b"planted" not in blob


def test_control_copy_outside_the_sample_directory_is_seen(demo_runs):
    record = demo_runs["control"].record
    assert demo_runs["control"].code == 0
    expected = wbs_files(demo_runs["copy"] / "gantry.sample.toml")
    assert expected
    assert wbs_opens(record) == expected
    found = violations(record, **demo_kwargs(demo_runs["folders"].values()))
    outside = {canon(_path(item, record)) for reason, item in found
               if reason == "outside" and _path(item, record) is not None}
    assert expected <= outside
    assert [item for reason, item in found if reason == "planted"] == [], describe(found)


@pytest.mark.parametrize("name", ["home", "env", "xdg", "cwd"])
def test_each_plant_is_a_working_config(tmp_path, name):
    folders, configs = plant(tmp_path / "p")
    ran = launch([tmp_path / "rec.json", ROOT, "build", "--config", configs[name]], cwd=tmp_path,
                 env=child_env(HOME=tmp_path, USERPROFILE=tmp_path))
    read_record(ran, tmp_path / "rec.json")
    tree = written_bytes(configs[name].parent / "out")
    assert any(b"PLANTED" in blob for blob in tree.values()), sorted(tree)


def test_runner_refuses_a_foreign_gantry(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    ran = launch([tmp_path / "rec.json", empty, "build", "--config", GANTRY_SAMPLE],
                 cwd=tmp_path, env=child_env())
    assert ran.returncode == 3
    assert len(ran.stderr.decode("utf-8", "replace").strip().splitlines()) == 1
    assert not (tmp_path / "rec.json").exists()


def test_real_git_call_record_is_classified(tmp_path, built_sample_history):
    from sample_roles import HISTORY_RECENT

    record_file = tmp_path / "rec.json"
    repo = sample_dir() / "history" / "out" / HISTORY_RECENT
    ran = launch(["--git-probe", record_file, ROOT, repo, gantry_root()], cwd=tmp_path, env=child_env())
    record = read_record(ran, record_file)
    assert record["exit"] == 0 and record["hook_errors"] == 0
    assert len([e for e in record["events"] if e["event"] == "subprocess.Popen"]) == 1
    kwargs = demo_kwargs()
    found = violations(record, **kwargs)
    assert found == [], describe(found)
    assert set(GIT_HELPER_BY_NAME) <= used_by_name(record, **kwargs)
    # Control: the same call run elsewhere is a violation, once per spawn event
    # (Popen, and on Windows its CreateProcess), and nothing else is.
    spawns = [item for item in record["events"] if item["event"] in SPAWNS]
    for item in spawns:
        item["popen_cwd"] = str(tmp_path)
    assert [reason for reason, _ in violations(record, **kwargs)] == ["starts a process"] * len(spawns)


# --- the demo's git reads (AC 3; operator's option (a), 2026-11-12) ------------------------

TRIPWIRE = """the demo build now starts a process: #29 has bound the history reader into the build.
Change this assertion to "at least one subprocess.Popen event"; nothing else: rule 2 of
violations() and the git-helper BY_NAME entries are already proved on a real git call
by test_real_git_call_record_is_classified."""


def test_sample_installation_git_reads_stay_in_the_sample_directory(tmp_path, monkeypatch, built_sample_history):
    """In process, over the sample installation: the demo build itself makes no
    git call until #29 binds the history reader."""
    from gantry import wbs_history
    from gantry.config import load_config
    from gantry.sites import config_roots, load_sites
    from helpers_history import spy_git, spy_repository
    from sample_roles import HISTORY_LESS

    calls = spy_git(monkeypatch)
    given = spy_repository(monkeypatch)
    config = load_config(GANTRY_SAMPLE)
    roots = config_roots(config)
    sites, _ = load_sites(config, roots)
    cache = wbs_history.HistoryCache()
    answers = {s.name: wbs_history.last_state_change(roots, s, config, config.as_of, 4, cache) for s in sites}
    assert sites and len(answers) == len(sites)
    repos = [r for r in given if isinstance(r, wbs_history.Repo)]
    assert len(repos) >= 3
    assert [r.top for r in repos if not inside(r.top, sample_dir())] == []
    history = {n for n, o in config.sites.items() if o.history is not None}
    assert len(history) == 3
    assert {n for n, a in answers.items() if isinstance(a.repo, wbs_history.Repo)} == history
    assert answers[HISTORY_LESS].no_data.text == "no data: in no repository"
    assert calls
    assert [cwd for _, cwd, _ in calls if not inside(cwd, sample_dir())] == []
    assert {canon(ceiling) for _, _, ceiling in calls} == {canon(gantry_root())}
    # Control: `inside` is not always true.
    assert not inside(gantry_root(), sample_dir())
    assert not inside(tmp_path, sample_dir())


def test_demo_build_starts_no_process_yet(demo_runs):
    for run in ("planted", "baseline"):
        record = demo_runs[run].record
        assert [e for e in record["events"] if e["event"] in SPAWNS | PROCESS_EVENTS] == [], TRIPWIRE


# --- the classifier's controls ----------------------------------------------------------


def _ev(event, arg, *, inner=None, gantry=(("roots.py", "open_confined"),), **more):
    inner = [str(PACKAGE / "roots.py"), "open_confined"] if inner is None else list(inner)
    return {"event": event, "arg": arg, "inner": inner, "gantry": [list(g) for g in gantry], **more}


IMPORT_INNER = ("<frozen importlib._bootstrap_external>", "get_data")
GIT_GANTRY = (RUN_GIT, ("wbs_history.py", "last_state_change"))


def _popen(argv, cwd, gantry=GIT_GANTRY):
    return _ev("subprocess.Popen", None, inner=("subprocess.py", "_execute_child"), gantry=gantry, argv=argv,
               popen_cwd=str(cwd))


def _create_process(cwd):
    return _ev("_winapi.CreateProcess", None, inner=("subprocess.py", "_execute_child"), gantry=GIT_GANTRY,
               argv="", popen_cwd=str(cwd))


def _other_drive():
    drive = os.path.splitdrive(str(ROOT))[0].upper()
    letter = "Q" if drive != "Q:" else "R"
    return letter + ":" + os.sep + "elsewhere" + os.sep + "x.toml"


def _rows(tmp):
    """(row id, event, expected reason); `tmp` holds the planted, outside and sys.path folders."""
    stdlib_file = str(Path(sysconfig.get_paths()["stdlib"]) / "os.py")
    planted = str(tmp / "plant" / "gantry.toml")
    outside = str(tmp / "outside" / "x.toml")
    entry = str(tmp / "entry")
    samples = str(sample_dir() / "harbor" / "WBS.md")
    sibling = str(sample_dir().parent / (sample_dir().name + "-x") / "WBS.md")
    flipped = samples.swapcase() if os.name == "nt" else samples.replace(sample_dir().name, sample_dir().name.upper())
    git_posix = ["git", "rev-parse", "--show-toplevel"]
    git_windows = "git rev-parse --show-toplevel"
    harbor_history = sample_dir() / "history" / "out" / "harbor"
    return [
        ("planted-open", _ev("open", planted), "planted"),
        ("planted-import", _ev("open", planted, inner=IMPORT_INNER), "planted"),
        ("outside-open", _ev("open", outside), "outside"),
        ("outside-listdir", _ev("os.listdir", outside), "outside"),
        ("outside-scandir", _ev("os.scandir", outside), "outside"),
        ("stdlib-import", _ev("open", stdlib_file, inner=IMPORT_INNER), "import system"),
        ("stdlib-not-import", _ev("open", stdlib_file), "outside"),
        ("syspath-listing-import", _ev("os.listdir", entry, inner=IMPORT_INNER), "import system"),
        ("syspath-listing-not-import", _ev("os.listdir", entry), "outside"),
        ("syspath-child-import", _ev("open", str(Path(entry) / "mod.py"), inner=IMPORT_INNER), "outside"),
        ("samples", _ev("open", samples), "allowed folder"),
        ("package", _ev("open", str(PACKAGE / "templates" / "x.html")), "allowed folder"),
        ("sibling-of-samples", _ev("open", sibling), "outside"),
        ("case-changed-samples", _ev("open", flipped), "allowed folder" if os.name == "nt" else "outside"),
        ("descriptor-write-confined", _ev("open", 3, inner=("<frozen os>", "fdopen"),
                                          gantry=(("roots.py", "write_confined"),)), "by name"),
        ("descriptor-other-frame", _ev("open", 3, inner=("<frozen os>", "fdopen"),
                                       gantry=(("build.py", "write_page"),)), "outside"),
        # On POSIX a drive-letter path is relative: it joins the planted working directory.
        ("other-drive", _ev("open", _other_drive()), "outside" if os.name == "nt" else "planted"),
        ("integer-arg-listing", _ev("os.listdir", 5), "outside"),
        ("os-system", _ev("os.system", "echo"), "starts a process"),
        ("popen-git-posix", _popen(git_posix, harbor_history), "git helper"),
        ("popen-git-windows", _popen(git_windows, harbor_history), "git helper"),
        ("popen-other-program", _popen(["python", "-c", "0"], harbor_history), "starts a process"),
        ("popen-git-cwd-outside", _popen(git_posix, tmp / "outside"), "starts a process"),
        ("popen-git-no-run-git", _popen(git_posix, harbor_history, gantry=(("build.py", "write_page"),)),
         "starts a process"),
        # Relative: it would resolve against the pytest process's directory.
        ("popen-git-relative-cwd", _popen(git_posix, os.path.relpath(harbor_history, ROOT)), "starts a process"),
        ("chdir", _ev("os.chdir", outside), "changes directory"),
        ("devnull-run-git", _ev("open", os.devnull, inner=("subprocess.py", "_get_devnull"), gantry=GIT_GANTRY),
         "by name"),
        ("devnull-other-frame", _ev("open", os.devnull, inner=("subprocess.py", "_get_devnull")), "outside"),
        ("os-fork", _ev("os.fork", None), "starts a process"),
        ("os-forkpty", _ev("os.forkpty", None), "starts a process"),
        ("winapi-create-process", _ev("_winapi.CreateProcess", None, argv="git rev-parse --show-toplevel",
                                      popen_cwd=str(harbor_history)), "starts a process"),
        # Python garbles CreateProcess's command line in the audit event, so it is
        # judged by the git-helper Popen it follows, never alone.
        ("winapi-create-process-alone", _create_process(harbor_history), "starts a process"),
        ("winapi-create-process-after-git-popen",
         [_popen(git_windows, harbor_history), _create_process(harbor_history)], ["git helper", "git helper"]),
        ("winapi-create-process-other-cwd",
         [_popen(git_windows, harbor_history), _create_process(tmp / "outside")], ["git helper", "starts a process"]),
    ]


# The ids touch no file, so a placeholder folder names them at collection.
ROW_IDS = [row for row, _, _ in _rows(Path("tmp"))]


def test_runner_records_every_event_the_classifier_judges():
    import demo_reads_runner

    judged = PROCESS_EVENTS | SPAWNS | LISTINGS | {"open", "os.chdir"}
    assert demo_reads_runner.EVENTS == judged


def _synthetic(tmp, events):
    # The working directory is planted, as in the planted demo run.
    return {"cwd": str(tmp / "plant"), "stdlib": [sysconfig.get_paths()["stdlib"], sysconfig.get_paths()["platstdlib"]],
            "sys_path": [str(tmp / "entry")], "events": events if isinstance(events, list) else [events]}


OK_REASONS = frozenset({"allowed folder", "import system", "by name", "git helper"})


@pytest.mark.parametrize("row", ROW_IDS)
def test_classifier_controls(tmp_path, row):
    for folder in ("plant", "outside", "entry"):
        (tmp_path / folder).mkdir()
    (event, expected), = [(e, x) for r, e, x in _rows(tmp_path) if r == row]
    got = classify(_synthetic(tmp_path, event), **demo_kwargs([tmp_path / "plant"]))
    expected = expected if isinstance(expected, list) else [expected]
    assert [reason if isinstance(reason, str) else reason[0] for _, reason, _ in got] == expected, got
    assert [verdict for verdict, _, _ in got] == ["ok" if x in OK_REASONS else "violation" for x in expected]


# --- no module names a default-lookup variable --------------------------------------------

LOOKUP_RE = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(LOOKUP_NAMES) + r")(?![A-Za-z0-9_])")


def lookup_name_hits(folder):
    """{relative path: [names]} for every file under `folder` naming a lookup variable."""
    folder = Path(folder)
    hits = {}
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or "__pycache__" in path.relative_to(folder).parts:
            continue
        found = LOOKUP_RE.findall(path.read_bytes().decode("latin-1"))
        if found:
            hits[path.relative_to(folder).as_posix()] = found
    return hits


def test_no_gantry_module_names_a_default_lookup_variable():
    assert len([p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts]) >= 20
    assert lookup_name_hits(PACKAGE) == {}


@pytest.mark.parametrize("name", LOOKUP_NAMES)
@pytest.mark.parametrize("form", ["literal", "subscript", "comment"])
def test_env_name_scan_flags_each_name(tmp_path, name, form):
    text = {"literal": f'key = "{name}"\n', "subscript": f"value = os.environ[{name!r}]\n",
            "comment": f"# reads {name} first\n"}[form]
    (tmp_path / "mod.py").write_text(text, encoding="utf-8")
    assert lookup_name_hits(tmp_path) == {"mod.py": [name]}


def test_env_name_scan_ignores_longer_words(tmp_path):
    (tmp_path / "mod.py").write_text("HOMEPAGE = 1\nhome = 2\nMY_HOME_DIR = 3\n", encoding="utf-8")
    assert lookup_name_hits(tmp_path) == {}


# --- the documentation ------------------------------------------------------------------


def test_config_doc_states_the_git_history_rule():
    from gantry import wbs_history

    text = (ROOT / "docs" / "config.md").read_bytes().decode("utf-8")
    start = text.index("## Where a build may read and write")
    end = text.index("\n## ", start + 3)
    section = " ".join(text[start:end].split())
    assert "**Git reads a history directory's repository.**" in section
    assert "no data: " + wbs_history.OUTSIDE_ROOTS in section
    assert "git never receives the path" in section
