"""Build the Gantry demo from the samples only (#45).

    python tools/demo_build.py

Deletes and recreates the demo output folder (`output_dir` of
samples/gantry.sample.toml), then runs each step of STEPS in order: the
sample WBS.md history (tools/sample_history.py), then `gantry build` on the
sample config. An issue that adds a preparation step inserts it into STEPS
before "build". On success the last line of output is the folder, relative
to the gantry root in POSIX form (`samples/out`).

Before deleting anything the folder must be inside samples/, must not be or
sit under a symlink or junction, and git must ignore it and track no
file in it, in a gantry
checkout. A refusal or a failed step prints one `demo_build: <reason>` line
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

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

Step = namedtuple("Step", "name run")


class Refused(Exception):
    """A reason to exit 1; the message names no absolute path."""


def _sample_history(root):
    import sample_history

    return sample_history.main(["--samples", str(root / "samples")])


def _build(root):
    from gantry.cli import main as gantry_main

    return gantry_main(["build", "--config", str(root / CONFIG)])


STEPS = (Step("sample history", _sample_history), Step("build", _build))


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


def _git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    try:
        return subprocess.run(["git", "-C", str(root), *args], env=env, stdin=subprocess.DEVNULL,
                              capture_output=True)
    except OSError:
        raise Refused("cannot ask git whether the demo output folder is ignored") from None


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
    except Exception as e:
        print(f"demo_build: step {step.name} failed ({type(e).__name__})", file=sys.stderr)
        traceback.print_exc()
        return 1
    code = 0 if code is None else code
    if code != 0:
        print(f"demo_build: step {step.name} failed (exit {code})", file=sys.stderr)
        return 1
    return 0


def run(root) -> int:
    """Guard, clean, then each step in order; 0 or 1."""
    root = Path(root)
    try:
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
