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
import json
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


# --- rules 1 and 2: generated output and installation configs ---------------------

GENERATED_NAMES = {"week-plan.json", "build.json", "update.json"}
HTML_HOMES = ("gantry/templates/", "tests/page_script/known_bad/")
FIXTURE_ROOT = "tests/fixtures/"


def generated_output(path):
    """Rule 1: why path is generated output, or None."""
    parts = path.split(SEP)
    if path.lower().endswith(".html") and not path.startswith(HTML_HOMES):
        return "html page"
    if parts[-1].casefold() in GENERATED_NAMES:
        return "generated file name"
    if "out" in parts[:-1]:
        return "under an out/ folder"
    return None


def _walk_strings(node, dotted=""):
    """(dotted key, string) for every string leaf, in every table and array."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_strings(value, f"{dotted}.{key}" if dotted else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk_strings(value, f"{dotted}[{i}]")
    elif isinstance(node, str):
        yield dotted, node


def _resolves_inside(folder, value):
    """True when value is relative and resolves lexically inside folder."""
    if ABSOLUTE_START_RE.match(value):
        return False
    p = posixpath.normpath(posixpath.join(folder, value.replace(BACK, SEP)))
    return p == folder or p.startswith(folder + SEP)


def installation_config(path, data):
    """Rule 2: why path is a refused installation config, or None."""
    if posixpath.basename(path).lower().endswith(".local.toml"):
        return "*.local.toml"
    if not path.lower().endswith(".toml") or path.startswith(SAMPLE_ROOT + SEP):
        return None
    text = decode_blob(data)
    try:
        doc = tomllib.loads(text) if text is not None else {}
    except tomllib.TOMLDecodeError:
        return None
    if "config_version" not in doc or not ("devagent_config" in doc or "output_dir" in doc):
        return None
    if not path.startswith(FIXTURE_ROOT):
        return "outside samples/"
    folder = posixpath.dirname(path)
    for dotted, value in _walk_strings(doc):
        if not _resolves_inside(folder, value):
            return f"fixture value not self-contained: {dotted}"
    return None


def check_file(path, data, exemptions):
    """Rules 1-3 for one file version: finding lines without a commit prefix."""
    findings = []
    why = generated_output(path)
    if why:
        findings.append(f"{path}: generated-output ({why})")
    why = installation_config(path, data)
    if why:
        findings.append(f"{path}: installation-config ({why})")
    text = decode_blob(data) if path != EXEMPT_PATH else None
    if text is not None:
        lines = text.split("\n")
        for lineno, form, start, end in line_hits(text):
            if not is_exempt(path, lines[lineno - 1], start, end, exemptions):
                findings.append(f"{path}:{lineno}: absolute-path ({form})")
    return findings


def read_exemptions(repo, rev):
    """(entries, finding lines) for the exemption list at rev ("" for the index)."""
    data = repo.read_file(rev, EXEMPT_PATH)
    if data is None:
        return [], []
    entries, errors = parse_exemptions(decode_blob(data) or "")
    return entries, [f"{EXEMPT_PATH}:{n}: exemption (malformed entry)" for n, _ in errors]


# --- listings ------------------------------------------------------------------

GITLINK = "160000"


def staged_files(repo):
    """[(mode, blob id, path)] the commit adds or changes: the index against HEAD."""
    if repo.run("rev-parse", "--verify", "-q", "HEAD^{commit}", check=False).returncode != 0:
        files = []
        for record in _nul_fields(repo.run("ls-files", "-s", "-z").stdout):
            info, path = record.split("\t", 1)
            mode, oid, _ = info.split(" ")
            files.append((mode, oid, path))
    else:
        fields = _nul_fields(repo.run(
            "diff-index", "--cached", "-r", "-z", "--no-renames", "--diff-filter=d", "HEAD",
        ).stdout)
        files = []
        for i in range(0, len(fields) - 1, 2):
            _, mode, _, oid, _ = fields[i].lstrip(":").split(" ")
            files.append((mode, oid, fields[i + 1]))
    return [f for f in files if f[0] != GITLINK]


def tree_files(repo, rev):
    """[(mode, blob id, path)] for every file in rev's tree (gitlinks skipped)."""
    files = []
    for record in _nul_fields(repo.run("ls-tree", "-r", "-z", rev).stdout):
        info, path = record.split("\t", 1)
        mode, kind, oid = info.split(" ")
        if kind == "blob":
            files.append((mode, oid, path))
    return files


