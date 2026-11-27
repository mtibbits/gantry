"""Write the expected-leaves snapshots from gantry/portfolio_json.py's output (#83).

    python3 tools/update_expected_leaves.py [--devagent DIR] [--check]

A snapshot records what the portfolio reader returns for one site and what
each consumer reads from it: every node's fields, its metadata through
gantry.model's accessors, its classified depends_on, rank, est: class, h:
score and satisfies:/objective: IDs, then the site's links, held-out keys,
report and the devagent report entries shown for it.

Two sets are written under tests/fixtures/expected_leaves/:

- `<site>.json` for each sample site: the pinned devagent release's
  aggregator (the checkout --devagent, default GANTRY_DEVAGENT_DIR) is run on
  a temporary copy of samples/ and its document read with read_portfolio;
- `fixture/<project>.json` for each project of the committed
  tests/fixtures/portfolio/written.json, mapped with map_document.

--check writes nothing and prints one line per file that differs, is missing
or is extra. A snapshot is what the build reads (#12: Gantry has no reader of
its own).

Exit 0 done; 1 a difference, an extra file, a failed aggregator or a document
the reader refuses; 2 a usage error, no bash, or --devagent is not a devagent
checkout at the pinned release. docs/worksite-page.md states the rules.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "tools"):  # tools/ too: `python -P` leaves it off
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import sample_portfolio  # noqa: E402
from gantry import trace  # noqa: E402
from gantry.attention import read_h  # noqa: E402
from gantry.deps import refs  # noqa: E402
from gantry.est_rules import classify_est  # noqa: E402
from gantry.model import Row  # noqa: E402
from gantry.portfolio_json import PortfolioLoadError, map_document, read_portfolio  # noqa: E402
from gantry.priority import rank  # noqa: E402
from gantry.roots import directory_roots  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "expected_leaves"
FIXTURE_OUT = OUT / "fixture"


def _value(raw):
    """A metadata value as devagent parsed it, in JSON's types."""
    return [str(v) for v in raw] if isinstance(raw, list) else str(raw)


def _node(node, site, namespaces):
    is_row = isinstance(node, Row)
    priority = node.meta.get("priority") if is_row else node.priority
    out = {
        "line": node.line, "row": is_row, "key": node.key, "issue": node.issue, "outline": node.outline,
        "title": node.title, "glyph": node.glyph, "state": node.state, "open": None if is_row else node.open,
        "priority": None if priority is None else _value(priority),
        "epic": None if is_row else node.epic, "parent": node.parent if is_row else None,
        "meta": {key: _value(value) for key, value in node.meta.items()},
    }
    deps = refs(node.meta, site) if is_row else node.deps
    out["depends_on"] = [[ref.raw, ref.kind, ref.site, ref.issue] for ref in deps]
    if is_row:
        out.update(rank=None, est=None, h=None, satisfies=[], objective=[])
        return out
    est = classify_est(node.meta.get("est"))
    satisfied, _ = trace.satisfies_ids(node, namespaces)
    objective, _ = trace.objective_ids(node, satisfied, namespaces)
    out.update(rank=rank(node.priority), est=[est.kind, est.lo_h, est.hi_h, est.number, est.unit], h=read_h(node),
               satisfies=[str(rid) for rid in satisfied], objective=[str(rid) for rid in objective])
    return out


def snapshot_of(project, shown, *, pin, schema_version, namespaces) -> dict:
    """One site's snapshot. `project` has a ProjectInput's four fields; `shown`
    is the devagent report entries shown for it; `namespaces` is every
    configured project name (the site's own included), for satisfies:."""
    data = project.data
    namespaces = frozenset(namespaces) | {project.name}
    nodes = sorted([*data.leaves, *data.rows], key=lambda node: node.line)
    return {
        "pin": pin, "schema_version": schema_version, "site": project.name, "status": project.status,
        "reason": project.reason,
        "nodes": [_node(node, project.name, namespaces) for node in nodes],
        "links": [list(link) for link in data.links],
        "held_out": list(data.held_out),
        "report": [[e.line, e.kind, e.message] for e in data.report],
        "devagent": [[e.line, e.kind, e.message] for e in shown],
    }


