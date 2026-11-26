"""Make devagent's portfolio JSON with devagent's own aggregator (#83).

    python3 tools/sample_portfolio.py [--devagent DIR] [--samples DIR]
    python3 tools/sample_portfolio.py --fixtures [--devagent DIR] [--check]

The first form writes the sample document: it rebuilds the git-ignored folder
samples/devagent/out/ (a devagent home holding a copy of
samples/devagent/config.toml) and runs scripts/portfolio.sh of the devagent
checkout --devagent (default: GANTRY_DEVAGENT_DIR) with that home and with
samples/devagent/ as the working directory, so devagent and Gantry resolve a
relative devdoc_dir alike. The document is out/portfolio/portfolio.json. An
existing out/ is deleted only when it is not a link, git ignores it and git
tracks no file in it.

--fixtures writes tests/fixtures/portfolio/written.json, the aggregator's
document over the WBS texts in FIXTURE_SOURCES with `generated_at` set to a
fixed time, and provenance.json beside it; then samples.json, the
aggregator's document over a copy of samples/ with the same fixed time, and
samples_provenance.json, the sha256 of each input it read (#84). --check
writes nothing: it regenerates all four and prints one line per file that
differs or is missing. Run --fixtures before tools/update_expected_leaves.py
when the pin moves.

Exit 0 done; 1 the aggregator failed, a folder was refused, or --check found
a difference; 2 a usage error, no bash, or --devagent is not a devagent
checkout at the pinned release. The aggregator runs offline with five environment names (PATH,
HOME, DA_HOME, TZ, LC_ALL) and every path made absolute first. No absolute
path reaches any file this tool writes. docs/worksite-page.md states the
rules.

It imports nothing from gantry, so it runs before any install.
"""

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:  # `python -P` leaves the script's folder off
    sys.path.insert(0, str(ROOT / "tools"))

# read_pin, Refused and Preflight are re-exported: tools/update_expected_leaves.py takes them from here.
from devagent_record_fixtures import (  # noqa: E402
    Preflight,
    Refused,
    checkout_version,
    find_bash,
    posix,
    read_pin,
    run_bash,
    writer_env,
)

SAMPLES = ROOT / "samples"
FIXTURES = ROOT / "tests" / "fixtures" / "portfolio"
WRITTEN = FIXTURES / "written.json"
PROVENANCE = FIXTURES / "provenance.json"
# The sample document, committed so the sample-site tests run without a checkout (#84).
SAMPLES_WRITTEN = FIXTURES / "samples.json"
SAMPLES_PROVENANCE = FIXTURES / "samples_provenance.json"
# Where the aggregator writes its document, under the devagent home.
DOCUMENT = ("portfolio", "portfolio.json")
OUT = "devagent/out"  # the sample devagent home, relative to the samples folder
FIXED_TIME = "2026-01-12T09:30:00Z"
NOT_CHECKOUT = "--devagent is not a devagent checkout (no .claude-plugin/plugin.json version)"

# One project per WBS text, in this order: the shape fixtures the stopgap
# parser is tested with, then the shapes where the two readers differ.
FIXTURE_SOURCES = {
    **{name: f"tests/fixtures/wbs/{name}.md" for name in (
        "cycle", "empty", "fences", "ids", "nesting", "not_utf8", "page", "priority", "refs", "rows", "states")},
    "est": "tests/fixtures/inputs/est.md",
    "h_values": "tests/fixtures/attention/h_values.md",
    "edges": "tests/fixtures/portfolio/edges.md",
    # The fires tests' lines (#12): line 1 one clean leaf, then each P0 and STUCK shape they restate.
    "fires": "tests/fixtures/portfolio/fires.md",
}

# run_bash runs from, and writes its driver into, the folder it is given. So
# the aggregator gets a private folder there and this script moves to the
# working directory itself, with nothing written beside the caller's files.
RUNNER = 'cd -- "$1" || exit 97\nexec bash "$2"\n'


def _absolute(path):
    return Path(os.path.abspath(path))


def _absolute_path_list(value):
    """PATH with every entry absolute: a relative one would re-resolve after the cd."""
    return os.pathsep.join(os.path.abspath(entry) for entry in value.split(os.pathsep) if entry)


