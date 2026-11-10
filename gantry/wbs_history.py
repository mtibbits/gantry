"""The WBS.md history reader (#28): three answers for a site from its
planning repository, through one repository rule and one isolated git helper.

- `last_state_change`: the latest date a leaf line's glyph changed in a
  window, read from the first-parent history of the site's `WBS.md`, plus
  whether the working-tree `WBS.md` matches its committed version;
- `committed_text`: a committed file's text at `HEAD`;
- `history_repository`: the one rule for which repository answers.

When git cannot answer, the result is a `NoData` with a reason; nothing here
raises for a site. It is a line scanner, not a WBS parser, and never builds
on the stopgap parser (#12 deletes it). docs/wbs-history.md states the rules.
The signatures are provisional until #29 binds them.
"""

import os
import re
import stat
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import PurePath

from gantry.roots import OutsideRoots, gantry_root, is_within, program_on_path, resolve_confined, stat_confined

# Every "no data" reason, the one spelling (docs/wbs-history.md lists each).
GIT_NOT_FOUND = "git not found"
DIR_NOT_FOUND = "history directory not found"
NO_PLANNING_DIR = "the site has no planning directory"
OUTSIDE_ROOTS = "history directory is outside the configured roots"
CANNOT_CHECK = "cannot check the history directory: {reason}"
NO_REPOSITORY = "in no repository"
OWN_REPOSITORY = "history would come from Gantry's own repository"
NO_COMMITS = "`WBS.md` has no commits on or before {as_of}"
SHALLOW = "shallow history"
GIT_FAILED = "git failed: {message}"
REASONS = (GIT_NOT_FOUND, DIR_NOT_FOUND, NO_PLANNING_DIR, OUTSIDE_ROOTS, CANNOT_CHECK, NO_REPOSITORY,
           OWN_REPOSITORY, NO_COMMITS, SHALLOW, GIT_FAILED)

GIT_TIMEOUT = 120  # seconds, for one plumbing call


@dataclass(frozen=True)
class NoData:
    """git could not answer, and why."""

    reason: str

    @property
    def text(self) -> str:
        return "no data: " + self.reason


@dataclass(frozen=True)
class Repo:
    """The repository that answers for a site: its top level, the history
    directory relative to it (`/` form, "" at the top), the site's name, and
    the ceiling it was discovered under."""

    top: PurePath
    prefix: str
    site: str
    ceiling: PurePath


@dataclass(frozen=True)
class Absent:
    """The path is not in the commit."""

# devagent's node rule (scripts/lib/wbs-parser.py at 8d729b5; equal to
# 8a821d5's): these three regexes are its _BULLET_RE, _META_BLOCK_RE and
# _FENCE_RE, verbatim. META_RE is kept as the reference `split_meta` equals;
# the scan never runs it (it backtracks on a long whitespace run).
NODE_RE = re.compile(r"^(?P<indent>\s*)- \[(?P<glyph>[ x\-!~?P])\]\s+(?P<rest>.+)$")
META_RE = re.compile(r"\s*\{(?P<body>[^{}]*)\}\s*$")
FENCE_RE = re.compile(r"^[ \t]*(?P<run>`{3,}|~{3,})(?P<info>.*)$")


# --- the line scanner --------------------------------------------------------------


def decode_version(data: bytes) -> str:
    """A committed version's text: UTF-8 with replacement, so no byte fails."""
    return data.decode("utf-8", "replace")


def split_meta(rest: str):
    """`META_RE.search(rest)` as `(start, body)`, or None, in linear time."""
    stripped = rest.rstrip()
    if not stripped.endswith("}"):
        return None
    close = len(stripped) - 1
    brace = stripped.rfind("{", 0, close)
    if brace == -1 or "}" in stripped[brace + 1:close]:
        return None
    start = brace
    while start > 0 and stripped[start - 1].isspace():
        start -= 1
    return start, stripped[brace + 1:close]


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _parse_meta(body: str) -> dict:
    """devagent's `_parse_meta_walk`, its value only: a repeated key keeps the last."""
    out = {}
    i, n = 0, len(body)
    while i < n:
        while i < n and body[i] in " ,\t":
            i += 1
        if i >= n:
            break
        key_start = i
        while i < n and body[i] != ":":
            i += 1
        key = body[key_start:i].strip()
        if i >= n or not key:
            break
        i += 1
        while i < n and body[i] in " \t":
            i += 1
        if i < n and body[i] == "[":
            end = body.find("]", i)
            if end == -1:
                out[key] = body[i + 1:].strip()
                break
            out[key] = [_strip_quotes(x.strip()) for x in body[i + 1:end].split(",") if x.strip()]
            i = end + 1
        elif i < n and body[i] in "\"'":
            quote = body[i]
            i += 1
            val_start = i
            while i < n and body[i] != quote:
                i += 1
            out[key] = body[val_start:i]
            if i < n:
                i += 1
        else:
            val_start = i
            while i < n and body[i] != ",":
                i += 1
            out[key] = body[val_start:i].strip()
    return out