def snapshots(portfolio, pin) -> dict:
    """{project: snapshot bytes} for every project of a PortfolioInput, in its order."""
    names = frozenset(p.name for p in portfolio.projects)
    return {p.name: sample_portfolio.dumps(snapshot_of(
                p, [e for e in portfolio.devagent_report if e.project == p.name], pin=pin,
                schema_version=portfolio.schema_version, namespaces=names))
            for p in portfolio.projects}


def expected_fixtures() -> dict:
    """{project: snapshot bytes} for the committed written.json; no aggregator is run."""
    doc = sample_portfolio.written_doc()
    return snapshots(map_document(doc, source="tests/fixtures/portfolio/written.json"), sample_portfolio.read_pin())


def expected(devagent) -> dict:
    """{site: snapshot bytes} for the sample sites: the aggregator of the
    checkout `devagent` on a temporary copy of samples/, read by read_portfolio."""
    pin = sample_portfolio.read_pin()
    return sample_portfolio.on_samples_copy(
        devagent, lambda copy, document: snapshots(read_portfolio(directory_roots([copy]), document), pin))


def _files(devagent):
    """{path relative to OUT: bytes} for every snapshot, sample sites first."""
    made = {f"{site}.json": data for site, data in expected(devagent).items()}
    made.update({f"{FIXTURE_OUT.name}/{name}.json": data for name, data in expected_fixtures().items()})
    return made


def _extra(made):
    """Snapshot files on disk that no site or fixture project accounts for."""
    found = []
    for folder, prefix in ((OUT, ""), (FIXTURE_OUT, f"{FIXTURE_OUT.name}/")):
        if folder.is_dir():
            found += [prefix + p.name for p in sorted(folder.iterdir())
                      if p.is_file() and prefix + p.name not in made]
    return found


def _say(lines):
    for line in lines:
        print(f"update_expected_leaves: {line}")


def run(devagent, check) -> int:
    made = _files(devagent)
    if check:
        lines = sample_portfolio.compare((rel, OUT / rel, data) for rel, data in made.items())
        lines += [f"{rel}: extra" for rel in _extra(made)]
        # Sample files, then the fixture set: the order the files are listed in.
        lines.sort(key=lambda line: line.startswith(f"{FIXTURE_OUT.name}/"))
        _say(lines or [f"{len(made)} snapshots match"])
        return 1 if lines else 0
    for rel, data in made.items():
        path = OUT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    extra = [f"{rel}: extra" for rel in _extra(made)]
    _say([f"wrote {len(made)} snapshots", *extra])
    return 1 if extra else 0


def build_parser():
    parser = argparse.ArgumentParser(prog="update_expected_leaves", description=__doc__.split("\n\n")[0])
    parser.add_argument("--devagent", help="a devagent checkout (default: GANTRY_DEVAGENT_DIR)")
    parser.add_argument("--check", action="store_true", help="compare, write nothing")
    return parser


def parse_args(argv=None):
    """The arguments; a usage error exits 2."""
    parser = build_parser()
    args = parser.parse_args(argv)
    # Read here, not at import: a test or a caller may set the variable later.
    args.devagent = args.devagent or os.environ.get("GANTRY_DEVAGENT_DIR")
    return parser, args


def main(argv=None):
    parser, args = parse_args(argv)
    if not args.devagent:
        parser.error("--devagent is required when GANTRY_DEVAGENT_DIR is not set")
    devagent = Path(os.path.abspath(args.devagent))
    try:
        sample_portfolio.require_pinned(devagent)
        return run(devagent, args.check)
    except sample_portfolio.Preflight as e:
        print(f"update_expected_leaves: {e}", file=sys.stderr)
        return 2
    except (sample_portfolio.Refused, PortfolioLoadError) as e:
        print(f"update_expected_leaves: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
