"""Build the sample WBS.md history repository (#28).

    python tools/sample_history.py [--samples DIR] [--out DIR]

Reads samples/history/plan.toml and commits, per site, each dated version
samples/history/<site>/<date>.md and then the site's current
samples/<site>/WBS.md at the plan's last_date, into a fresh git repository
at --out (default <samples>/history/out, which git ignores). Every input is
normalised to LF, every commit has a pinned identity and midday dates, the
repository is sha1, and the user's git settings are kept out, so the commit
ids are the same on every run and every checkout. Exit 0 prints the new HEAD
id; exit 1 prints one `sample_history: <reason>` line naming no absolute
path; a usage error exits 2. docs/wbs-history.md states the rules.

It imports nothing from gantry, so it runs before any install.
"""

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tomllib
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_NAME = "gantry.sample.toml"
NAME = "Gantry Sample"
EMAIL = "sample@example.invalid"
TOOL_DIR = ".tool"
MARKER = "sample-history"  # in TOOL_DIR: the folder is this tool's to delete
FLAGS = ("-c", "core.hooksPath=", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false")


class Refused(Exception):
    """A reason to exit 1; the message names paths relative to --samples."""


def git_env(out, when=None, environ=None) -> dict:
    """The tool's git environment: every inherited GIT_* removed, then only
    its own config, template, identity and (for a commit) dates."""
    environ = os.environ if environ is None else environ
    env = {k: v for k, v in environ.items() if not k.upper().startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = str(Path(out) / TOOL_DIR / "gitconfig")
    env["GIT_TEMPLATE_DIR"] = str(Path(out) / TOOL_DIR / "template")
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = NAME
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = EMAIL
    stamp = f"{(when or date(1970, 1, 1)).isoformat()}T12:00:00+0000"
    env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = stamp
    return env


def _git(out, *args, when=None) -> str:
    try:
        ran = subprocess.run(["git", *FLAGS, *args], cwd=out, env=git_env(out, when),
                             stdin=subprocess.DEVNULL, capture_output=True)
    except FileNotFoundError:
        raise Refused("git not found") from None
    if ran.returncode != 0:
        line = next((x for x in ran.stderr.decode("utf-8", "replace").splitlines() if x.strip()), "")
        raise Refused(f"git {args[0]} failed (exit {ran.returncode}){': ' + line if line else ''}"
                      .replace(str(out), "<out>"))
    return ran.stdout.decode("utf-8", "replace")


def _rel(path, samples) -> str:
    try:
        return Path(path).relative_to(samples).as_posix()
    except ValueError:
        return Path(path).name


def _within(inner, outer) -> bool:
    inner, outer = os.path.normcase(str(inner)), os.path.normcase(str(outer))
    try:
        return os.path.commonpath([inner, outer]) == outer
    except ValueError:
        return False


def _lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def read_plan(samples):
    """(last_date, [(date, site, bytes)] in commit order), or Refused."""
    plan_file = samples / "history" / "plan.toml"
    try:
        plan = tomllib.loads(plan_file.read_bytes().decode("utf-8"))
        as_of = tomllib.loads((samples / CONFIG_NAME).read_bytes().decode("utf-8"))["as_of"]
    except (OSError, ValueError, KeyError) as e:
        raise Refused(f"cannot read the plan or {CONFIG_NAME}: {getattr(e, 'strerror', None) or e}") from None
    last = plan.get("last_date")
    if type(last) is not date or type(as_of) is not date:
        raise Refused("plan last_date and the sample as_of must be TOML dates")
    if last > as_of:
        raise Refused(f"plan last_date {last} is after as_of {as_of}")
    commits = []
    for order, (site, table) in enumerate(plan.get("site", {}).items()):
        dates = table.get("dates", [])
        if any(type(d) is not date for d in dates):
            raise Refused(f"plan site {site}: dates must be TOML dates")
        if any(a >= b for a, b in zip(dates, dates[1:])) or (dates and dates[-1] >= last):
            raise Refused(f"plan site {site}: dates must be strictly ascending and before last_date {last}")
        files = [(d, samples / "history" / site / f"{d.isoformat()}.md") for d in dates]
        files.append((last, samples / site / "WBS.md"))
        before = None
        for when, file in files:
            try:
                data = _lf(file.read_bytes())
            except OSError:
                raise Refused(f"missing version {_rel(file, samples)}") from None
            if data == before:
                raise Refused(f"version {_rel(file, samples)} equals the one before it")
            before = data
            commits.append((when, order, site, data))
    commits.sort(key=lambda c: (c[0], c[1]))
    return last, [(when, site, data) for when, _, site, data in commits]


def _check_out(out, samples, sites):
    if out == samples or not _within(out, samples):
        raise Refused("output path is outside the sample directory")
    inputs = [samples / "history" / "plan.toml", samples / "devagent", *[samples / s for s in sites]]
    for held in inputs:
        if _within(held, out):
            raise Refused(f"output path holds the sample input {_rel(held, samples)}")
    if out.exists():
        if not out.is_dir():
            raise Refused("output path is not a folder")
        if any(out.iterdir()) and not (out / TOOL_DIR / MARKER).is_file():
            raise Refused("output folder is not empty and was not made by this tool")


def _delete(out):
    """Delete the folder, clearing each read-only bit first (git's objects are read-only)."""
    if not out.exists():
        return
    for folder, dirs, files in os.walk(out, topdown=False):
        for name in files:
            path = os.path.join(folder, name)
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            os.remove(path)
        for name in dirs:
            path = os.path.join(folder, name)
            if os.path.islink(path):
                os.remove(path)
            else:
                os.rmdir(path)
    os.rmdir(out)


def build(samples, out) -> str:
    """Rebuild the history repository at `out`; its HEAD id."""
    last, commits = read_plan(samples)
    _check_out(out, samples, sorted({site for _, site, _ in commits}))
    if shutil.which("git") is None:
        raise Refused("git not found")
    _delete(out)
    (out / TOOL_DIR / "template").mkdir(parents=True)
    (out / TOOL_DIR / "gitconfig").write_bytes(b"")
    (out / TOOL_DIR / MARKER).write_bytes(b"made by tools/sample_history.py; deleted and rebuilt on each run\n")
    _git(out, "init", "-q", "--object-format=sha1", "--initial-branch=main", ".")
    info = out / ".git" / "info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "attributes").write_bytes(b"* text eol=lf\n")
    for when, site, data in commits:
        (out / site).mkdir(exist_ok=True)
        (out / site / "WBS.md").write_bytes(data)
        _git(out, "add", "--", f"{site}/WBS.md")
        _git(out, "commit", "-q", "-m", f"{site}: {when.isoformat()}", when=when)
    return _git(out, "rev-parse", "HEAD").strip()


def main(argv) -> int:
    parser = argparse.ArgumentParser(prog="sample_history", description=__doc__.split("\n\n")[0])
    parser.add_argument("--samples", default=str(ROOT / "samples"), help="the sample directory")
    parser.add_argument("--out", default=None, help="the repository to build (default <samples>/history/out)")
    args = parser.parse_args(argv)
    samples = Path(os.path.realpath(args.samples))
    out = Path(os.path.realpath(args.out if args.out is not None else samples / "history" / "out"))
    try:
        print(build(samples, out))
    except Refused as e:
        print(f"sample_history: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"sample_history: {e.strerror or type(e).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
