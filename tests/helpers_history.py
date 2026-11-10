"""Fixture repositories and the git gate for the history tests (#28).

A plain module, not a test module; it resolves like helpers_config (see
sample_roles's import contract). Every repository is built with pinned
identities and dates and no global or system git config, so its commit ids
are the same on every run.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

from helpers_config import literal, write_site_tree
from test_samples import decode_bytes, git_env

GIT_MISSING = "git not on PATH"
CI_SUFFIX = " (CI is set, so a history test fails instead of skipping)"
IDENTITY = {"GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}


def require_git(env=None):
    """Return when git is on PATH, by the reader's own lookup; else skip, or
    fail under a truthy CI."""
    from gantry.roots import program_on_path

    env = os.environ if env is None else env
    if program_on_path("git", env):
        return
    if env.get("CI"):
        pytest.fail(GIT_MISSING + CI_SUFFIX, pytrace=False)
    pytest.skip(GIT_MISSING)


def stamp(when):
    """A git date: `YYYY-MM-DD` is midday UTC; anything longer is used as given."""
    return f"{when}T12:00:00+0000" if len(when) == 10 else when


def git(repo, *args, when=None, check=True):
    """git in `repo` with no global or system config and a pinned identity: stdout text."""
    env = {**git_env(hermetic=True), **IDENTITY}
    if when is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = stamp(when)
    result = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=repo, env=env, capture_output=True)
    if check:
        assert result.returncode == 0, (args, decode_bytes(result.stderr))
    return decode_bytes(result.stdout)


def make_repo(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "--initial-branch=main", ".")
    return path


def write(repo, rel, data):
    """Write bytes (a str is UTF-8) at `rel`, LF untouched."""
    file = Path(repo) / rel
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return file


def commit(repo, rel, data, when):
    """Write `rel`, commit it dated `when`, and return the commit id."""
    write(repo, rel, data)
    git(repo, "add", "-f", "--", rel)
    git(repo, "commit", "-q", "-m", f"{rel} {when}", when=when)
    return git(repo, "rev-parse", "HEAD").strip()


def branch(repo, name):
    git(repo, "branch", name)


def checkout(repo, name):
    git(repo, "checkout", "-q", name)


def merge(repo, name, when):
    """Merge branch `name` into the current one with a merge commit; its id."""
    git(repo, "merge", "-q", "--no-ff", "-m", f"merge {name}", name, when=when)
    return git(repo, "rev-parse", "HEAD").strip()


def shallow_clone(src, dst, depth=1):
    git(Path(dst).parent, "clone", "-q", f"--depth={depth}", Path(src).resolve().as_uri(), str(dst))
    return Path(dst)


def corrupt(repo, object_id):
    """Overwrite a loose object with bytes that do not inflate."""
    file = Path(repo) / ".git" / "objects" / object_id[:2] / object_id[2:]
    os.chmod(file, stat.S_IWRITE | stat.S_IREAD)
    file.write_bytes(b"not a zlib stream")


def site_config(folder, sites, history=None):
    """(config, roots, sites by name) for a site tree; `history` maps a site
    name to the path its `history` key names."""
    from gantry.config import load_config
    from gantry.sites import config_roots, load_sites

    tail = "".join(f"\n[site.{name}]\nhistory = {literal(path)}\n" for name, path in (history or {}).items())
    config = load_config(write_site_tree(folder, sites, tail=tail))
    roots = config_roots(config)
    found, _ = load_sites(config, roots)
    return config, roots, {site.name: site for site in found}


def spy_git(monkeypatch):
    """Record each wbs_history.run_git call as (args, cwd, ceiling)."""
    from gantry import wbs_history

    calls = []
    real = wbs_history.run_git

    def spy(args, *, cwd, ceiling):
        calls.append((list(args), cwd, ceiling))
        return real(args, cwd=cwd, ceiling=ceiling)

    monkeypatch.setattr(wbs_history, "run_git", spy)
    return calls


def spy_repository(monkeypatch):
    """Record each wbs_history.history_repository answer."""
    from gantry import wbs_history

    given = []
    real = wbs_history.history_repository

    def spy(*args, **kwargs):
        given.append(real(*args, **kwargs))
        return given[-1]

    monkeypatch.setattr(wbs_history, "history_repository", spy)
    return given


def run_sample_history(*argv):
    """tools/sample_history.py's main(argv) in process: (exit code, stdout, stderr)."""
    import io
    from contextlib import redirect_stderr, redirect_stdout

    import sample_history

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = sample_history.main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()
