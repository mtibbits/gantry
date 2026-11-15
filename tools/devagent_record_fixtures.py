"""Write devagent's per-issue record fixtures with devagent's own scripts (#31).

    python3 tools/devagent_record_fixtures.py [--devagent DIR] [--out DIR]
        [--samples DIR] [--commit SHA] [--check]

Runs each recipe in RECIPES (devagent's checklist-init.sh, checklist-mark.sh,
stuck.sh, checklist-stuck.sh, park.sh, resume.sh and revise.sh) against the
devagent checkout --devagent (default: GANTRY_DEVAGENT_DIR), in a throwaway
devagent home with one configured project, offline. Every timestamp the
writers put in a checklist.md or STUCK file is replaced with a fixed one in
the writer's own form, and the files are written to --out/<Issue-N>/ (default
tests/fixtures/devagent_records/written) with a provenance.json beside them.
--samples DIR also copies each sample recipe's checklist.md and STUCK into
DIR/<Issue-N>/. --check writes nothing: it regenerates into a temporary folder
and compares, masking the timestamps on both sides, and exits 1 on any
difference, one line each. A failed preflight (no bash, a missing program, a
python3 older than 3.11, a host trust policy) exits 2; a writer that behaves
other than its recipe expects exits 1. No absolute path reaches any file this
tool writes. docs/devagent-formats.md states the rules.

It imports nothing from gantry, so it runs before any install.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePath
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / "tests" / "devagent.pin"
DEFAULT_OUT = ROOT / "tests" / "fixtures" / "devagent_records" / "written"
RECORD_FILES = ("checklist.md", "STUCK")
PROVENANCE = "provenance.json"
PROJECT = "harbor"
DIR = "<dir>"
CREATE = "create"
STUCK_WRITERS = ("stuck.sh", "checklist-stuck.sh")


class Recipe(NamedTuple):
    issue: str
    writer: str
    steps: tuple
    sample: bool
    expected_exit: int = 0
    refused: bool = False  # an expected exit that writes nothing (no STUCK)

    @property
    def leaves_stuck(self):
        return self.writer in STUCK_WRITERS and not self.refused


INIT = ("checklist-init.sh", "--project", PROJECT, DIR)


def mark(n, glyph, name):
    return ("checklist-mark.sh", DIR, str(n), glyph, name)


PULLED = (INIT, mark(0, "x", "pull"))
PARKED = PULLED + (("park.sh", PROJECT, "{issue}"),)

RECIPES = (
    Recipe("Issue-6", "checklist-mark.sh", PULLED + (mark(2, "~", "draft"),), sample=True),
    Recipe("Issue-7", "checklist-mark.sh", PULLED, sample=True),
    Recipe("Issue-8", "checklist-mark.sh", PULLED, sample=True),
    Recipe("Issue-12", "park.sh", PARKED, sample=True),
    Recipe("Issue-13", "stuck.sh",
           PULLED + (("stuck.sh", PROJECT, "The harbour crane is waiting for a spare part."),), sample=True),
    Recipe("Issue-14", "checklist-stuck.sh",
           PULLED + (("checklist-stuck.sh", DIR, "The tide table for the north quay is missing."),), sample=True),
    Recipe("Issue-15", "checklist-init.sh", (INIT,), sample=True),
    # stuck.sh cannot write a marker at step 0: `last_good` is unset under set -u.
    Recipe("Issue-21", "stuck.sh", (INIT, ("stuck.sh", PROJECT, "Nothing has run yet.")), sample=False,
           expected_exit=1, refused=True),
    Recipe("Issue-22", "checklist-stuck.sh", (INIT, ("checklist-stuck.sh", DIR, "The berth plan has no owner yet.")),
           sample=False),
    Recipe("Issue-23", "resume.sh", PARKED + (("resume.sh", PROJECT, "{issue}"),), sample=False),
    Recipe("Issue-24", "revise.sh",
           PARKED + ((CREATE, DIR + "/revisions/r1/comments.md"), ("revise.sh", PROJECT, "{issue}", "--no-chain")),
           sample=False),
    # A reason of two lines: both writers write the whole file and mark the
    # step, then log_append refuses the message and the script exits 1.
    Recipe("Issue-25", "stuck.sh",
           PULLED + (("stuck.sh", PROJECT, "The ferry timetable changed.\nThe second line names the new pier."),),
           sample=False, expected_exit=1),
    Recipe("Issue-26", "checklist-stuck.sh",
           PULLED + (("checklist-stuck.sh", DIR, "The pilot boat is late.\nCreated:     not the key"),),
           sample=False, expected_exit=1),
)

# Fixed timestamps, one per writer form, dated before the samples' as_of.
FIXED_ISO = b"2026-01-12T09:30:00+00:00"
FIXED_MINUTES = b"2026-01-12 09:30"
ISO = rb"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}"
MINUTES = rb"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}"
HEADER_RE = re.compile(rb"^(Created: )" + ISO + rb"$", re.M)
LOG_LINE_RE = re.compile(rb"^(- )" + MINUTES + rb"(  )")
STUCK_CREATED_RE = re.compile(rb"^(Created:     )(?:(" + ISO + rb")|(" + MINUTES + rb"))$")


class Refused(Exception):
    """A reason to exit 1."""


class Preflight(Exception):
    """A reason to exit 2."""


def fix_timestamps(name, data, token=None):
    """Replace each writer timestamp in a record file's bytes.

    With no token each becomes the fixed value in its own form; with a token
    (the contract test's b"<TS>") each becomes the token. The sites: a
    checklist's `Created:` header, the `- YYYY-MM-DD HH:MM  ` stamp of each
    line under `## Log`, and a STUCK file's last line when it is a `Created:`
    line of either form. A site of another shape is left as written.
    """
    iso, minutes = (FIXED_ISO, FIXED_MINUTES) if token is None else (token, token)
    if name == "STUCK":
        lines = data.split(b"\n")
        last = len(lines) - 1
        while last >= 0 and lines[last] == b"":
            last -= 1
        if last >= 0:
            match = STUCK_CREATED_RE.match(lines[last])
            if match:
                lines[last] = match.group(1) + (iso if match.group(2) else minutes)
        return b"\n".join(lines)
    data = HEADER_RE.sub(lambda m: m.group(1) + iso, data, count=1)
    lines = data.split(b"\n")
    in_log = False
    for i, line in enumerate(lines):
        if line.startswith(b"## "):
            in_log = line.rstrip() == b"## Log"
            continue
        if in_log:
            lines[i] = LOG_LINE_RE.sub(lambda m: m.group(1) + minutes + m.group(2), line, count=1)
    return b"\n".join(lines)


def read_pin():
    return PIN.read_bytes().decode("ascii").rstrip("\n")


def checkout_version(devagent):
    try:
        data = json.loads((Path(devagent) / ".claude-plugin" / "plugin.json").read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) else None


def checkout_commit(devagent):
    """HEAD of a git checkout (a clone at the tag), or None."""
    if not (Path(devagent) / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "-C", str(devagent), "rev-parse", "HEAD"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    commit = out.stdout.decode("ascii", "replace").strip()
    return commit if re.fullmatch(r"[0-9a-f]{40}", commit) else None


def posix(path):
    return PurePath(path).as_posix()


PREFLIGHT = r"""
for program in awk sed mktemp date chmod python3; do
  command -v "$program" >/dev/null 2>&1 || { echo "$program is not on PATH"; exit 1; }
done
date -Iseconds >/dev/null 2>&1 || { echo "date -Iseconds failed (devagent needs GNU date)"; exit 1; }
probe="$(mktemp -d)" || { echo "mktemp -d failed"; exit 1; }
: > "$probe/a"; : > "$probe/b"
chmod --reference="$probe/a" "$probe/b" 2>/dev/null || { rm -rf "$probe"; echo "chmod --reference failed (devagent needs GNU chmod)"; exit 1; }
rm -rf "$probe"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "python3 is older than 3.11 (devagent reads its config with tomllib)"; exit 1; }
source "$1/scripts/lib/trust.sh" || { echo "cannot load devagent's trust library"; exit 1; }
trust_locate_host || { echo "devagent's host trust policy lookup failed"; exit 1; }
if [ "${TRUST_HOST:-0}" = 1 ]; then echo "a devagent host trust policy is installed; the writers would consult it"; exit 1; fi
exit 0
"""


# Arguments reach the writer through the environment, not a command line:
# a Windows process's command line reaches MSYS bash split at newlines and
# with quotes mangled, and a two-line reason must arrive whole.
DRIVER = r"""
args=()
i=0
while [ "$i" -lt "$FIXTURE_ARGC" ]; do
  name="FIXTURE_ARG_$i"
  args+=("${!name}")
  unset "$name"
  i=$((i + 1))
