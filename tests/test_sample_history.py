"""tools/sample_history.py: the pinned-date sample WBS.md history (#28).

The tool runs in process, through `sample_history.main(argv)`, on a copy of
samples/ under tmp_path (copy_samples leaves out every `out` folder).
"""

import os
import tomllib
from pathlib import Path

import pytest

import sample_history
from helpers_config import copy_samples, replace_bytes
from helpers_history import git
from helpers_history import run_sample_history as run
from sample_roles import HISTORY_LESS, HISTORY_RECENT, HISTORY_STALE, HISTORY_YOUNG, NOT_BUILT
from test_samples import GANTRY_SAMPLE, SAMPLES
from test_samples import git as plain_git

pytestmark = pytest.mark.usefixtures("git_ready")

PLAN = SAMPLES / "history" / "plan.toml"
TOOL_GIT_KEYS = {"GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_TEMPLATE_DIR", "GIT_AUTHOR_NAME",
                 "GIT_COMMITTER_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL", "GIT_AUTHOR_DATE",
                 "GIT_COMMITTER_DATE"}
# HEAD of the small fixed plan below: the same on every platform and run.
PINNED_HEAD = "608b0b71f8cc3b2ad145a6cac71dd77b2334dd21"


def built(copy, out=None):
    """Build the copy's history; (HEAD id, every commit id newest first, out folder)."""
    out = copy / "history" / "out" if out is None else out
    code, stdout, stderr = run("--samples", copy, "--out", out)
    assert (code, stderr) == (0, ""), stderr
    head = stdout.strip()
    assert stdout == head + "\n"
    return head, git(out, "rev-list", "HEAD").split(), out


def test_two_runs_give_the_same_ids(tmp_path):
    copy = copy_samples(tmp_path)
    first = built(copy)[:2]
    assert built(copy)[:2] == first
    assert len(first[1]) == 3 + 3 + 1 + 3


def test_ids_ignore_the_users_git_settings(tmp_path, monkeypatch):
    copy = copy_samples(tmp_path)
    clean = built(copy)[:2]
    home = tmp_path / "home"
    (home / "git").mkdir(parents=True)
    hostile = b"[commit]\n\tgpgsign = true\n[core]\n\tautocrlf = true\n[user]\n\tname = Someone Else\n"
    (home / ".gitconfig").write_bytes(hostile)
    (home / "git" / "config").write_bytes(hostile)
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Inherited")
    monkeypatch.setenv("GIT_DEFAULT_HASH", "sha256")
    assert built(copy)[:2] == clean


def test_crlf_inputs_give_the_same_ids(tmp_path):
    copy = copy_samples(tmp_path)
    clean = built(copy)[:2]
    for path in sorted(copy.rglob("*.md")):
        if path.parent.name == "marsh":
            continue  # deliberately invalid UTF-8; never rewritten
        before = path.read_bytes()
        after = before.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        assert after != before, path
        path.write_bytes(after)
    assert built(copy)[:2] == clean


def test_tool_environment_is_exact(tmp_path):
    environ = {"GIT_DIR": "x", "git_index_file": "x", "GIT_DEFAULT_HASH": "sha256", "GIT_AUTHOR_NAME": "x",
               "PATH": "p"}
    env = sample_history.git_env(tmp_path, None, environ)
    assert {k for k in env if k.upper().startswith("GIT_")} == TOOL_GIT_KEYS
    assert env["GIT_AUTHOR_NAME"] == env["GIT_COMMITTER_NAME"] == "Gantry Sample"
    assert env["PATH"] == "p"


def test_object_format_is_sha1(tmp_path):
    _, _, out = built(copy_samples(tmp_path))
    assert git(out, "rev-parse", "--show-object-format").strip() == "sha1"


def test_info_attributes_pin_lf(tmp_path):
    _, _, out = built(copy_samples(tmp_path))
    assert (out / ".git" / "info" / "attributes").read_bytes() == b"* text eol=lf\n"
    assert git(out, "check-attr", "eol", "--", f"{HISTORY_RECENT}/WBS.md").strip().endswith("eol: lf")


def _refused(copy, out, message):
    code, stdout, stderr = run("--samples", copy, "--out", out)
    assert (code, stdout) == (1, "")
    assert stderr == f"sample_history: {message}\n", stderr
    for form in (str(copy.parent), str(copy.parent).replace("\\", "/")):
        assert form not in stderr