def _identity(rest: str) -> tuple[str, str]:
    """("issue", its value) when the meta block holds a non-empty string
    `issue`, else ("text", the line's text without its meta block)."""
    rest = rest.rstrip()
    found = split_meta(rest)
    if found is None:
        return "text", rest.strip()
    start, body = found
    value = _parse_meta(body).get("issue")
    if isinstance(value, str) and value:
        return "issue", value
    return "text", rest[:start].rstrip()


def _fence(line: str):
    """(fence character, run length, is a closer), or None when not an opener
    or closer. A backtick run whose info holds a backtick is neither."""
    m = FENCE_RE.match(line)
    if not m:
        return None
    run, info = m.group("run"), m.group("info")
    if run[0] == "`" and "`" in info:
        return None
    return run[0], len(run), not info.strip()


def _code_lines(lines: list[str]) -> list[bool]:
    """Which lines are inside a fence (opener and closer included), by
    devagent's pairing: an opener's closer is the first later fence line of
    the same character, a run at least as long, and blank info; an opener
    with no closer is plain text. Linear: the longest closer run after each
    line is precomputed per character, so an unclosed opener costs nothing."""
    fences = [_fence(line) for line in lines]
    n = len(lines)
    longest = {"`": [0] * (n + 1), "~": [0] * (n + 1)}
    for i in range(n - 1, -1, -1):
        for char in longest:
            longest[char][i] = longest[char][i + 1]
        f = fences[i]
        if f is not None and f[2]:
            longest[f[0]][i] = max(longest[f[0]][i], f[1])
    code = [False] * n
    i = 0
    while i < n:
        f = fences[i]
        if f is None or longest[f[0]][i + 1] < f[1]:
            i += 1
            continue
        j = i + 1
        while not (fences[j] is not None and fences[j][0] == f[0] and fences[j][1] >= f[1] and fences[j][2]):
            j += 1
        for k in range(i, j + 1):
            code[k] = True
        i = j + 1
    return code


def scan_nodes(text: str) -> dict:
    """Each leaf line's key to its glyph, in file order.

    A key is (kind, identity, occurrence): kind "issue" or "text" (see
    `_identity`), and the identity's occurrence number among the node lines
    in file order, from 1. A node line is a leaf when the next node line's
    depth is not greater than its own, or there is none.
    """
    lines = text.splitlines()
    code = _code_lines(lines)
    nodes = []
    for line, in_code in zip(lines, code):
        if in_code:
            continue
        m = NODE_RE.match(line)
        if m:
            depth = len(m.group("indent").expandtabs(2)) // 2 + 1
            nodes.append((depth, m.group("glyph"), _identity(m.group("rest"))))
    seen = {}
    out = {}
    for index, (depth, glyph, ident) in enumerate(nodes):
        seen[ident] = seen.get(ident, 0) + 1
        if index + 1 == len(nodes) or nodes[index + 1][0] <= depth:
            out[(*ident, seen[ident])] = glyph
    return out


def state_changes(old: dict, new: dict) -> set:
    """The keys that are leaves in both scans with different glyphs."""
    return {key for key, glyph in new.items() if key in old and old[key] != glyph}


# --- the isolated git helper ---------------------------------------------------------


class GitRefused(ValueError):
    """A git command line the helper does not run: only plumbing, and only
    the options this module needs."""