done
script="$FIXTURE_SCRIPT"
unset FIXTURE_ARGC FIXTURE_SCRIPT
exec bash "$script" "${args[@]}"
"""


def run_bash(bash, tmp, env, script, args=()):
    """Run `script` (a Path, or bash text) with `args`, both carried in the environment."""
    driver = tmp / "driver.sh"
    driver.write_bytes(DRIVER.encode("utf-8"))
    if isinstance(script, str):
        inline = tmp / "inline.sh"
        inline.write_bytes(script.encode("utf-8"))
        script = inline
    carried = {**env, "FIXTURE_SCRIPT": posix(script), "FIXTURE_ARGC": str(len(args))}
    carried.update({f"FIXTURE_ARG_{i}": arg for i, arg in enumerate(args)})
    return subprocess.run([bash, posix(driver)], cwd=tmp, env=carried, capture_output=True)


def writer_env(tmp, issue=None):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": posix(tmp / "home"), "DA_HOME": posix(tmp / "da"),
           "TZ": "UTC", "LC_ALL": "C.UTF-8"}
    if issue is not None:
        env["DEVAGENT_ACTIVE_ISSUE"] = issue
    return env


def find_bash(env):
    bash = shutil.which("bash", path=env["PATH"])
    if bash is None:
        raise Preflight("bash is not on PATH")
    return bash


def preflight(devagent, tmp):
    env = writer_env(tmp)
    bash = find_bash(env)
    proc = run_bash(bash, tmp, env, PREFLIGHT, [posix(devagent)])
    if proc.returncode != 0:
        text = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip() or "the preflight failed"
        raise Preflight(f"{text} (bash: {bash})")
    return bash


def config_text(plan_dir):
    return ("[defaults]\n"
            'checklist_template = "standard"\n'
            "\n"
            f"[project.{PROJECT}]\n"
            f'devdoc_dir = "{posix(plan_dir)}"\n'
            'default_baseline = "origin/main"\n')


def log_lines(path):
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    _, _, log = data.partition(b"\n## Log\n")
    return [line for line in log.split(b"\n") if line.startswith(b"- ")]


def concrete(step, issue):
    return [part.replace(DIR, f"plan/{issue}").replace("{issue}", issue) for part in step]


def run_recipe(recipe, devagent, bash, work):
    """Run one recipe in a fresh devagent home under `work`; the issue folder."""
    tmp = work / recipe.issue
    (tmp / "home").mkdir(parents=True)
    (tmp / "da").mkdir()
    (tmp / "plan").mkdir()
    (tmp / "da" / "config.toml").write_bytes(config_text(tmp / "plan").encode("utf-8"))
    issue_dir = tmp / "plan" / recipe.issue
    issue_dir.mkdir()
    checklist = issue_dir / "checklist.md"
    for index, step in enumerate(recipe.steps):
        last = index == len(recipe.steps) - 1
        argv = concrete(step, recipe.issue)
        if argv[0] == CREATE:
            target = tmp / argv[1]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"")
            continue
        want = recipe.expected_exit if last else 0
        if want != 0:
            before_bytes = checklist.read_bytes() if checklist.exists() else None
            before_log = log_lines(checklist)
        env = writer_env(tmp, recipe.issue if argv[0] == "stuck.sh" else None)
        proc = run_bash(bash, tmp, env, Path(devagent) / "scripts" / argv[0], argv[1:])
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        if proc.returncode != want:
            raise Refused(f"{recipe.issue}: {argv[0]} exited {proc.returncode}, expected {want}: {stderr}")
        if want == 0:
            continue
        # An expected non-zero exit passes on its side effects only.
        stuck = issue_dir / "STUCK"
        if recipe.refused:
            if stuck.exists() or checklist.read_bytes() != before_bytes:
                raise Refused(f"{recipe.issue}: {argv[0]} was expected to refuse and write nothing: {stderr}")
        else:
            rows = checklist.read_bytes().split(b"\n")
            if not stuck.exists() or not any(r.startswith(b"- [!]") for r in rows) \
                    or log_lines(checklist) != before_log:
                raise Refused(f"{recipe.issue}: {argv[0]} was expected to write STUCK, mark the step and add "
                              f"no log line before exiting {want}: {stderr}")
    return issue_dir


def provenance(recipe, tag, commit, version):
    data = {"pin": tag, "devagent_commit": commit, "devagent_version": version, "writer": recipe.writer,
            "expected_exit": recipe.expected_exit,
            "steps": [[part.replace("{issue}", recipe.issue) for part in step] for step in recipe.steps]}
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def regenerate(devagent_dir, out, commit):
    """Run every recipe against `devagent_dir`; write the fixtures under `out`."""
    # Resolved here: every writer runs with the temp folder as its cwd.
    devagent = Path(devagent_dir).resolve()
    version = checkout_version(devagent)
    if version is None:
        raise Preflight("--devagent is not a devagent checkout (no .claude-plugin/plugin.json version)")
    tag = read_pin()
    out = Path(out)
    with tempfile.TemporaryDirectory(prefix="devagent-fixtures-") as scratch:
        work = Path(scratch)
        bash = preflight(devagent, work)
        for recipe in RECIPES:
            issue_dir = run_recipe(recipe, devagent, bash, work)
            target = out / recipe.issue
            target.mkdir(parents=True, exist_ok=True)
            for name in RECORD_FILES:
                source = issue_dir / name
                if source.exists():
                    (target / name).write_bytes(fix_timestamps(name, source.read_bytes()))
            (target / PROVENANCE).write_bytes(provenance(recipe, tag, commit, version))
    return out


class Differences(list):
    """compare()'s messages; `compared` is the number of record files compared."""

    compared = 0