def stale_exemptions(repo, entries, files):
    """Finding lines for entries whose file is gone or whose text holds no flagged form."""
    oids = {path: oid for _, oid, path in files}
    present = [e for e in entries if e.path in oids]
    texts = repo.read_objects([oids[e.path] for e in present])
    live = {e: entry_is_live(e, decode_blob(t) or "") for e, t in zip(present, texts)}
    findings = []
    for entry in entries:
        if entry not in live:
            findings.append(f"{EXEMPT_PATH}:{entry.lineno}: exemption (file missing)")
        elif not live[entry]:
            findings.append(f"{EXEMPT_PATH}:{entry.lineno}: exemption (text no longer flagged)")
    return findings


def check_blobs(repo, files, exemptions):
    """check_file over [(mode, blob id, path)], read through one cat-file --batch."""
    findings = []
    for (_, _, path), data in zip(files, repo.read_objects([oid for _, oid, _ in files])):
        findings += check_file(path, data if data is not None else b"", exemptions)
    return findings


# --- commits and messages ------------------------------------------------------


def check_message(text):
    """Rule 3 over a commit message: finding lines `message:<line>: …`."""
    return [f"message:{lineno}: absolute-path ({form})" for lineno, form, _, _ in line_hits(text)]


def comment_string(repo):
    for key in ("core.commentString", "core.commentChar"):
        result = repo.run("config", "--get", key, check=False)
        value = _decode(result.stdout).rstrip("\n")
        if result.returncode == 0 and value:
            return "#" if value == "auto" else value
    return "#"


def clean_message(text, comment):
    """git's default cleanup: cut at the scissors line, blank comment lines.

    Blanked, not dropped, so line numbers still point into the file.
    """
    scissors = re.compile("^" + re.escape(comment) + " -+ >8 -+$")
    kept = []
    for line in text.split("\n"):
        if scissors.match(line.rstrip("\r")):
            break
        kept.append("" if line.startswith(comment) else line)
    return "\n".join(kept)


def parse_commit(data):
    """(parents, message) from a raw commit object."""
    header, _, message = _decode(data).partition("\n\n")
    parents = [l.split(" ", 1)[1] for l in header.split("\n") if l.startswith("parent ")]
    return parents, message


def changed_files(repo, left, commit):
    """[(mode, blob id, path)] added or changed from left to commit (two-tree form)."""
    fields = _nul_fields(repo.run(
        "diff-tree", "-r", "-z", "--no-renames", "--diff-filter=d", left, commit,
    ).stdout)
    files = []
    for i in range(0, len(fields) - 1, 2):
        _, mode, _, oid, _ = fields[i].lstrip(":").split(" ")
        if mode != GITLINK:
            files.append((mode, oid, fields[i + 1]))
    return files


def empty_tree(repo):
    return repo.out("hash-object", "-t", "tree", "--stdin", input=b"").strip()


def scan_commits(repo, commits, exemptions):
    """Rules 1-3 over each commit's own changes (vs its first parent) and its message.

    Never a combined diff, so a merge's own resolution is scanned; a root
    commit is diffed against the empty tree.
    """
    findings, empty = [], None
    for sha, data in zip(commits, repo.read_objects(commits)):
        parents, message = parse_commit(data)
        if parents:
            left = parents[0]
        else:
            empty = empty or empty_tree(repo)
            left = empty
        prefix = sha[:12] + " "
        files = changed_files(repo, left, sha)
        findings += [prefix + f for f in check_blobs(repo, files, exemptions)]
        findings += [prefix + f for f in check_message(message)]
    return findings


def verify_commit(repo, rev):
    result = repo.run("rev-parse", "--verify", "-q", rev + "^{commit}", check=False)
    if result.returncode != 0:
        raise SetupError(f"not a commit: {rev}")
    return _decode(result.stdout).strip()


def rev_list(repo, *args):
    return repo.out("rev-list", "--reverse", "--topo-order", *args).split()