def run_aggregator(devagent, cwd, da_home) -> Path:
    """Run `<devagent>/scripts/portfolio.sh` in `cwd` with `da_home` as devagent's home; the document's path.

    The script gets no argument and exactly PATH, HOME (`<da_home>/home`),
    DA_HOME, TZ=UTC and LC_ALL=C.UTF-8. A non-zero exit or a missing
    document raises Refused with the exit code and what the script printed.
    """
    devagent, cwd, da_home = _absolute(devagent), _absolute(cwd), _absolute(da_home)
    document = da_home.joinpath(*DOCUMENT)
    with tempfile.TemporaryDirectory(prefix="sample-portfolio-") as scratch:
        tmp = Path(scratch)
        env = writer_env(tmp)
        env.update(PATH=_absolute_path_list(env["PATH"]), HOME=posix(da_home / "home"), DA_HOME=posix(da_home))
        bash = os.path.abspath(find_bash(env))
        proc = run_bash(bash, tmp, env, RUNNER, [posix(cwd), posix(devagent / "scripts" / "portfolio.sh")])
    if proc.returncode != 0:
        said = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        raise Refused(f"devagent's portfolio.sh exited {proc.returncode}: {said}")
    if not document.is_file():
        raise Refused("devagent's portfolio.sh exited 0 and wrote no document")
    return document


def _is_link(path) -> bool:
    """A symlink, or a junction (which os.path.islink misses before 3.12)."""
    st = os.lstat(path)
    tag = getattr(st, "st_reparse_tag", 0)
    return stat.S_ISLNK(st.st_mode) or tag == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1)


def _git(folder, *args):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    try:
        return subprocess.run(["git", "-C", str(folder), *args], env=env, stdin=subprocess.DEVNULL,
                              capture_output=True)
    except OSError:
        return None


def require_pinned(devagent):
    """Preflight unless `devagent` is a devagent checkout at the release tests/devagent.pin names."""
    found = checkout_version(devagent)
    if found is None:
        raise Preflight(NOT_CHECKOUT)
    tag = read_pin()
    if tag != f"devagent--v{found}":
        raise Preflight(f"--devagent is devagent {found}; tests/devagent.pin names {tag}")


def guard_deletable(base, rel, label) -> Path:
    """`<base>/<rel>`, or Refused; deletes nothing. `label` names it in a refusal.

    The checks made before a generated folder is deleted (#12 shares them with
    tools/demo_build.py's devagent fetch folder): neither `base`, nor any folder
    from it down to the target, is a link; git, run in `base`, ignores the
    target (a .gitignore or the clone's .git/info/exclude) and tracks no file in it.
    """
    base = _absolute(base)
    target = base.joinpath(*Path(rel).parts)
    walk = base
    for part in ("", *Path(rel).parts):
        walk = walk / part
        if os.path.lexists(walk) and _is_link(walk):
            raise Refused(f"{label} is, or is under, a link; it is not deleted")
    cannot = Refused(f"cannot ask git whether {label} is ignored; it is not deleted")
    probe = f"{Path(rel).as_posix()}/.probe"
    ran = _git(base, "-c", f"core.excludesFile={os.devnull}", "check-ignore", "-q", "--no-index", "--", probe)
    if ran is None or ran.returncode not in (0, 1):
        raise cannot
    if ran.returncode == 1:
        raise Refused(f"git does not ignore {label}; it is not deleted")
    ran = _git(base, "ls-files", "-z", "--", Path(rel).as_posix())
    if ran is None or ran.returncode != 0:
        raise cannot
    if ran.stdout:
        raise Refused(f"git tracks a file in {label}; it is not deleted")
    return target


def guard_out(samples) -> Path:
    """`<samples>/devagent/out`, or Refused; deletes nothing (guard_deletable's checks,
    the folder above it included: tools/demo_build.py makes the same before it deletes the demo output)."""
    return guard_deletable(_absolute(samples) / "devagent", "out", OUT)


