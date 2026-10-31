"""Commit check against private data (#44).

One standard-library tool holds every rule, and every mode routes through the
same functions:

1. generated output, anywhere in the tree;
2. installation-config files outside samples/ and self-contained fixtures;
3. absolute local paths, in files and in commit messages;
4. the ignore rules.

`names` checks names and hostnames against a private denylist kept outside the
repo, and reports a count only. Content is read from git objects, never from
the work tree. See docs/commit-check.md.

The pattern literals below are assembled from pieces, so this file passes its
own rule 3.
"""

import argparse
import os
import posixpath
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass

SEP = "/"
BACK = "\\"
HOME_ROOTS = ("home", "Users", "root", "mnt")

# Start of line, or any character except a letter, digit, _ . - / ~ or colon.
BOUND = r"(?<![A-Za-z0-9_.\-" + SEP + "~:])"
PATH_FORMS = {
    "drive_backslash": r"(?<![A-Za-z0-9])[A-Za-z]:" + BACK * 2,
    "drive_slash": r"(?<![A-Za-z0-9])[A-Za-z]:" + SEP,
    "unc": BOUND + "(?:" + BACK * 4 + "|" + SEP * 2 + r")[A-Za-z0-9._$-]+[" + BACK * 2 + SEP + "]",
    "posix_home": BOUND + SEP + "(?:" + "|".join(HOME_ROOTS) + ")" + SEP,
    "msys_drive": BOUND + SEP + "[A-Za-z]" + SEP,
    "tilde": BOUND + "~(?:[A-Za-z][A-Za-z0-9._-]*)?" + SEP,
}
PATH_FORM_RES = {form: re.compile(pattern) for form, pattern in PATH_FORMS.items()}


def path_form_hits(text):
    """[(form, matched text)] for every absolute-path form in text."""
    return [
        (form, m.group(0))
        for form, pattern in PATH_FORM_RES.items()
        for m in pattern.finditer(text)
    ]


def line_hits(text):
    """[(line number, form, start, end)], lines numbered as git numbers them."""
    hits = []
    for lineno, line in enumerate(text.split("\n"), 1):
        for form, pattern in PATH_FORM_RES.items():
            hits += [(lineno, form, m.start(), m.end()) for m in pattern.finditer(line)]
    return hits


# --- the exemption list ----------------------------------------------------------

EXEMPT_PATH = "tools/commit_check_exempt.txt"
# Rules 1 and 2 have no exemption by design, and rule 4 is repo-level.
EXEMPTIBLE_RULES = {"absolute-path"}
FIELD_SEP = " | "


@dataclass(frozen=True)
class Exemption:
    path: str
    rule: str
    text: str
    reason: str
    lineno: int


def parse_exemptions(text):
    """(entries, errors) for the exemption list; errors are (line number, why)."""
    entries, errors = [], []
    for lineno, line in enumerate(text.split("\n"), 1):
        line = line.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = [f.strip() for f in line.split(FIELD_SEP, 3)]
        if len(fields) != 4 or not all(fields):
            errors.append((lineno, "needs four fields: path | rule | text | reason"))
        elif fields[1] not in EXEMPTIBLE_RULES:
            errors.append((lineno, "only absolute-path can be exempted"))
        elif fields[0] == EXEMPT_PATH:
            errors.append((lineno, "the exemption list cannot exempt itself"))
        else:
            entries.append(Exemption(*fields, lineno))
    return entries, errors


def is_exempt(path, line, start, end, entries):
    """True when an entry for path has an occurrence of its text spanning the hit."""
    for entry in entries:
        if entry.path != path:
            continue
        at = line.find(entry.text)
        while at != -1:
            if at <= start and end <= at + len(entry.text):
                return True
            at = line.find(entry.text, at + 1)
    return False


def entry_is_live(entry, file_text):
    """True when some line holds the entry's text with a rule-3 hit inside it."""
    for line in file_text.split("\n"):
        if entry.text not in line:
            continue
        for _, _, start, end in line_hits(line):
            if is_exempt(entry.path, line, start, end, [entry]):
                return True
    return False


# --- the repository -------------------------------------------------------------


class SetupError(Exception):
    """A usage, setup or install error: exit 2."""


def _decode(data):
    return data.decode("utf-8", "surrogateescape")


def _encode(text):
    return text.encode("utf-8", "surrogateescape")


def _nul_fields(data):
    fields = _decode(data).split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    return fields


class Repo:
    """A work tree; every git call runs at its top with the inherited environment.

    The environment is inherited, never stripped of GIT_INDEX_FILE, so a hook
    run by `git commit -a` or `git commit <path>` sees the temporary index.
    """

    def __init__(self, top, env=None):
        self.top = str(top)
        self.env = env

    @classmethod
    def find(cls, cwd=None, env=None):
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd, env=env, capture_output=True,
        )
        if result.returncode != 0:
            raise SetupError("not inside a git work tree")
        return cls(_decode(result.stdout).rstrip("\n"), env)

    def run(self, *args, input=None, check=True):
        result = subprocess.run(
            ["git", *args], cwd=self.top, env=self.env, input=input, capture_output=True,
        )
        if check and result.returncode != 0:
            raise SetupError(f"git {args[0]} failed: {_decode(result.stderr).strip()}")
        return result

    def out(self, *args, input=None):
        return _decode(self.run(*args, input=input).stdout)

    def read_objects(self, names):
        """Bytes for each object name (None when missing), through one cat-file --batch."""
        if not names:
            return []
        data = self.run("cat-file", "--batch", input=_encode("".join(n + "\n" for n in names))).stdout
        result, pos = [], 0
        for _ in names:
            nl = data.index(b"\n", pos)
            header = data[pos:nl].split(b" ")
            pos = nl + 1
            if len(header) == 3 and header[2].isdigit():
                size = int(header[2])
                result.append(data[pos:pos + size])
                pos += size + 1
            else:
                result.append(None)
        return result

    def read_file(self, rev, path):
        """A file's bytes at rev ("" for the index), or None when absent."""
        return self.read_objects([f"{rev}:{path}"])[0]

    def tracked(self):
        if not hasattr(self, "_tracked"):
            self._tracked = set(_nul_fields(self.run("ls-files", "-z").stdout))
        return self._tracked