def read_text_file(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise SetupError(f"cannot read {path} ({e.strerror})")
    return decode_blob(data) or ""


# --- modes ---------------------------------------------------------------------


def report(findings):
    """Print the findings and the last line; the exit code."""
    for line in findings:
        print(line)
    n = len(findings)
    print(f"commit-check: {n} finding{'' if n == 1 else 's'}" if n else "commit-check: clean")
    return 1 if n else 0


def tree_findings(repo):
    """Every tracked file at HEAD, rule 4, and the exemption list's own checks."""
    exemptions, findings = read_exemptions(repo, "HEAD")
    files = tree_files(repo, "HEAD")
    findings += check_blobs(repo, files, exemptions)
    findings += ignore_rule(repo, "HEAD")
    return findings + stale_exemptions(repo, exemptions, files)


def cmd_tree(repo, args):
    return report(tree_findings(repo))


def cmd_staged(repo, args):
    exemptions, findings = read_exemptions(repo, "")
    findings += check_blobs(repo, staged_files(repo), exemptions)
    return report(findings + ignore_rule(repo, ""))


def cmd_range(repo, args):
    base, head = verify_commit(repo, args.base), verify_commit(repo, args.head)
    exemptions, findings = read_exemptions(repo, head)
    return report(findings + scan_commits(repo, rev_list(repo, f"{base}..{head}"), exemptions))


def cmd_message(repo, args):
    text = clean_message(read_text_file(args.file), comment_string(repo))
    return report(check_message(text))


# --- ci ------------------------------------------------------------------------

ZERO_SHA = re.compile("^0+$")
WATCHED = (
    "tools/commit_check.py",
    EXEMPT_PATH,
    ".github/workflows/commit-check.yml",
)


def load_event():
    """(event name, payload) from the GitHub Actions environment."""
    name, path = os.environ.get("GITHUB_EVENT_NAME"), os.environ.get("GITHUB_EVENT_PATH")
    if not name or not path:
        raise SetupError("ci needs GITHUB_EVENT_NAME and GITHUB_EVENT_PATH")
    try:
        with open(path, encoding="utf-8") as f:
            return name, json.load(f)
    except (OSError, ValueError) as e:
        raise SetupError(f"cannot read the event payload at GITHUB_EVENT_PATH ({e})")


def _is_commit(repo, sha):
    return bool(sha) and repo.run("cat-file", "-e", sha + "^{commit}", check=False).returncode == 0


def ci_range(repo, event, payload):
    """(commits, description) the event asks the check to scan.

    pull_request: base..head. push: before..after; with before all zeros
    (a new branch) or not in the clone (a force-push), every commit not on
    origin/<default>, or the whole history for the default branch itself.
    """
    default = payload["repository"]["default_branch"]
    if event == "pull_request":
        base, head = payload["pull_request"]["base"]["sha"], payload["pull_request"]["head"]["sha"]
        if not (_is_commit(repo, base) and _is_commit(repo, head)):
            raise SetupError("the pull request's base or head commit is not in the clone")
        return rev_list(repo, f"{base}..{head}"), f"{base[:12]}..{head[:12]}"
    if event == "push":
        before, after = payload["before"], payload["after"]
        if ZERO_SHA.match(after):
            return [], "empty"
        if ZERO_SHA.match(before) or not _is_commit(repo, before):
            if payload["ref"] == f"refs/heads/{default}":
                return rev_list(repo, after), f"{after[:12]} (whole history)"
            return rev_list(repo, after, "--not", f"origin/{default}"), f"{after[:12]} --not origin/{default}"
        return rev_list(repo, f"{before}..{after}"), f"{before[:12]}..{after[:12]}"
    raise SetupError(f"ci does not know the event {event!r}")


def changed_paths(repo, left, commit):
    return set(_nul_fields(repo.run(
        "diff-tree", "-r", "-z", "--no-renames", "--name-only", left, commit,
    ).stdout))


def self_changes(repo, commits, default):
    """WATCHED paths the range's own commits (those not on origin/<default>) change.

    A merge counts a path only when it differs from every parent, so merging
    main forward does not count main's own changes.
    """
    ref = f"refs/remotes/origin/{default}"
    if repo.run("rev-parse", "--verify", "-q", ref, check=False).returncode == 0:
        stdin = _encode("".join(c + "\n" for c in commits) + f"^{ref}\n")
        own = set(repo.out("rev-list", "--stdin", input=stdin).split()) if commits else set()
    else:
        own = set(commits)
    touched, empty = set(), None
    for sha, data in zip(commits, repo.read_objects(commits)):
        if sha not in own:
            continue
        parents, _ = parse_commit(data)
        if not parents:
            empty = empty or empty_tree(repo)
            parents = [empty]
        touched |= set.intersection(*(changed_paths(repo, p, sha) for p in parents))
    return [p for p in WATCHED if p in touched]


def cmd_ci(repo, args):
    event, payload = load_event()
    try:
        commits, description = ci_range(repo, event, payload)
        default = payload["repository"]["default_branch"]
    except (KeyError, TypeError) as e:
        raise SetupError(f"the {event} payload lacks a field the range needs ({e})")
    print(f"ci: range {description}")
    findings = tree_findings(repo)  # also reports a malformed exemption list
    exemptions, _ = read_exemptions(repo, "HEAD")
    findings += scan_commits(repo, commits, exemptions)
    changed = self_changes(repo, commits, default)
    if changed:
        print(
            f"::warning title=Commit check changed::This range changes {', '.join(changed)}; "
            "changes to the check need explicit review (docs/commit-check.md)"
        )
    return report(findings)


# --- names: the private denylist ------------------------------------------------

# Fields that must carry the author's name; their values are public in that file
# already, so names skips them. A later issue that adds such a field extends this.
AUTHORSHIP_FIELDS = (
    ("LICENSE", None),                                   # the whole file
    (".claude-plugin/plugin.json", "author"),
    (".claude-plugin/marketplace.json", "owner"),
    (".claude-plugin/marketplace.json", "plugins[].author"),
    ("pyproject.toml", "project.authors"),
)
# What #16's name search removes before matching. Removals become one space and
# never join lines.
TRAILER_RE = re.compile(r"(?im)^[ \t]*(?:signed-off-by|co-authored-by):.*$")
GITHUB_URL_RE = re.compile(r"(?i)(?:https?:" + SEP * 2 + r")?(?:www\.)?github\.com" + SEP + r"[^\s<>()\[\]\"'`]*")
REPO_REF_RE = re.compile(r"(?<![\w.-])[A-Za-z0-9][A-Za-z0-9-]*" + SEP + r"[A-Za-z0-9._-]+#\d+")
ORIGIN_RE = re.compile(
    r"^(?:https?:" + SEP * 2 + r"(?:[^@" + SEP + r"]+@)?github\.com" + SEP
    + r"|ssh:" + SEP * 2 + r"git@github\.com" + SEP + r"|git@github\.com:)"
    r"(?P<o>[A-Za-z0-9-]+)" + SEP + r"(?P<r>[A-Za-z0-9._-]+?)(?:\.git)?" + SEP + "?$"
)


def read_denylist(path, source):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise SetupError(f"cannot read the denylist that {source} names ({e.strerror})")
    entries = [l.strip() for l in (decode_blob(data) or "").split("\n")]
    entries = [e for e in entries if e and not e.startswith("#")]
    if not entries:
        raise SetupError(f"the denylist that {source} names has no entries")
    return entries


def denylist_entries(repo, flag):
    if flag:
        return read_denylist(flag, "--denylist")
    result = repo.run("config", "--get", "gantry.denylist", check=False)
    path = _decode(result.stdout).rstrip("\n")
    if result.returncode != 0 or not path:
        raise SetupError("no denylist: pass --denylist or record git config gantry.denylist")
    return read_denylist(path, "git config gantry.denylist")


def match_re(entries):
    """Each entry, case-insensitively, not adjacent to a letter."""
    alternatives = "|".join(re.escape(e) for e in sorted(set(entries), key=len, reverse=True))
    return re.compile(r"(?i)(?<![^\W\d_])(?:" + alternatives + r")(?![^\W\d_])")


def identity_res(repo):
    """Patterns for this clone's own public identity, from origin; [] when not GitHub."""
    result = repo.run("remote", "get-url", "origin", check=False)
    m = ORIGIN_RE.match(_decode(result.stdout).strip()) if result.returncode == 0 else None
    if not m:
        print("names: no GitHub origin, so no identity was removed", file=sys.stderr)
        return []
    o, r = re.escape(m["o"]), re.escape(m["r"])
    return [
        re.compile(r"(?i)(?<![\w.-])" + o + r"\.github\.io" + SEP + r + r"(?![\w.-])"),
        re.compile(r"(?i)(?<![\w.-])" + o + SEP + r + r"(?:\.git)?(?![\w.-])"),
    ]


def _field_leaves(node, parts):
    """Every string leaf under the dotted field path (a `[]` part walks a list)."""
    if not parts:
        yield from (s for _, s in _walk_strings(node))
        return
    key, many = parts[0].removesuffix("[]"), parts[0].endswith("[]")
    value = node.get(key) if isinstance(node, dict) else None
    for item in (value if many and isinstance(value, list) else [value] if not many else []):
        yield from _field_leaves(item, parts[1:])


def authorship_values(path, text):
    """None to skip the whole file, else the authorship strings to blank in it."""
    fields = [f for p, f in AUTHORSHIP_FIELDS if p == path]
    if not fields:
        return []
    if None in fields:
        return None
    try:
        doc = tomllib.loads(text) if path.endswith(".toml") else json.loads(text)
    except ValueError:
        return []  # nothing to skip: fail closed
    values = set()
    for field in fields:
        values.update(v for v in _field_leaves(doc, field.split(".")) if v)
    return sorted(values | {json.dumps(v)[1:-1] for v in values}, key=len, reverse=True)


def names_text(text, identity, blank=()):
    """The text after the removals, one space per removal."""
    for value in blank:
        text = text.replace(value, " ")
    for pattern in (TRAILER_RE, GITHUB_URL_RE, REPO_REF_RE, *identity):
        text = pattern.sub(" ", text)
    return text


def names_lines(text, identity, matcher, blank=()):
    """The line number of each match, one entry per match."""
    hits = []
    for lineno, line in enumerate(names_text(text, identity, blank).split("\n"), 1):
        hits += [lineno] * len(matcher.findall(line))
    return hits


def cmd_names(repo, args):
    matcher = match_re(denylist_entries(repo, args.denylist))
    identity = identity_res(repo)
    mask = lambda s: matcher.sub("<match>", s)
    where = []
    if args.staged or args.all:
        files = staged_files(repo) if args.staged else tree_files(repo, "HEAD")
        for (_, _, path), data in zip(files, repo.read_objects([oid for _, oid, _ in files])):
            shown = mask(path)
            where += [f"names: {shown} (path)"] * len(matcher.findall(names_text(path, identity)))
            text = decode_blob(data) if data is not None else None
            blank = authorship_values(path, text) if text is not None else None
            if blank is not None:
                where += [f"names: {shown}:{i}" for i in names_lines(text, identity, matcher, blank)]
    else:
        text = read_text_file(args.message or args.text)
        label = "message" if args.message else "text"
        if args.message:
            text = clean_message(text, comment_string(repo))
        where += [f"names: {label}:{i}" for i in names_lines(text, identity, matcher)]
    if args.where:
        for line in where:
            print(line)
    n = len(where)
    print(f"names: {n} match" + ("" if n == 1 else "es"))
    return 1 if n else 0


COMMANDS = {
    "tree": cmd_tree, "staged": cmd_staged, "range": cmd_range, "message": cmd_message,
    "ci": cmd_ci, "names": cmd_names,
}


def build_parser():
    parser = argparse.ArgumentParser(prog="commit_check.py", description="Commit check against private data.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tree", help="every tracked file at HEAD, rule 4 and the exemption list")
    sub.add_parser("staged", help="what the commit adds or changes, read from the index (pre-commit)")
    p = sub.add_parser("range", help="each commit's own changes and message in base..head")
    p.add_argument("base")
    p.add_argument("head")
    p = sub.add_parser("message", help="rule 3 on a commit message file (commit-msg)")
    p.add_argument("file")
    sub.add_parser("ci", help="tree plus the range the GitHub event names")
    p = sub.add_parser("names", help="count denylist names and hostnames (never prints them)")
    p.add_argument("--denylist", help="the denylist file (default: git config gantry.denylist)")
    p.add_argument("--where", action="store_true", help="print each match's file and line")
    scope = p.add_mutually_exclusive_group(required=True)
    scope.add_argument("--staged", action="store_true", help="what the commit adds or changes")
    scope.add_argument("--all", action="store_true", help="every tracked file at HEAD")
    scope.add_argument("--message", metavar="FILE", help="a commit message file (cleaned)")
    scope.add_argument("--text", metavar="FILE", help="any text, such as a pull-request body")
    return parser


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = build_parser().parse_args(argv)  # a usage error exits 2
    try:
        return COMMANDS[args.command](Repo.find(), args)
    except SetupError as e:
        print(f"{'names' if args.command == 'names' else 'commit-check'}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
