"""Print the next free requirement ID for a layer of docs/requirements.md (#161).

    python3 tools/next_requirement_id.py [--offline] <code>

<code> is a layer a project file uses: BRule, UC, SR, FR, QA, EI or CON. The
tool prints `<code>-<n>` on stdout, where <n> is one more than the highest
<code> number mentioned in, in this order:

1. docs/requirements.md at origin/main, once `git ls-remote` (which writes
   nothing) shows that origin/main is origin's main; when they differ it
   refuses, saying "run git fetch", and --offline skips the comparison;
2. the working tree's docs/requirements.md, so a number this branch took counts;
3. the bodies of the repository's open issues;
4. the docs/requirements.md of its open pull requests, at their heads.

The last two count only when the author is the repository's owner, a member
or a collaborator (GitHub's author_association), and each listing is read
page by page until its count matches its total. stderr says what was read
and names the first source holding the highest number, the lowest issue or
pull request number first. --offline reads only the first two, and says so.

Every mention counts, bare or `gantry:`, not only definitions: a number above
main's highest can only be a claim. Not counted: another namespace (so
`Claim:FR-3`, and `{refines:SR-2}` written without its space), FR-01, the FR-4
inside NFR-4, any part of a ten-digit run, and what the tool cannot see:
drafts, comments, and a number chosen but not yet claimed.

It is maintainer tooling, the first tools/ script to call gh, and never one
of gantry's commands. It runs only `git rev-parse`, `git ls-remote`, `git
cat-file`, read-only `gh api graphql` queries and `gh api` GETs, and writes
nothing.

Exit 0 printed an ID; 1 refused: stdout is empty, and stderr names the source
it could not read or the check that failed, and the remedy; 2 a usage error.
Exit 1 covers retryable refusals (a stale origin/main, a listing that changed
while it was read) and permanent ones (an unknown author association, a pull
request with no docs/requirements.md, an ID past nine digits) alike, told
apart on stderr only; the contract is provisional for machine consumers.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]

from gantry.config import ID_TOKEN_RE  # noqa: E402
from gantry.requirements import ID_RE, LAYERS, MAX_DIGITS, PROJECT_LAYERS  # noqa: E402

PROG = "next_requirement_id"
NAMESPACE = "gantry"
FILE = "docs/requirements.md"
CODES = tuple(c for c in LAYERS if c in PROJECT_LAYERS)  # the layers' order, never the frozenset's
COUNTED = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
ASSOCIATIONS = COUNTED | {"CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "MANNEQUIN", "NONE"}

# The grammar is ID_RE's: its optional namespace, then the code and number (CORE).
_NAMESPACE_PART = f"(?:(?P<namespace>{ID_TOKEN_RE.pattern}):)?"
CORE = ID_RE.pattern.removeprefix(_NAMESPACE_PART)
if CORE == ID_RE.pattern:
    raise ImportError(f"{PROG}: gantry.requirements.ID_RE no longer starts with its namespace part")
# A mention has no letter, digit or ':' before it and no letter or digit after it
# (NFR-4, harbor:|FR-4, FR-12a and any part of a ten-digit run are not mentions;
# _FR-4_, x-FR-4 and e.g.FR-4 are). A namespace is tried only where a token cannot
# extend to the left, skipping leading _ . -, so the token runs scanned are disjoint
# and the scan stays linear; a namespace other than gantry is then not counted.
SCAN = re.compile(
    rf"(?:(?<![A-Za-z0-9_.:-])[_.-]*(?P<namespace>{ID_TOKEN_RE.pattern}):|(?<![A-Za-z0-9:])){CORE}(?![A-Za-z0-9])"
)

ORDER = {"main": 0, "tree": 1, "issue": 2, "pull": 3}
SHA_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")  # fullmatch
# Single-line queries, so no newline goes into a Windows argv; read-only by construction.
ISSUES_QUERY = (
    "query($owner: String!, $name: String!, $after: String) { repository(owner: $owner, name: $name) "
    "{ nameWithOwner issues(states: OPEN, first: 100, after: $after) "
    "{ totalCount pageInfo { hasNextPage endCursor } nodes { number authorAssociation body } } } }"
)
PULLS_QUERY = (
    "query($owner: String!, $name: String!, $after: String) { repository(owner: $owner, name: $name) "
    "{ nameWithOwner pullRequests(states: OPEN, first: 100, after: $after) "
    "{ totalCount pageInfo { hasNextPage endCursor } nodes { number authorAssociation headRefOid } } } }"
)
LISTINGS = {"issues": ("open issues", "body"), "pullRequests": ("open pull requests", "headRefOid")}
CONTENTS = "repos/{owner}/{repo}/contents/" + FILE + "?ref="  # gh fills {owner}/{repo} from the checkout
OFFLINE_REMEDY = "or run --offline (which reads no open issues or pull requests)"
EPILOG = """exit status:
  Exit 0 printed an ID; 1 refused (stdout is empty; stderr names the source
  and the remedy); 2 a usage error. 1 covers retryable and permanent refusals
  alike, and is provisional for machine consumers."""


class TooLong(Exception):
    """The ID would have more than MAX_DIGITS digits."""


class Refused(Exception):
    """A refusal; its args are the stderr lines, without the PROG prefix."""


class Result(NamedTuple):
    returncode: int
    stdout: bytes
    stderr: bytes


def run(argv, cwd) -> Result:
    """The default runner. A missing program raises FileNotFoundError."""
    done = subprocess.run(argv, cwd=cwd, capture_output=True, stdin=subprocess.DEVNULL)
    return Result(done.returncode, done.stdout, done.stderr)


class Source(NamedTuple):
    kind: str  # main, tree, issue or pull
    number: int | None  # the issue or pull request number
    association: str | None  # GitHub's author_association, for an issue or pull request
    text: str

    @property
    def label(self) -> str:
        if self.kind == "main":
            return "origin/main"
        if self.kind == "tree":
            return "the working tree"
        return f"open {'issue' if self.kind == 'issue' else 'pull request'} #{self.number}"


def mentions(text: str, code: str) -> list[int]:
    """The numbers of every bare or gantry: mention of `code` in `text`, in order."""
    # The grammar caps the number at MAX_DIGITS digits before int().
    return [int(m["number"]) for m in SCAN.finditer(text)
            if m["code"] == code and m["namespace"] in (None, NAMESPACE)]


def counted(source: Source) -> bool:
    return source.kind in ("main", "tree") or source.association in COUNTED


def choose(code: str, sources) -> tuple[int, Source | None]:
    """The highest `code` number and the first source holding it, or (0, None).

    Issues and pull requests whose author is not the owner, a member or a
    collaborator are dropped before any text is scanned. The order is
    origin/main, the working tree, issues ascending, then pull requests ascending."""
    best, where = 0, None
    for source in sorted(filter(counted, sources), key=lambda s: (ORDER[s.kind], s.number or 0)):
        top = max(mentions(source.text, code), default=0)
        if top > best:
            best, where = top, source
    return best, where


def format_id(code: str, n: int) -> str:
    if len(str(n)) > MAX_DIGITS:
        raise TooLong(code, n)
    return f"{code}-{n}"


# --- the sources -------------------------------------------------------------------


def unreadable(where, reason) -> Refused:
    return Refused(f"{where}: could not read: {reason}")


def exited(argv, result) -> str:
    """`<prog> exited <rc>`, then its first non-empty stderr line, if any."""
    lines = (line.strip() for line in result.stderr.decode("utf-8", "replace").splitlines())
    first = next((line for line in lines if line), "")
    return f"{argv[0]} exited {result.returncode}" + (f": {first}" if first else "")


def call(runner, argv, cwd, where) -> Result:
    try:
        return runner(argv, cwd)
    except FileNotFoundError:
        raise unreadable(where, f"{argv[0]} is not installed") from None
    except OSError as e:
        raise unreadable(where, e.strerror or type(e).__name__) from None


def checked(runner, argv, cwd, where) -> bytes:
    """The stdout of a run that must exit 0."""
    result = call(runner, argv, cwd, where)
    if result.returncode != 0:
        raise unreadable(where, exited(argv, result))
    return result.stdout


def decoded(data, where) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise unreadable(where, "not UTF-8") from None


def toplevel(runner) -> Path:
    out = checked(runner, ["git", "rev-parse", "--show-toplevel"], os.getcwd(), "the checkout")
    return Path(os.fsdecode(out.rstrip(b"\r\n")))


def origin_main(runner, root) -> str:
    """origin/main's full SHA; refuses when it is missing."""
    argv = ["git", "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main^{commit}"]
    result = call(runner, argv, root, "origin/main")
    out = result.stdout.decode("ascii", "replace").strip()
    if result.returncode == 1 and not out:
        raise Refused("origin/main: missing; run git fetch")
    if result.returncode != 0:
        raise unreadable("origin/main", exited(argv, result))
    if not SHA_RE.fullmatch(out):
        raise unreadable("origin/main", "unexpected output")
    return out


