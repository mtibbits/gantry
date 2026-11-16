"""Print the next free requirement ID for a layer of docs/requirements.md (#161).

    python3 tools/next_requirement_id.py [--offline] <code>
"""

import re
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


class TooLong(Exception):
    """The ID would have more than MAX_DIGITS digits."""


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
