"""tools/demo_build.py: the demo build, its clean guard and its steps (#45).

The guard and step tests run against a throwaway git tree with fake steps;
the clone tests build the real demo from committed HEAD in a child process
under a frozen clock (tests/demo_build_runner.py, tests/clock.py).
"""

import shutil

import pytest

import demo_build
from helpers_history import make_repo, write
from helpers_links import link_dir

CONFIG = "samples/gantry.sample.toml"
SENTINEL = "keep.txt"


def config_bytes(output_dir='"out"'):
    line = "" if output_dir is None else f"output_dir = {output_dir}\n"
    return f"config_version = 1\n{line}as_of = 2026-01-20\n".encode("utf-8")


def plant(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / SENTINEL).write_bytes(b"sentinel\n")
    return folder / SENTINEL


@pytest.fixture
def tree(tmp_path, git_ready):
    root = make_repo(tmp_path / "tree")
    write(root, ".gitignore", "out/\n")
    write(root, CONFIG, config_bytes())
    plant(root / "samples" / "out")
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


REFUSALS = {
    "outside": (set_output('"../elsewhere"', "elsewhere"), "the demo output folder is not inside samples/"),
    "is-samples": (set_output('"."', "samples"), "the demo output folder is not inside samples/"),
    "not-ignored": (set_output('"built"', "samples/built"), "git does not ignore the demo output folder"),
    "no-git": (no_git, "not a gantry checkout"),
    "no-config": (set_output(None, "samples/out"), "cannot read output_dir from samples/gantry.sample.toml"),
    "link-out": (link_out, "the demo output folder is a link"),
    "link-in": (link_in, "the demo output folder is a link"),
    "clean-fails": (None, "cannot clean samples/out: Permission denied"),
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


def test_real_steps():
    assert [s.name for s in demo_build.STEPS] == ["sample history", "build"]
    assert demo_build.Step._fields == ("name", "run")
