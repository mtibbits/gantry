"""Check that the Python and JavaScript schedulers agree on real sites (#9).

    python tools/check_schedulers.py --config FILE --out DIR

Builds the worksite page of every site the installation config can build into
DIR/sites/, then compares gantry.schedule.plan with the page's own
gantrySchedule.plan (through the page-script harness) at 1 to 8 lanes. It
prints one line of counts, never a site's data, so the line can go in a PR.
Exit codes: 0 agreed, 1 a disagreement, 2 a usage error, a refused --out, a
config error or a harness that cannot run. DIR must be outside the repository.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from gantry.config import ConfigError, load_config  # noqa: E402
from gantry.schedule import plan  # noqa: E402
from gantry.sites import load_sites  # noqa: E402
from gantry.views.worksite import page_model, render_page  # noqa: E402
from gantry.wbs_stopgap import read_wbs  # noqa: E402
from page_script.harness import HarnessError, run_function  # noqa: E402

LANES = range(1, 9)


def _fail(message):
    print(f"check_schedulers: {message}", file=sys.stderr)
    return 2


def _inside_repo(out):
    out = out.resolve()
    return out == ROOT or out.is_relative_to(ROOT)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="check_schedulers", description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, metavar="FILE", help="the installation config")
    parser.add_argument("--out", required=True, metavar="DIR", help="where the pages go; outside the repository")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if _inside_repo(out):
        return _fail("--out must be outside the repository")
    try:
        config = load_config(args.config)
        sites, _ = load_sites(config)
    except ConfigError as e:
        return _fail(str(e))
    folder = out / "sites"
    built = skipped = leaves = disagreements = 0
    for site in sites:
        data = read_wbs(site.planning_dir / "WBS.md", site.name) if site.problem is None else None
        if data is None or any(e.kind == "site_unreadable" for e in data.report):
            skipped += 1
            continue
        items = page_model(site.name, data, default_unit_hours=config.default_unit_hours)["schedule"]["items"]
        folder.mkdir(parents=True, exist_ok=True)
        page = folder / f"{site.name}.html"
        page.write_bytes(render_page(site.name, data, default_unit_hours=config.default_unit_hours).encode("utf-8"))
        built += 1
        leaves += len(items)
        for lanes in LANES:
            try:
                js = {key: tuple(v) for key, v in run_function(page, "gantrySchedule.plan", [items, lanes])}
            except HarnessError as e:
                return _fail(str(e))
            disagreements += js != plan(items, lanes)
    verdict = "agreed" if not disagreements else f"{disagreements} disagreements"
    print(f"sites {built}, skipped {skipped}, leaves {leaves}, lanes 1-8: {verdict}")
    return 0 if not disagreements else 1


if __name__ == "__main__":
    sys.exit(main())
