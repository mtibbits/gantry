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

import re

from gantry.roots import gantry_root

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