# --- blobs ---------------------------------------------------------------------


def decode_blob(data):
    """Text for a blob, or None for binary (a NUL byte without a UTF-16 BOM)."""
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", "replace")
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", "replace")
    if b"\0" in data:
        return None
    text = data.decode("utf-8", "replace")
    return text[1:] if text.startswith("﻿") else text


# --- rule 4: the ignore rules ------------------------------------------------------

SAMPLE_CONFIG = "samples/gantry.sample.toml"
SAMPLE_ROOT = "samples"
PROBE_NAME = "commit-check-probe"
FIXED_PROBE_DIRS = ("samples/history/out", "samples/devagent/out")
MUST_NOT_IGNORE = (SAMPLE_CONFIG, "samples/devagent/config.toml")
# Absolute, rooted, drive-letter or home-relative (#42's test uses the same test).
ABSOLUTE_START_RE = re.compile("^(?:[" + SEP + BACK * 2 + "~]|[A-Za-z]:)")
NO_EXCLUDES = ("-c", f"core.excludesFile={os.devnull}")


def sample_output_dir(repo, rev):
    """The sample config's output folder, repo-relative, or None when unreadable."""
    data = repo.read_file(rev, SAMPLE_CONFIG)
    text = decode_blob(data) if data is not None else None
    if text is None:
        return None
    try:
        value = tomllib.loads(text).get("output_dir")
    except tomllib.TOMLDecodeError:
        return None
    if not isinstance(value, str) or ABSOLUTE_START_RE.match(value):
        return None
    p = posixpath.normpath(posixpath.join(SAMPLE_ROOT, value.replace(BACK, SEP)))
    return None if p in (".", "..") or p.startswith(".." + SEP) else p


def check_ignored(repo, paths):
    """{path: ignored}; ignored only by a tracked .gitignore pattern without '!'.

    check-ignore also reads .git/info/exclude, so the record's source decides;
    an empty record (no match, or a directory negation) is not ignored.
    """
    result = repo.run(
        *NO_EXCLUDES, "check-ignore", "--no-index", "-v", "-z", "--non-matching", "--stdin",
        input=_encode("".join(p + "\0" for p in paths)), check=False,
    )
    if result.returncode not in (0, 1):
        raise SetupError(f"git check-ignore failed: {_decode(result.stderr).strip()}")
    gitignores = {p for p in repo.tracked() if posixpath.basename(p) == ".gitignore"}
    fields = _nul_fields(result.stdout)
    ignored = dict.fromkeys(paths, False)
    for i in range(0, len(fields) - 3, 4):
        source, _, pattern, path = fields[i:i + 4]
        if path in ignored:
            ignored[path] = source in gitignores and bool(pattern) and not pattern.startswith("!")
    return ignored


def _probe_paths(out_dir):
    dirs = ([out_dir] if out_dir is not None else []) + list(FIXED_PROBE_DIRS)
    return [d + SEP + PROBE_NAME for d in dict.fromkeys(dirs)]


def ignore_probes(top, rev="HEAD", env=None):
    """{probe path: ignored} for every folder the ignore rules must cover."""
    repo = top if isinstance(top, Repo) else Repo(top, env)
    return check_ignored(repo, _probe_paths(sample_output_dir(repo, rev)))


def ignore_rule(repo, rev):
    """Rule 4 findings: a probe not ignored, or a config or tracked file ignored."""
    findings = []
    out_dir = sample_output_dir(repo, rev)
    if out_dir is None:
        findings.append(".gitignore: ignore-rules (sample output folder unreadable)")
    probes = _probe_paths(out_dir)
    ignored = check_ignored(repo, probes + list(MUST_NOT_IGNORE))
    findings += [f".gitignore: ignore-rules ({p} is not ignored)" for p in probes if not ignored[p]]
    bad = [p for p in MUST_NOT_IGNORE if ignored[p]]
    listed = repo.run(*NO_EXCLUDES, "ls-files", "-z", "-ci", "--exclude-per-directory=.gitignore").stdout
    bad += [p for p in _nul_fields(listed) if p not in bad]
    findings += [f".gitignore: ignore-rules ({p} is ignored but must not be)" for p in bad]
    return findings


# --- modes ---------------------------------------------------------------------


def report(findings):
    """Print the findings and the last line; the exit code."""
    for line in findings:
        print(line)
    n = len(findings)
    print(f"commit-check: {n} finding{'' if n == 1 else 's'}" if n else "commit-check: clean")
    return 1 if n else 0


def cmd_tree(repo, args):
    return report(ignore_rule(repo, "HEAD"))


COMMANDS = {"tree": cmd_tree}


def build_parser():
    parser = argparse.ArgumentParser(prog="commit_check.py", description="Commit check against private data.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tree", help="every tracked file at HEAD, rule 4 and the exemption list")
    return parser


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = build_parser().parse_args(argv)  # a usage error exits 2
    try:
        return COMMANDS[args.command](Repo.find(), args)
    except SetupError as e:
        print(f"commit-check: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
