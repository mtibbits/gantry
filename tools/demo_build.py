"""Build the Gantry demo from the samples only (#45, #12).

    python tools/demo_build.py

Checks its preconditions, deletes and recreates the demo output folder
(`output_dir` of samples/gantry.sample.toml), then runs each step of STEPS in
order: the sample WBS.md history (tools/sample_history.py); devagent at the
release tests/devagent.pin names; the sample portfolio JSON, written by that
devagent's own aggregator (tools/sample_portfolio.py); then `gantry build` on
the sample config. An issue that adds a preparation step inserts it into STEPS
before "build". On success the last line of output is the folder, relative
to the gantry root in POSIX form (`samples/out`).

devagent is the checkout GANTRY_DEVAGENT_DIR names when it is set, and nothing
is fetched. Otherwise it is a clone of the pinned tag in the git-ignored folder
out/devagent under the gantry root: reused when it is at the pinned version and
the recorded commit with no change in it, else deleted (under
tools/sample_portfolio.py's guard) and fetched again. A fetched checkout at
any commit but the one the fixtures were written at is refused. Windows is
refused, and so is a host with no bash on the path, before anything is deleted.

Before deleting anything the folder must be inside samples/, must not be or
sit under a symlink or junction, and git must ignore it and track no
file in it, in a gantry
checkout; and, with no GANTRY_DEVAGENT_DIR, git must ignore out/devagent. A refusal or a failed step prints one `demo_build: <reason>` line
naming no absolute path and exits 1 (a step's exception also prints its
traceback); an argument is a usage error, exit 2. docs/demo.md states the
rules. It imports nothing of gantry before its build step runs.
"""

import argparse
import os
import shutil
import stat
import subprocess
import sys
import traceback
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = "samples/gantry.sample.toml"
PROBE = ".probe"
# devagent for the sample portfolio step (#12).
VARIABLE = "GANTRY_DEVAGENT_DIR"
FETCH = "out/devagent"  # the fetch folder, under the gantry root; .gitignore's out/ covers it
REPO = "https://github.com/mtibbits/devagent"
# The commit the fixtures were written at: a fetched tag must still point there.
COMMIT_RECORD = "tests/fixtures/devagent_records/written/Issue-6/provenance.json"
NO_BASH = "bash is not on the path; the sample portfolio step needs it"

for _path in (ROOT, ROOT / "tools"):  # tools/ too: `python -P` leaves it off
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

Step = namedtuple("Step", "name run")


class Refused(Exception):
    """A reason to exit 1; the message names no absolute path."""


def _sample_history(root):
    import sample_history

    return sample_history.main(["--samples", str(root / "samples")])


def _build(root):
    from gantry.cli import main as gantry_main

    return gantry_main(["build", "--config", str(root / CONFIG)])


def windows_refusal():
    from devagent_record_fixtures import TOOLCHAIN

    return f"the sample portfolio step {TOOLCHAIN}, which Windows does not provide; see docs/demo.md"


def devagent_dir(root, env) -> Path:
    """The devagent checkout the portfolio step runs: GANTRY_DEVAGENT_DIR's, else the fetch folder."""
    value = env.get(VARIABLE) or ""
    return Path(os.path.abspath(value)) if value else Path(root) / FETCH


def _fetch_folder_ignored(root):
    ran = _git(root, "-c", f"core.excludesFile={os.devnull}", "check-ignore", "-q", "--no-index", "--",
               f"{FETCH}/{PROBE}")
    if ran.returncode == 1:
        raise Refused(f"git does not ignore {FETCH}, the devagent fetch folder")
    if ran.returncode != 0:
        raise Refused(f"cannot ask git whether {FETCH} is ignored")


def preconditions(root, env, os_name):
    """Refused unless the portfolio step can run; checked before the clean, so the previous demo stands."""
    if os_name == "nt":
        raise Refused(windows_refusal())
    if shutil.which("bash", path=env.get("PATH", os.defpath)) is None:
        raise Refused(NO_BASH)
    if not env.get(VARIABLE):
        _fetch_folder_ignored(root)