def compare_with_remote(runner, root, sha):
    """Refuses unless origin's main is at `sha`; `git ls-remote` writes nothing."""
    out = checked(runner, ["git", "ls-remote", "origin", "refs/heads/main"], root, "origin/main")
    remote = None
    for line in decoded(out, "origin/main").splitlines():
        fields = line.split("\t")
        if len(fields) == 2 and fields[1] == "refs/heads/main":
            remote = fields[0]
    if remote is None:
        raise unreadable("origin/main", "origin has no branch main")
    if remote != sha:
        raise Refused(f"origin/main: at {sha[:12]}, but origin's main is at {remote[:12]}; run git fetch")


def main_text(runner, root, sha) -> str:
    return decoded(checked(runner, ["git", "cat-file", "blob", f"{sha}:{FILE}"], root, "origin/main"),
                   "origin/main")


def tree_text(root) -> str:
    where = f"the working tree's {FILE}"
    try:
        data = (root / FILE).read_bytes()
    except OSError as e:
        raise unreadable(where, e.strerror or type(e).__name__) from None
    return decoded(data, where)


def listing_page(data, field, where):
    """(listing, nameWithOwner) from one page; refuses on any other shape."""
    text_key = LISTINGS[field][1]
    try:
        repository = json.loads(data.decode("utf-8"))["data"]["repository"]
        name, listing = repository["nameWithOwner"], repository[field]
        total, info, nodes = listing["totalCount"], listing["pageInfo"], listing["nodes"]
        more, cursor = info["hasNextPage"], info["endCursor"]
        good = (type(name) is str and type(total) is int and total >= 0 and type(more) is bool
                and (cursor is None or type(cursor) is str) and (not more or bool(cursor))
                and type(nodes) is list
                and all(type(node) is dict and type(node.get("number")) is int
                        and type(node.get("authorAssociation")) is str and type(node.get(text_key)) is str
                        for node in nodes))
    except (ValueError, RecursionError, TypeError, KeyError):
        good = False
    if not good:
        raise unreadable(where, "unexpected response")
    for node in nodes:
        if node["authorAssociation"] not in ASSOCIATIONS:  # refuse, never drop a claim unseen
            raise unreadable(where, f"unknown author association {node['authorAssociation']} on "
                                    f"#{node['number']}; this tool needs updating for it, {OFFLINE_REMEDY}")
    return listing, name


