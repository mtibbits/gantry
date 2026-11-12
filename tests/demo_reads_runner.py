"""The child side of tests/test_demo_reads.py's read spy (#43).

    python -P -B demo_reads_runner.py RECORD GANTRY_ROOT ARGV...
    python -P -B demo_reads_runner.py --git-probe RECORD GANTRY_ROOT CWD CEILING

The first form installs an audit hook, then runs gantry.cli.main(ARGV); the
second runs ONE real wbs_history.run_git call under the same hook, so a real
subprocess.Popen record can be classified. Either way RECORD (UTF-8 JSON) is
written after the hook is switched off, even when the run raised, and the
process exits with the run's code. It exits 3 with one stderr line, writing
no RECORD, when `gantry` is already imported or would not be imported from
GANTRY_ROOT/gantry.

A plain module, never collected: stdlib only, and nothing of gantry is
imported before the hook. The hook records raw values only; the parent
classifies them.
"""

import json
import os
import sys
import sysconfig

EVENTS = frozenset({"open", "os.listdir", "os.scandir", "subprocess.Popen", "os.chdir",
                    "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.startfile",
                    "os.fork", "os.forkpty", "_winapi.CreateProcess"})
ON = [False]
ERRORS = [0]
RECORDED = []
PKG = [""]
_IS_GANTRY = {}


def _is_gantry(filename):
    hit = _IS_GANTRY.get(filename)
    if hit is None:
        hit = _IS_GANTRY[filename] = os.path.normcase(filename).startswith(PKG[0])
    return hit


def _plain(value):
    """A JSON value for an event argument: str, int, or None."""
    if isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool)):
        return value
    if isinstance(value, bytes):
        return os.fsdecode(value)
    if hasattr(value, "__fspath__"):
        return _plain(os.fspath(value))
    return None


def hook(event, args):
    # Filter on the name BEFORE touching a frame: sys._getframe raises an
    # audit event itself, and an unfiltered hook never returns (U2).
    if event not in EVENTS or not ON[0]:
        return
    try:
        frame = sys._getframe(1)  # the frame that made the audited call
        inner = [frame.f_code.co_filename, frame.f_code.co_name]
        gantry = []
        while frame is not None:
            code = frame.f_code
            if _is_gantry(code.co_filename):
                gantry.append([os.path.basename(code.co_filename), code.co_name])
            frame = frame.f_back
        if not gantry:
            return
        item = {"event": event, "arg": _plain(args[0]) if args else None, "inner": inner, "gantry": gantry}
        if event == "open":
            item["mode"] = _plain(args[1]) if len(args) > 1 else None
        if event in ("subprocess.Popen", "_winapi.CreateProcess"):
            # (executable, args, cwd, env) and (application, command line, cwd)
            argv = args[1]
            if isinstance(argv, (str, bytes)) or hasattr(argv, "__fspath__"):
                item["argv"] = _plain(argv)
            else:
                item["argv"] = [_plain(a) for a in argv]
            item["popen_cwd"] = _plain(args[2])
        RECORDED.append(item)
    except Exception:
        ERRORS[0] += 1  # a hook never raises; the count is asserted 0


def _refuse(message):
    print(f"demo_reads_runner: {message}", file=sys.stderr)
    sys.exit(3)


def _write(record_path, exit_value):
    ON[0] = False
    paths = sysconfig.get_paths()
    record = {
        "exit": exit_value,
        "hook_errors": ERRORS[0],
        "cwd": os.getcwd(),
        "sys_path": list(sys.path),
        "stdlib": [paths["stdlib"], paths["platstdlib"]],
        "safe_path": bool(getattr(sys.flags, "safe_path", False)),
        "dont_write_bytecode": sys.dont_write_bytecode,
        "events": RECORDED,
    }
    with open(record_path, "w", encoding="utf-8") as handle:
        json.dump(record, handle)


def _run(argv):
    from gantry.cli import main

    return main(argv)


def _git_probe(cwd, ceiling):
    from gantry import wbs_history

    ran = wbs_history.run_git(["rev-parse", "--show-toplevel"], cwd=cwd, ceiling=ceiling)
    return ran.returncode if hasattr(ran, "returncode") else f"no data: {ran.text}"


def main(argv):
    probe = bool(argv) and argv[0] == "--git-probe"
    if probe:
        argv = argv[1:]
    if len(argv) < (4 if probe else 2):
        _refuse("usage: RECORD GANTRY_ROOT ARGV... | --git-probe RECORD GANTRY_ROOT CWD CEILING")
    record_path, root, rest = argv[0], argv[1], argv[2:]
    if "gantry" in sys.modules:
        _refuse("gantry is already imported")
    PKG[0] = os.path.normcase(os.path.join(os.path.realpath(root), "gantry")) + os.sep
    import importlib.util

    spec = importlib.util.find_spec("gantry")
    origin = spec.origin if spec is not None else None
    if origin is None or not os.path.normcase(os.path.realpath(origin)).startswith(PKG[0]):
        _refuse(f"gantry would be imported from {origin}, not from GANTRY_ROOT")
    sys.addaudithook(hook)
    ON[0] = True
    try:
        exit_value = _git_probe(*rest[:2]) if probe else _run(rest)
    except SystemExit as e:
        exit_value = e.code if isinstance(e.code, int) or e.code is None else str(e.code)
    except BaseException as e:  # recorded, then the process exits non-zero
        exit_value = f"raised: {type(e).__name__}"
        raise
    finally:
        _write(record_path, exit_value)
    return exit_value


if __name__ == "__main__":
    value = main(sys.argv[1:])
    sys.exit(value if isinstance(value, int) else (0 if value is None else 1))