# Per subcommand, the options allowed before `--` (a name ending "=" takes a value).
_ALLOWED = {
    "rev-parse": ("--show-toplevel", "--show-prefix", "--verify", "--quiet", "--is-shallow-repository"),
    "rev-list": ("--first-parent", "--format="),
    "hash-object": ("--path=",),
    "cat-file": (),
}


def _check_args(args):
    if not args or args[0] not in _ALLOWED:
        raise GitRefused(f"git {args[0] if args else ''} is not run here")
    command, rest = args[0], list(args[1:])
    if command == "cat-file":
        # Only `cat-file blob <id>`: no --batch (it reports a corrupt blob as
        # missing), no --textconv or --filters.
        if len(rest) != 2 or rest[0] != "blob" or rest[1].startswith("-"):
            raise GitRefused("only `git cat-file blob <id>` is run here")
        return
    allowed = _ALLOWED[command]
    for arg in rest:
        if arg == "--":
            break
        if arg.startswith("-") and not any(arg == o or (o.endswith("=") and arg.startswith(o)) for o in allowed):
            raise GitRefused(f"git {command} {arg} is not run here")


def git_env(ceiling, environ=None) -> dict:
    """The environment of every git call: every inherited GIT_* variable
    removed (a hook's GIT_DIR cannot redirect a read), the ceiling set, no
    lazy fetch or prompt, and the C locale so git's messages are untranslated."""
    environ = os.environ if environ is None else environ
    env = {k: v for k, v in environ.items() if not k.upper().startswith("GIT_")}
    env["GIT_CEILING_DIRECTORIES"] = str(ceiling)
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    return env