def graphql_pages(runner, root, query, field):
    """(every node, nameWithOwner) of one listing, read until the count matches the total."""
    where = LISTINGS[field][0]
    nodes, cursors, pages, cursor = [], set(), 0, None
    while True:
        argv = ["gh", "api", "graphql", "-F", "owner={owner}", "-F", "name={repo}", "-f", "query=" + query]
        if cursor is not None:
            argv += ["-f", "after=" + cursor]
        listing, name = listing_page(checked(runner, argv, root, where), field, where)
        pages += 1
        nodes += listing["nodes"]
        total = listing["totalCount"]
        if not listing["pageInfo"]["hasNextPage"]:
            break
        cursor = listing["pageInfo"]["endCursor"]
        if cursor in cursors:
            raise unreadable(where, "the same page came back twice; run again")
        cursors.add(cursor)
        if pages >= total // 100 + 2:
            raise unreadable(where, f"more pages than {total} items need; run again")
    numbers = {node["number"] for node in nodes}
    if len(nodes) != total or len(numbers) != len(nodes):
        raise unreadable(where, f"read {len(numbers)} of {total}; run again")
    return nodes, name


def pull_text(runner, root, node) -> str:
    """The pull request's docs/requirements.md at its head commit."""
    where, oid = f"open pull request #{node['number']}", node["headRefOid"]
    if not SHA_RE.fullmatch(oid):  # checked before it goes into an argv
        raise unreadable(where, "unexpected head commit")
    argv = ["gh", "api", "-H", "Accept: application/vnd.github.raw+json", CONTENTS + oid]
    result = call(runner, argv, root, where)
    if result.returncode != 0:
        lines = [f"{where}: could not read: {exited(argv, result)}"]
        if b"HTTP 404" in result.stderr:
            lines.append(f"{where} has no {FILE} at its head {oid}; ask its author to rebase, or close it, "
                         + OFFLINE_REMEDY)
        raise Refused(*lines)
    return decoded(result.stdout, where)