def _record_set(folder):
    found = set()
    if Path(folder).is_dir():
        for sub in sorted(Path(folder).iterdir()):
            if sub.is_dir():
                found.update((sub.name, name) for name in RECORD_FILES if (sub / name).is_file())
    return found


def _provenance(folder, issue):
    try:
        return json.loads((Path(folder) / issue / PROVENANCE).read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return {}


def _line(lines, n):
    return lines[n].decode("utf-8", "replace") if n < len(lines) else None


def compare(fixtures_dir, generated_dir):
    """Messages naming each difference between committed and regenerated fixtures.

    checklist.md and STUCK are compared byte for byte with every timestamp
    masked; provenance.json on `writer` and `expected_exit` only.
    """
    diffs = Differences()
    committed = _record_set(fixtures_dir)
    generated = _record_set(generated_dir)
    diffs.compared = len(committed)
    issues = sorted({i for i, _ in committed | generated}, key=_issue_key)
    kept_by = {issue: _provenance(fixtures_dir, issue) for issue in issues}
    made_by = {issue: _provenance(generated_dir, issue) for issue in issues}
    for issue, name in sorted(committed | generated, key=lambda k: (_issue_key(k[0]), k[1])):
        made = made_by[issue]
        writer = made.get("writer")
        version = made.get("devagent_version")
        if (issue, name) not in generated:
            if not made:
                diffs.append(f"{issue}/{name}: committed but written by no recipe")
            else:
                diffs.append(f"{issue}/{name}: not written by {writer} at devagent {version}")
            continue
        if (issue, name) not in committed:
            diffs.append(f"{issue}/{name}: written by {writer} at devagent {version} but not committed")
            continue
        a = fix_timestamps(name, (Path(fixtures_dir) / issue / name).read_bytes(), token=b"<TS>").split(b"\n")
        b = fix_timestamps(name, (Path(generated_dir) / issue / name).read_bytes(), token=b"<TS>").split(b"\n")
        if a != b:
            # On the bytes: two lines can differ only in bytes that decode alike.
            n = next(i for i in range(max(len(a), len(b))) if a[i:i + 1] != b[i:i + 1])
            diffs.append(f"{issue}/{name}: written by {writer} at devagent {version}: first differing line {n + 1}: "
                         f"fixture {_line(a, n)!r}, devagent {_line(b, n)!r}")
    for issue in issues:
        kept, made = kept_by[issue], made_by[issue]
        if not made:
            continue
        for key in ("writer", "expected_exit"):
            if kept.get(key) != made.get(key):
                diffs.append(f"{issue}/{PROVENANCE}: {key}: fixture {kept.get(key)!r}, devagent {made.get(key)!r}")
    return diffs


def _issue_key(issue):
    match = re.fullmatch(r"Issue-([0-9]+)", issue)
    return (0, int(match.group(1)), issue) if match else (1, 0, issue)


def check(devagent_dir, fixtures_dir=DEFAULT_OUT):
    """Regenerate into a temporary folder and compare with `fixtures_dir`."""
    with tempfile.TemporaryDirectory(prefix="devagent-check-") as scratch:
        generated = regenerate(devagent_dir, Path(scratch) / "written", commit="0" * 40)
        return compare(fixtures_dir, generated)


def write_fixtures(devagent_dir, out, commit):
    """Regenerate into a temporary folder, then replace each recipe's folder in `out`."""
    with tempfile.TemporaryDirectory(prefix="devagent-write-") as scratch:
        generated = regenerate(devagent_dir, Path(scratch) / "written", commit=commit)
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        for recipe in RECIPES:
            target = out / recipe.issue
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(generated / recipe.issue, target)
    return out


def copy_samples(out, samples):
    for recipe in RECIPES:
        if not recipe.sample:
            continue
        target = Path(samples) / recipe.issue
        target.mkdir(parents=True, exist_ok=True)
        for name in RECORD_FILES:
            source = Path(out) / recipe.issue / name
            if source.exists():
                (target / name).write_bytes(source.read_bytes())


def main(argv=None):
    parser = argparse.ArgumentParser(prog="devagent_record_fixtures", description=__doc__.split("\n\n")[0])
    parser.add_argument("--devagent", default=os.environ.get("GANTRY_DEVAGENT_DIR") or None)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--samples")
    parser.add_argument("--commit", help="the devagent commit, when --devagent is not a git checkout")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if not args.devagent:
        parser.error("--devagent is required when GANTRY_DEVAGENT_DIR is not set")
    args.devagent = Path(args.devagent).resolve()
    try:
        if args.check:
            diffs = check(args.devagent, args.out)
            for line in diffs:
                print(f"devagent_record_fixtures: {line}")
            if diffs:
                return 1
            print(f"devagent_record_fixtures: {diffs.compared} record files match")
            return 0
        commit = args.commit or checkout_commit(args.devagent)
        if commit is None or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise Preflight("cannot tell the devagent commit: --devagent is not a git checkout; pass --commit")
        write_fixtures(args.devagent, args.out, commit)
        if args.samples:
            copy_samples(args.out, args.samples)
        return 0
    except Preflight as e:
        print(f"devagent_record_fixtures: {e}", file=sys.stderr)
        return 2
    except Refused as e:
        print(f"devagent_record_fixtures: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