def run_git(args, *, cwd, ceiling):
    """The module's only child process: a CompletedProcess (bytes), or NoData
    when git is not on the path or cannot be started."""
    _check_args(args)
    env = git_env(ceiling)
    if not program_on_path("git", env):
        return NoData(GIT_NOT_FOUND)
    try:
        return subprocess.run(["git", *args], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return NoData(GIT_FAILED.format(message="timed out"))
    except OSError as e:
        return NoData(GIT_FAILED.format(message=e.strerror or type(e).__name__))


# An absolute path in git's message: quoted (kept quoted), or bare, which
# runs to the end of the line (a path may hold a space).
_QUOTED_PATH_RE = re.compile(r"'(?:[A-Za-z]:[\\/]|[\\/])[^']*'")
_BARE_PATH_RE = re.compile(r"(?<![\w.:/\\-])(?:[A-Za-z]:[\\/]|[\\/]).*")


def clean(message: str, known, site: str) -> str:
    """`message` with every absolute path replaced by the site's name: each
    known path first (both slash spellings, the longest first, as
    stamp.public_text does), then any quoted or bare absolute path."""
    spellings = set()
    for path in known:
        if path:
            spellings |= {path, path.replace("\\", "/"), path.replace("/", "\\")}
    for path in sorted(spellings, key=len, reverse=True):
        message = message.replace(path, site)
    message = _QUOTED_PATH_RE.sub(lambda m: f"'{site}'", message)
    return _BARE_PATH_RE.sub(lambda m: site, message)


def _first_line(data: bytes) -> str:
    for line in data.decode("utf-8", "replace").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _failed(ran, known, site) -> NoData:
    line = _first_line(ran.stderr)
    message = clean(line, known, site) if line else f"exit {ran.returncode}"
    return NoData(GIT_FAILED.format(message=message))


def _out(ran) -> str:
    return ran.stdout.decode("utf-8", "surrogateescape")


# --- the repository rule -------------------------------------------------------------


def history_dir(site, config):
    """The site's `history` path when set, else its planning directory (may be None)."""
    override = config.sites.get(site.name)
    if override is not None and override.history is not None:
        return override.history
    return site.planning_dir


def history_repository(roots, path, gantry_root, *, key, site):
    """The one repository rule: the Repo git finds from the history directory
    `path`, or NoData. Discovery runs under GIT_CEILING_DIRECTORIES =
    `gantry_root`; a directory, or a top level, at `gantry_root` or above it
    is refused, the first before any git call."""
    if path is None:
        return NoData(DIR_NOT_FOUND)
    try:
        st = stat_confined(roots, path, key=key, site=site)
        if not stat.S_ISDIR(st.st_mode):
            return NoData(DIR_NOT_FOUND)
        resolved = resolve_confined(roots, path, key=key, site=site)
    except (FileNotFoundError, NotADirectoryError):
        return NoData(DIR_NOT_FOUND)
    except OutsideRoots:
        return NoData(OUTSIDE_ROOTS)
    except (OSError, ValueError) as e:
        # ValueError: an embedded NUL, or a path too long for Windows.
        return NoData(CANNOT_CHECK.format(reason=getattr(e, "strerror", None) or str(e)))
    if is_within(gantry_root, resolved):
        return NoData(OWN_REPOSITORY)
    ran = run_git(["rev-parse", "--show-toplevel", "--show-prefix"], cwd=resolved, ceiling=gantry_root)
    if isinstance(ran, NoData):
        return ran
    known = [str(path), str(resolved)]
    if ran.returncode != 0:
        if _first_line(ran.stderr).startswith("fatal: not a git repository"):
            return NoData(NO_REPOSITORY)
        return _failed(ran, known, site)
    lines = _out(ran).splitlines()
    top = PurePath(os.path.normpath(lines[0]))
    if is_within(gantry_root, top):
        return NoData(OWN_REPOSITORY)
    prefix = lines[1].rstrip("/") if len(lines) > 1 else ""
    return Repo(top, prefix, site, PurePath(gantry_root))


# --- the walk ------------------------------------------------------------------------


class HistoryCache:
    """One build's reads, shared by every site in a repository: `HEAD` and
    shallowness by top level, chains by (top, head, path), blob ids by
    (top, commit, path) and scans by (top, blob id). It lasts one build."""

    def __init__(self):
        self.heads, self.shallow, self.chains, self.blobs, self.scans = {}, {}, {}, {}, {}


@dataclass(frozen=True)
class HistoryResult:
    date: date | None
    history_start: bool
    commits_read: int
    uncommitted: bool | None
    uncommitted_reason: str | None
    repo: Repo | NoData
    no_data: NoData | None


def _top(repo) -> str:
    return os.path.normcase(str(repo.top))


def _git(repo, args):
    """run_git in the repository's top level, under the ceiling it was found under."""
    return run_git(args, cwd=repo.top, ceiling=repo.ceiling)


def _repo_path(repo, rel: str) -> str:
    """`rel` (relative to the history directory, `/` form) relative to the top level."""
    return f"{repo.prefix}/{rel}" if repo.prefix else rel


def _known(repo):
    return [str(repo.top)]


def _resolve(repo, rev: str):
    """`rev` resolved to an id; Absent when rev-parse says it does not exist
    (exit 1, nothing on stderr); NoData for any other failure."""
    ran = _git(repo, ["rev-parse", "--verify", "--quiet", rev])
    if isinstance(ran, NoData):
        return ran
    if ran.returncode == 1 and not ran.stderr.strip():
        return Absent()
    if ran.returncode != 0:
        return _failed(ran, _known(repo), repo.site)
    return _out(ran).strip()


def _head(repo, cache):
    """HEAD's commit id, once per top level per build: an id, Absent (unborn) or NoData."""
    key = _top(repo)
    if key not in cache.heads:
        cache.heads[key] = _resolve(repo, "HEAD^{commit}")
    return cache.heads[key]


def _blob_id(repo, commit: str, path: str, cache):
    """The blob id of `path` (relative to the top) at `commit`, once per build."""
    key = (_top(repo), commit, path)
    if key not in cache.blobs:
        cache.blobs[key] = _resolve(repo, f"{commit}:{path}")
    return cache.blobs[key]


def _blob(repo, blob_id: str):
    """The blob's bytes, or NoData."""
    ran = _git(repo, ["cat-file", "blob", blob_id])
    if isinstance(ran, NoData):
        return ran
    if ran.returncode != 0:
        return _failed(ran, _known(repo), repo.site)
    return ran.stdout


def _version(repo, commit, path: str, cache):
    """The scan of `path` at `commit`: a dict (empty when the commit is None
    or the file is not there), or NoData."""
    if commit is None:
        return {}
    blob_id = _blob_id(repo, commit, path, cache)
    if isinstance(blob_id, (Absent, NoData)):
        return {} if isinstance(blob_id, Absent) else blob_id
    key = (_top(repo), blob_id)
    if key not in cache.scans:
        data = _blob(repo, blob_id)
        if isinstance(data, NoData):
            return data
        cache.scans[key] = scan_nodes(decode_version(data))
    return cache.scans[key]


def _chain(repo, head: str, path: str, cache):
    """The first-parent chain of commits that touch `path`, newest first, as
    (commit, first parent or None, date in the commit's own offset); or NoData."""
    key = (_top(repo), head, path)
    if key not in cache.chains:
        ran = _git(repo, ["rev-list", "--first-parent", "--format=%H %P|%ci", head, "--", f":(top,literal){path}"])
        if isinstance(ran, NoData):
            return ran
        if ran.returncode != 0:
            return _failed(ran, _known(repo), repo.site)
        chain = []
        for line in _out(ran).splitlines():
            if not line.strip() or line.startswith("commit "):
                continue
            ids, _, when = line.partition("|")
            parts = ids.split()
            chain.append((parts[0], parts[1] if len(parts) > 1 else None, date.fromisoformat(when.strip()[:10])))
        cache.chains[key] = chain
    return cache.chains[key]


def _is_shallow(repo, cache):
    key = _top(repo)
    if key not in cache.shallow:
        ran = _git(repo, ["rev-parse", "--is-shallow-repository"])
        if isinstance(ran, NoData):
            return ran
        if ran.returncode != 0:
            return _failed(ran, _known(repo), repo.site)
        cache.shallow[key] = _out(ran).strip() == "true"
    return cache.shallow[key]


def _walk(repo, as_of, weeks, cache):
    """(date, history_start, commits read) for the repository's WBS.md, or
    (NoData, commits read)."""
    path = _repo_path(repo, "WBS.md")
    no_commits = NoData(NO_COMMITS.format(as_of=as_of.isoformat()))
    head = _head(repo, cache)
    if isinstance(head, (Absent, NoData)):
        return (no_commits if isinstance(head, Absent) else head), 0
    chain = _chain(repo, head, path, cache)
    if isinstance(chain, NoData):
        return chain, 0
    first = as_of - timedelta(days=7 * weeks - 1)
    stop_on = first - timedelta(days=1)
    read, latest, last, stopped = 0, None, None, False
    for entry in chain:
        commit, parent, day = entry
        if day > as_of:
            continue
        read += 1
        last = entry
        new = _version(repo, commit, path, cache)
        if isinstance(new, NoData):
            return new, read
        old = _version(repo, parent, path, cache)
        if isinstance(old, NoData):
            return old, read
        if state_changes(old, new) and (latest is None or day > latest):
            latest = day
        if day <= stop_on:
            stopped = True
            break
    # A parentless last commit in a shallow clone is a boundary, not the start.
    boundary = last if last is not None else (chain[-1] if chain else None)
    if boundary is not None and boundary[1] is None:
        shallow = _is_shallow(repo, cache)
        if isinstance(shallow, NoData):
            return shallow, read
        if shallow:
            return NoData(SHALLOW), read
    if read == 0:
        return no_commits, 0
    if not stopped and latest is None and last is chain[-1]:
        return (last[2], True, read), read
    return (latest, False, read), read


def _no_data_result(no_data, repo, read=0) -> HistoryResult:
    return HistoryResult(None, False, read, None, no_data.text, repo, no_data)


def last_state_change(roots, site, config, as_of, weeks, cache) -> HistoryResult:
    """The latest date a leaf line's glyph changed in the `weeks` ending at
    `as_of` (or in the last commit read, just before the window), for a site
    (a sites.SiteEntry); never raises. Provisional until #29 binds it."""
    name = site.name
    path = history_dir(site, config)
    if path is None:
        return _no_data_result(NoData(NO_PLANNING_DIR), NoData(NO_PLANNING_DIR))
    override = config.sites.get(name)
    key = f"site.{name}.history" if override is not None and override.history is not None else "devdoc_dir"
    repo = history_repository(roots, path, gantry_root(), key=key, site=name)
    if isinstance(repo, NoData):
        return _no_data_result(repo, repo)
    walked, read = _walk(repo, as_of, weeks, cache)
    if isinstance(walked, NoData):
        return _no_data_result(walked, repo, read)
    day, history_start, read = walked
    return HistoryResult(day, history_start, read, None, None, repo, None)