def read_github(runner, root):
    """(sources, summary) for the open issues and pull requests.

    Only counted pull requests' files are read, ascending; choose() drops the
    other issues before it scans any text."""
    issues, name = graphql_pages(runner, root, ISSUES_QUERY, "issues")
    pulls, _ = graphql_pages(runner, root, PULLS_QUERY, "pullRequests")
    sources = [Source("issue", node["number"], node["authorAssociation"], node["body"]) for node in issues]
    mine = sorted((node for node in pulls if node["authorAssociation"] in COUNTED), key=lambda node: node["number"])
    sources += [Source("pull", node["number"], node["authorAssociation"], pull_text(runner, root, node))
                for node in mine]
    counted_issues = sum(node["authorAssociation"] in COUNTED for node in issues)
    return sources, (f"{name}'s {len(issues)} open issues and {len(pulls)} open pull requests "
                     f"({counted_issues} and {len(mine)} by the owner, a member or a collaborator)")


# --- the command -------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__.split("\n\n")[0], epilog=EPILOG,
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     allow_abbrev=False)
    parser.add_argument("--offline", action="store_true",
                        help="read only origin/main, without comparing it with the remote, and the working "
                             "tree; open issues and pull requests are not read")
    parser.add_argument("code", help="the layer: " + ", ".join(CODES))
    return parser


def parse_args(argv):
    """The parsed arguments; a usage error exits 2."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.code not in CODES:  # our own text: argparse's choices message differs between versions
        parser.error(f"{args.code}: not a code a project file uses; use one of {', '.join(CODES)}")
    return args


def say(lines, stream):
    for line in lines:
        print(f"{PROG}: {line}", file=stream)


def next_id(args, runner, root):
    """(stderr lines, the ID); raises Refused."""
    if root is None:
        root = toplevel(runner)
    root = Path(root)
    sha = origin_main(runner, root)
    if not args.offline:
        compare_with_remote(runner, root, sha)
    sources = [Source("main", None, None, main_text(runner, root, sha)),
               Source("tree", None, None, tree_text(root))]
    if args.offline:
        lines = [f"--offline: read origin/main at {sha[:12]}, not compared with the remote, and the working "
                 "tree; open issues and pull requests were not read"]
    else:
        github, summary = read_github(runner, root)
        sources += github
        lines = [f"read origin/main at {sha[:12]}, the working tree, and {summary}"]
    code = args.code
    n, where = choose(code, sources)
    try:
        new = format_id(code, n + 1)
    except TooLong:
        raise Refused(f"{code}-{n + 1} would have more than {MAX_DIGITS} digits, so it is not an ID; "
                      f"the highest {code} number, {n}, is in {where.label}") from None
    if where is None:
        lines.append(f"{new}: no {code} number was found")
    else:
        lines.append(f"{new}: the highest {code} number, {n}, is in {where.label}")
    return lines, new


def main(argv=None, runner=None, root=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")
    args = parse_args(argv)
    try:
        lines, new = next_id(args, run if runner is None else runner, root)
    except Refused as e:
        say(e.args, sys.stderr)
        return 1
    say(lines, sys.stderr)
    sys.stderr.flush()
    print(new)  # the last write
    return 0


if __name__ == "__main__":
    sys.exit(main())