def make_sample(devagent, samples=SAMPLES) -> Path:
    """Rebuild `<samples>/devagent/out/` and write the sample document there; its path."""
    samples = _absolute(samples)
    home = samples / "devagent"
    out = home / "out"
    try:
        config = (home / "config.toml").read_bytes()
    except OSError as e:
        raise Refused(f"cannot read devagent/config.toml in the samples folder: {e.strerror}") from None
    if os.path.lexists(out):
        shutil.rmtree(guard_out(samples))
    (out / "home").mkdir(parents=True)
    (out / "config.toml").write_bytes(config)
    return run_aggregator(devagent, home, out)


def on_samples_copy(devagent, read, samples=SAMPLES):
    """`read(copy, document)` where `copy` is a temporary copy of `samples`
    (every folder named out left behind) and `document` the sample document
    make_sample wrote there; the copy is gone when this returns."""
    with tempfile.TemporaryDirectory(prefix="sample-portfolio-copy-") as scratch:
        copy = Path(scratch) / "samples"
        shutil.copytree(_absolute(samples), copy, ignore=shutil.ignore_patterns("out"))
        return read(copy, make_sample(devagent, copy))


def dumps(doc) -> bytes:
    """devagent's own form (scripts/lib/portfolio.py, dumps): UTF-8, LF, two-space indent."""
    return (json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _load(path):
    return json.loads(path.read_bytes().decode("utf-8"))


def written_doc():
    """tests/fixtures/portfolio/written.json, parsed: a fresh copy on every call, so a caller may change it."""
    return _load(WRITTEN)


def sample_doc():
    """tests/fixtures/portfolio/samples.json, parsed: a fresh copy on every call."""
    return _load(SAMPLES_WRITTEN)


def compare(items, differs="differs"):
    """One line per (label, path, bytes) whose file is missing or holds other bytes."""
    lines = []
    for label, path, data in items:
        if not path.is_file():
            lines.append(f"{label}: missing")
        elif path.read_bytes() != data:
            lines.append(f"{label}: {differs}")
    return lines


def fixture_document(devagent) -> bytes:
    """The aggregator's document over FIXTURE_SOURCES, `generated_at` fixed.

    Each source is a byte copy at `site/<project>/WBS.md` in a temporary
    folder, which is the working directory and holds its own devagent home.
    """
    with tempfile.TemporaryDirectory(prefix="portfolio-fixture-") as scratch:
        cwd = Path(scratch)
        da_home = cwd / "da"
        (da_home / "home").mkdir(parents=True)
        tables = []
        for name, source in FIXTURE_SOURCES.items():
            folder = cwd / "site" / name
            folder.mkdir(parents=True)
            (folder / "WBS.md").write_bytes((ROOT / source).read_bytes())
            tables.append(f'[project.{name}]\ndevdoc_dir = "site/{name}"\n')
        (da_home / "config.toml").write_bytes("\n".join(tables).encode("utf-8"))
        document = run_aggregator(devagent, cwd, da_home)
        doc = json.loads(document.read_bytes().decode("utf-8"))
    doc["generated_at"] = FIXED_TIME
    return dumps(doc)


def _provenance(devagent, document, key, value) -> bytes:
    doc = json.loads(document.decode("utf-8"))
    return dumps({"pin": read_pin(), "devagent_version": checkout_version(devagent),
                  "schema_version": doc["schema_version"], "generated_at": doc["generated_at"], key: value})


def provenance(devagent, document=None) -> bytes:
    """Where written.json came from; `document` is fixture_document's bytes, made here when not given."""
    if document is None:
        document = fixture_document(devagent)
    return _provenance(devagent, document, "sources", dict(FIXTURE_SOURCES))


def sample_fixture_document(devagent, samples=SAMPLES) -> bytes:
    """The aggregator's document over a copy of `samples`, `generated_at` fixed."""
    doc = on_samples_copy(devagent, lambda copy, document: json.loads(document.read_bytes().decode("utf-8")),
                          samples)
    doc["generated_at"] = FIXED_TIME
    return dumps(doc)


def sample_inputs(samples=SAMPLES) -> dict:
    """{"samples/<path>": sha256 hex or None}: the devagent config, then each
    configured project's WBS.md in config order (None when it has none)."""
    samples = _absolute(samples)
    home = samples / "devagent"
    config = home / "config.toml"
    inputs = {"samples/devagent/config.toml": hashlib.sha256(config.read_bytes()).hexdigest()}
    for name, table in tomllib.loads(config.read_bytes().decode("utf-8"))["project"].items():
        folder = Path(os.path.normpath(home / table["devdoc_dir"]))
        if not folder.is_dir() or not folder.is_relative_to(samples):
            raise Refused(f"project {name}: devdoc_dir is not a folder of the samples")
        wbs = folder / "WBS.md"
        key = "samples/" + wbs.relative_to(samples).as_posix()
        inputs[key] = hashlib.sha256(wbs.read_bytes()).hexdigest() if wbs.is_file() else None
    return inputs


def stale_inputs(samples=SAMPLES) -> list[str]:
    """The inputs whose hash differs from samples_provenance.json, or that only one side has."""
    now = sample_inputs(samples)
    then = _load(SAMPLES_PROVENANCE)["inputs"]
    return sorted(path for path in now.keys() | then.keys() if now.get(path, 0) != then.get(path, 0))


def sample_provenance(devagent, document) -> bytes:
    """What samples.json was made from; `document` is sample_fixture_document's bytes."""
    return _provenance(devagent, document, "inputs", sample_inputs())


def _counts(document):
    doc = json.loads(document.decode("utf-8"))
    return (f"{len(doc['projects'])} projects, {sum(len(p['nodes']) for p in doc['projects'])} nodes, "
            f"{len(doc['parse_report'])} report entries")


def _fixtures(devagent, check):
    document = fixture_document(devagent)
    samples = sample_fixture_document(devagent)
    made = {WRITTEN: document, PROVENANCE: provenance(devagent, document),
            SAMPLES_WRITTEN: samples, SAMPLES_PROVENANCE: sample_provenance(devagent, samples)}
    if not check:
        for path, data in made.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        print(f"sample_portfolio: wrote {WRITTEN.name} ({_counts(document)}) and {PROVENANCE.name} "
              f"and {SAMPLES_WRITTEN.name} ({_counts(samples)}) and {SAMPLES_PROVENANCE.name}")
        return 0
    lines = compare(((path.name, path, data) for path, data in made.items()), "differs from what devagent writes")
    for line in lines:
        print(f"sample_portfolio: {line}")
    if lines:
        return 1
    print(f"sample_portfolio: {len(made)} fixture files match")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="sample_portfolio", description=__doc__.split("\n\n")[0])
    parser.add_argument("--devagent", help="a devagent checkout (default: GANTRY_DEVAGENT_DIR)")
    parser.add_argument("--samples", help="the samples folder (default: this checkout's samples/)")
    parser.add_argument("--fixtures", action="store_true", help="write the test fixture, not the sample document")
    parser.add_argument("--check", action="store_true", help="with --fixtures: compare, write nothing")
    return parser


def parse_args(argv=None):
    """The arguments, with the rules between flags applied; a usage error exits 2."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.check and not args.fixtures:
        parser.error("--check needs --fixtures")
    if args.fixtures and args.samples:
        parser.error("--samples cannot be used with --fixtures")
    # Read here, not at import: a test or a caller may set the variable later.
    args.devagent = args.devagent or os.environ.get("GANTRY_DEVAGENT_DIR")
    return parser, args


def main(argv=None):
    parser, args = parse_args(argv)
    if not args.devagent:
        parser.error("--devagent is required when GANTRY_DEVAGENT_DIR is not set")
    devagent = _absolute(args.devagent)
    try:
        require_pinned(devagent)
        if args.fixtures:
            return _fixtures(devagent, args.check)
        samples = _absolute(args.samples) if args.samples else SAMPLES
        document = make_sample(devagent, samples)
        print(f"sample_portfolio: wrote {document.relative_to(samples).as_posix()}")
        return 0
    except Preflight as e:
        print(f"sample_portfolio: {e}", file=sys.stderr)
        return 2
    except Refused as e:
        print(f"sample_portfolio: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