def test_refuses_output_outside_the_sample_directory(tmp_path):
    copy = copy_samples(tmp_path)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    for out in (tmp_path / "elsewhere", copy, copy / ".." / "x"):
        _refused(copy, out, "output path is outside the sample directory")
    _refused(copy, copy / "history", "output path holds the sample input history/plan.toml")
    _refused(copy, copy / HISTORY_RECENT, f"output path holds the sample input {HISTORY_RECENT}")
    _refused(copy, copy / "devagent", "output path holds the sample input devagent")
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
    stranger = copy / "history" / "stranger"
    stranger.mkdir()
    (stranger / "keep.md").write_bytes(b"x\n")
    _refused(copy, stranger, "output folder is not empty and was not made by this tool")
    assert (stranger / "keep.md").read_bytes() == b"x\n"


def test_refuses_plan_after_as_of(tmp_path):
    copy = copy_samples(tmp_path)
    replace_bytes(copy / "history" / "plan.toml", "last_date = 2026-01-16", "last_date = 2026-01-21")
    _refused(copy, copy / "history" / "out", "plan last_date 2026-01-21 is after as_of 2026-01-20")


def test_refuses_identical_versions(tmp_path):
    copy = copy_samples(tmp_path)
    versions = copy / "history" / HISTORY_RECENT
    (versions / "2026-01-08.md").write_bytes((versions / "2025-12-15.md").read_bytes().replace(b"\n", b"\r\n"))
    _refused(copy, copy / "history" / "out", f"version history/{HISTORY_RECENT}/2026-01-08.md equals the one before it")


def test_refuses_unordered_dates_and_a_missing_version(tmp_path):
    copy = copy_samples(tmp_path)
    plan = copy / "history" / "plan.toml"
    replace_bytes(plan, "dates = [2026-01-05]", "dates = [2026-01-05, 2026-01-04]")
    _refused(copy, copy / "history" / "out",
             f"plan site {HISTORY_YOUNG}: dates must be strictly ascending and before last_date 2026-01-16")
    replace_bytes(plan, "dates = [2026-01-05, 2026-01-04]", "dates = [2026-01-06]")
    _refused(copy, copy / "history" / "out", f"missing version history/{HISTORY_YOUNG}/2026-01-06.md")


def test_committed_plan_is_not_after_as_of():
    plan = tomllib.loads(PLAN.read_bytes().decode("utf-8"))
    config = tomllib.loads(GANTRY_SAMPLE.read_bytes().decode("utf-8"))
    assert plan["last_date"] <= config["as_of"]


def test_plan_names_only_sample_sites_with_a_wbs():
    plan = tomllib.loads(PLAN.read_bytes().decode("utf-8"))
    assert list(plan["site"]) == [HISTORY_RECENT, HISTORY_STALE, HISTORY_YOUNG]
    assert HISTORY_LESS not in plan["site"] and not NOT_BUILT & set(plan["site"])
    for site in plan["site"]:
        assert (SAMPLES / site / "WBS.md").is_file(), site


def test_newest_version_equals_the_sample_wbs(tmp_path):
    copy = copy_samples(tmp_path)
    _, _, out = built(copy)
    sites = list(tomllib.loads(PLAN.read_bytes().decode("utf-8"))["site"])
    assert len(sites) == 3
    for site in sites:
        blob = git(out, "rev-parse", f"HEAD:{site}/WBS.md").strip()
        ran = plain_git(out, "cat-file", "blob", blob, hermetic=True)
        assert ran.returncode == 0, ran.stderr
        committed = ran.stdout
        assert committed == (SAMPLES / site / "WBS.md").read_bytes().replace(b"\r\n", b"\n"), site


def test_rebuild_replaces_a_read_only_tree(tmp_path):
    copy = copy_samples(tmp_path)
    head, _, out = built(copy)
    objects = [p for p in (out / ".git" / "objects").rglob("*") if p.is_file()]
    assert objects and all(not os.access(p, os.W_OK) for p in objects if p.parent.name != "info"
                           and p.parent.name != "pack")
    assert built(copy)[0] == head


def _small_samples(tmp_path):
    samples = tmp_path / "small"
    (samples / "history" / "alpha").mkdir(parents=True)
    (samples / "alpha").mkdir()
    (samples / "gantry.sample.toml").write_bytes(b"as_of = 2026-01-20\n")
    (samples / "history" / "plan.toml").write_bytes(b"last_date = 2026-01-16\n[site.alpha]\ndates = [2026-01-05]\n")
    (samples / "history" / "alpha" / "2026-01-05.md").write_bytes(b"- [ ] A {issue: Issue-1}\n")
    (samples / "alpha" / "WBS.md").write_bytes(b"- [x] A {issue: Issue-1}\r\n")
    (samples / "devagent").mkdir()
    return samples


def test_pinned_head_for_a_small_plan(tmp_path):
    samples = _small_samples(tmp_path)
    code, stdout, stderr = run("--samples", samples)
    assert (code, stderr) == (0, "")
    assert stdout.strip() == PINNED_HEAD
    assert Path(samples / "history" / "out" / ".git").is_dir()