def recorded_commit(root):
    import json

    try:
        value = json.loads((Path(root) / COMMIT_RECORD).read_bytes().decode("utf-8"))["devagent_commit"]
    except (OSError, ValueError, KeyError, TypeError):
        value = None
    if not isinstance(value, str) or not value:
        raise Refused(f"cannot read devagent_commit from {COMMIT_RECORD}")
    return value


def _head(folder):
    ran = _git(folder, "rev-parse", "HEAD")
    return ran.stdout.decode("utf-8", "replace").strip() if ran.returncode == 0 else None


def _reusable(folder, version, commit) -> bool:
    from devagent_record_fixtures import checkout_version

    return checkout_version(folder) == version and _head(folder) == commit


def _clean_checkout(folder) -> bool:
    ran = _git(folder, "status", "--porcelain", "--untracked-files=all")
    return ran.returncode == 0 and not ran.stdout


def _rmtree(folder):
    """shutil.rmtree that also deletes read-only files (git's objects, which Windows will not unlink)."""
    def writable(func, path, _):
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)

    shutil.rmtree(folder, **({"onexc": writable} if sys.version_info >= (3, 12) else {"onerror": writable}))


def _devagent(root):
    """devagent at the pin: GANTRY_DEVAGENT_DIR's checkout (nothing fetched), or the fetch folder."""
    from devagent_record_fixtures import Refused as FixturesRefused
    from devagent_record_fixtures import read_pin
    from sample_portfolio import guard_deletable

    root = Path(root)
    if os.environ.get(VARIABLE):
        return 0  # tools/sample_portfolio.py refuses a checkout at another release
    tag, commit = read_pin(), recorded_commit(root)
    version = tag.removeprefix("devagent--v")
    folder = root / FETCH
    if os.path.lexists(folder):
        if not os.path.isdir(folder):
            raise Refused(f"{FETCH} is not a folder; remove it to fetch devagent")
        # A change in a checkout is someone's edit, at any version: never used, never deleted.
        if (folder / ".git").exists() and not _clean_checkout(folder):
            raise Refused(f"{FETCH} holds a change; it is not used or deleted (remove it to fetch again)")
        if _reusable(folder, version, commit):
            return 0
        try:
            target = guard_deletable(root, FETCH, FETCH)
        except FixturesRefused as e:  # sample_portfolio's: one line, as this tool's own
            raise Refused(str(e)) from None
        _rmtree(target)
    ran = _git(root, "clone", "-q", "--depth", "1", "--branch", tag, REPO, FETCH)
    if ran.returncode != 0:
        raise Refused(f"cannot fetch {tag} from {REPO} (git exit {ran.returncode})")
    found = _head(folder)
    if found != commit:
        raise Refused(f"{tag} is at {found}; the fixtures were written at {commit}")
    return 0


def _sample_portfolio(root):
    import sample_portfolio

    root = Path(root)
    return sample_portfolio.main(["--devagent", str(devagent_dir(root, os.environ)),
                                  "--samples", str(root / "samples")])


STEPS = (Step("sample history", _sample_history), Step("devagent", _devagent),
         Step("sample portfolio", _sample_portfolio), Step("build", _build))


def output_folder(root) -> Path:
    """`output_dir` of the sample config, joined to samples/ lexically."""
    import tomllib

    root = Path(root)
    try:
        value = tomllib.loads((root / CONFIG).read_bytes().decode("utf-8"))["output_dir"]
    except (OSError, ValueError, KeyError):
        value = None
    if not isinstance(value, str) or not value:
        raise Refused(f"cannot read output_dir from {CONFIG}")
    return Path(os.path.normpath(root / "samples" / value))


