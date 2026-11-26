"""The refresh tests' aggregator update step (#12): devagent's portfolio.sh, run
the way tools/sample_portfolio.py runs it.

    <python> -P run_aggregator.py DEVAGENT CWD DA_HOME PATH

Runs `<DEVAGENT>/scripts/portfolio.sh` in CWD with DA_HOME as devagent's home
(sample_portfolio.run_aggregator), so the document is
`<DA_HOME>/portfolio/portfolio.json`. PATH is the search path the aggregator
gets, recorded when the test wrote its config: the refresh may run this step
with an empty PATH (test_refresh's minimal_env). Exit 0 when the aggregator
wrote its document; 1, with one `run_aggregator:` line on stderr, when it
failed or could not start. A test file, never collected: stdlib and tools/ only.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import sample_portfolio as sp  # noqa: E402


def command(devagent, cwd, da_home, python=None) -> list[str]:
    """The update step's argv: every program and path absolute, so it runs under any PATH."""
    python = os.path.abspath(python or sys.executable)
    search = os.pathsep.join(os.path.abspath(entry) for entry in os.environ.get("PATH", "").split(os.pathsep)
                             if entry)
    return [python, "-P", str(Path(__file__).resolve()), str(Path(devagent).resolve()), os.path.abspath(cwd),
            os.path.abspath(da_home), search]


def main(argv) -> int:
    if len(argv) != 4:
        print("run_aggregator: usage: DEVAGENT CWD DA_HOME PATH", file=sys.stderr)
        return 2
    devagent, cwd, da_home, search = argv
    os.environ["PATH"] = search
    (Path(da_home) / "home").mkdir(parents=True, exist_ok=True)
    try:
        sp.run_aggregator(devagent, cwd, da_home)
    except (sp.Refused, sp.Preflight) as e:
        print(f"run_aggregator: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