def _run_git(root, *args):
    """git in `root` with no ambient GIT_* variable and never a credential prompt (a clone of a
    public repository must fail, not wait on a terminal). OSError is Refused, worded per command."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        return subprocess.run(["git", "-C", str(root), *args], env=env, stdin=subprocess.DEVNULL,
                              capture_output=True)
    except OSError:
        if args[-2:-1] == ("--",) or "check-ignore" in args or "ls-files" in args:
            raise Refused("cannot ask git whether the demo output folder is ignored") from None
        raise Refused(f"cannot run git ({next((a for a in args if not a.startswith('-')), 'git')})") from None


_git = _run_git  # the name every step calls; a test replaces it


def _is_link(path) -> bool:
    """A symlink, or a junction (which os.path.islink misses before 3.12)."""
    st = os.lstat(path)
    tag = getattr(st, "st_reparse_tag", 0)
    return stat.S_ISLNK(st.st_mode) or tag == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1)


def _inside(inner, outer) -> bool:
    inner, outer = os.path.normcase(str(inner)), os.path.normcase(str(outer))
    try:
        return inner != outer and os.path.commonpath([inner, outer]) == outer
    except ValueError:
        return False


def _same(found, root) -> bool:
    try:
        return os.path.samefile(found, root)
    except OSError:
        return False


def guard(root) -> Path:
    """The demo output folder, or Refused; deletes nothing."""
    root = Path(root)
    ran = _git(root, "rev-parse", "--show-toplevel")
    if ran.returncode != 0 or not _same(ran.stdout.decode("utf-8", "replace").strip(), root):
        raise Refused("not a gantry checkout")
    folder = output_folder(root)
    samples = Path(os.path.normpath(root / "samples"))
    if not _inside(folder, samples):
        raise Refused("the demo output folder is not inside samples/")
    walk = samples
    for part in ("", *folder.relative_to(samples).parts):
        walk = walk / part
        if os.path.lexists(walk) and _is_link(walk):
            raise Refused("the demo output folder is a link")
    probe = (folder / PROBE).relative_to(root).as_posix()
    ran = _git(root, "-c", f"core.excludesFile={os.devnull}", "check-ignore", "-q", "--no-index", "--", probe)
    if ran.returncode == 1:
        raise Refused("git does not ignore the demo output folder")
    if ran.returncode != 0:
        raise Refused("cannot ask git whether the demo output folder is ignored")
    # An ignored folder can still hold a force-added file; the clean would delete it.
    ran = _git(root, "ls-files", "-z", "--", folder.relative_to(root).as_posix())
    if ran.returncode != 0:
        raise Refused("cannot ask git whether the demo output folder is ignored")
    if ran.stdout:
        raise Refused("git tracks a file in the demo output folder")
    return folder


def _clean(folder, rel):
    try:
        if os.path.lexists(folder):
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
    except OSError as e:
        raise Refused(f"cannot clean {rel}: {e.strerror or type(e).__name__}") from None


def _run_step(step, root) -> int:
    try:
        code = step.run(root)
    except SystemExit as e:
        code = e.code
        if code is not None and not isinstance(code, int):
            print(code, file=sys.stderr)
            code = 1
    except Refused as e:
        print(f"demo_build: {e}", file=sys.stderr)
        print(f"demo_build: step {step.name} failed", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"demo_build: step {step.name} failed ({type(e).__name__})", file=sys.stderr)
        traceback.print_exc()
        return 1
    code = 0 if code is None else code
    if code != 0:
        print(f"demo_build: step {step.name} failed (exit {code})", file=sys.stderr)
        return 1
    return 0


def run(root, *, os_name=None) -> int:
    """Preconditions, guard, clean, then each step in order; 0 or 1. `os_name` replaces os.name (a test's)."""
    root = Path(root)
    try:
        preconditions(root, os.environ, os.name if os_name is None else os_name)
        folder = guard(root)
        rel = folder.relative_to(root).as_posix()
        _clean(folder, rel)
    except Refused as e:
        print(f"demo_build: {e}", file=sys.stderr)
        return 1
    for step in STEPS:
        if _run_step(step, root):
            return 1
    print(rel)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="demo_build", description=__doc__.split("\n\n")[0])
    parser.parse_args(argv)
    return run(ROOT)


if __name__ == "__main__":
    sys.stdout.reconfigure(newline="\n")
    sys.exit(main())
