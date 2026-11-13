"""Requirements files: the eight layers, the ID grammar and the reader (#15).

A requirement ID is `<namespace>:<code>-<n>` (`gantry:FR-12`), a strict subset
of the `<namespace>:<id>` shape mtibbits/devagent#888 checks in `satisfies:`.
A requirements file is Markdown; a requirement is an unordered list item whose
text starts with a bare ID and a colon (`- FR-12: <text>`). Lines in fences and
block HTML comments are hidden. Every other line is prose, except an ID-shaped
item that is not a requirement, or an HTML comment the reader does not hide,
which is reported.
The module has its own meta-block reader and fence pairing and shares no code
with gantry/wbs_stopgap.py or gantry/model.py, so #12 can delete the stopgap
freely. docs/requirements-format.md states the rules.
"""

import re
from dataclasses import dataclass
from itertools import chain
from typing import NamedTuple

# DEFAULT_BR_NAMESPACE is re-exported: its one home is gantry.config.
from gantry.config import DEFAULT_BR_NAMESPACE, ID_TOKEN_RE, error_reason
from gantry.roots import OutsideRoots, read_bytes_confined
from gantry.sites import SiteWarning, shown

# After Wiegers & Beatty: business requirement, business rule, use case,
# system requirement, functional requirement, quality attribute, external
# interface, constraint.
LAYERS = ("BR", "BRule", "UC", "SR", "FR", "QA", "EI", "CON")
PROJECT_LAYERS = frozenset(LAYERS) - {"BR"}  # how project files are read
MAX_DIGITS = 9  # operator decision Q1; as est_rules.NUM
_CODES = "|".join(sorted(LAYERS, key=len, reverse=True))
ID_RE = re.compile(
    rf"(?:(?P<namespace>{ID_TOKEN_RE.pattern}):)?"
    rf"(?P<code>{_CODES})-(?P<number>[1-9][0-9]{{0,{MAX_DIGITS - 1}}})"
)  # always fullmatch; case-sensitive

REPORT_KINDS = ("unreadable", "malformed_item", "malformed_meta", "malformed_link", "malformed_comment",
                "misplaced", "duplicate_id", "undefined_link", "refines_cycle")
_KIND_ORDER = {kind: i for i, kind in enumerate(REPORT_KINDS)}

# The line grammar. Every pattern is anchored, so each is linear on a long line.
ITEM_RE = re.compile(r"(?P<indent>[ \t]*)(?P<marker>[-*+]|[0-9]{1,9}[.)])(?:[ \t]+(?P<rest>.*)|)")  # fullmatch
CANDIDATE_RE = re.compile(  # .match on an item's rest
    r"(?P<open>[*_`~]*)"
    rf"(?:(?P<namespace>{ID_TOKEN_RE.pattern}):)?"
    r"(?P<code>[A-Za-z]+)(?P<dash>-?)(?P<digits>[0-9]+)(?P<suffix>[A-Za-z]*)"
    r"(?P<close>[*_`~ \t]*):"
)
BREAK_RE = re.compile(r"[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*")  # fullmatch: a thematic break, not a list item
HEADING_START_RE = re.compile(r"[ \t]*#{1,6}(?:[ \t]|$)")  # match, at most 3 columns in: an ATX heading
HTML_START_RE = re.compile(r"[ \t]*</?[A-Za-z]")  # match, at most 3 columns in: an HTML block may start
FENCE_RE = re.compile(r"[ \t]*(?P<run>`{3,}|~{3,})(?P<info>.*)")  # fullmatch
BLOCK_RE = re.compile(r"\{[ \t]*[A-Za-z_][A-Za-z0-9_]*[ \t]*:[^{}]*\}")  # finditer
_OPENER_RE = re.compile(r"\{[ \t]*[A-Za-z_][A-Za-z0-9_]*[ \t]*:")  # a block's start, closed or not
_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BOM = "\N{BYTE ORDER MARK}"

# Comment messages: never "the rest of the file", since a portfolio BR section is a slice.
UNCLOSED = "an HTML comment with no closing '-->' hides every line after it"
LEAKED = "an HTML comment whose list item ends before its '-->' hides every line after it"
INLINE = "an HTML comment inside the text; it stays in the text and hides nothing: put it on its own line"
BEFORE_ID = "an HTML comment before the ID; the item is not read: put the comment on its own line"
START = ("an HTML comment that starts a list item and does not close on its line; "
         "it hides nothing: put the comment on its own line")

_WHY = {
    "ordered": "an ordered list item (a requirement uses -, * or +)",
    "marks": "marks around the ID (write it plain)",
    "namespace": "a namespace (a requirement is defined with a bare ID)",
    "space": "a space before the colon",
    "code": "{code!r} is not a layer code (" + ", ".join(LAYERS) + ")",
    "dash": "no '-' between the code and the number",
    "number": "the number {digits!r} is not a positive integer without a leading zero",
    "long": "the number has more than 9 digits",
    "suffix": "letters after the number",
    "nested": "requirements are not nested",
}


class ReqId(NamedTuple):
    namespace: str
    code: str
    number: int

    def __str__(self):
        return f"{self.namespace}:{self.code}-{self.number}"


@dataclass(frozen=True)
class ReportEntry:  # requirements' own; never gantry.model's
    source: str
    line: int | None  # None: the whole file
    kind: str
    message: str


@dataclass(frozen=True)
class Requirement:
    id: ReqId
    layer: str
    text: str
    refines: ReqId | None
    needs: list[ReqId]
    source: str
    line: int


@dataclass(frozen=True)
class RequirementSet:
    namespace: str
    requirements: dict[str, Requirement]  # keyed str(id), in file order
    report: list[ReportEntry]  # sorted: line None first, then line, then REPORT_KINDS order


def parse_id(text: str, default_namespace: str | None = None) -> ReqId | None:
    """The ReqId `text` names, or None. A bare ID takes `default_namespace`."""
    m = ID_RE.fullmatch(text)
    if m is None:
        return None
    namespace = m["namespace"]
    if namespace is None:
        if default_namespace is None or not ID_TOKEN_RE.fullmatch(default_namespace):
            return None
        namespace = default_namespace
    # At most MAX_DIGITS digits: int() never meets a long run.
    return ReqId(namespace, m["code"], int(m["number"]))


# --- the reader ----------------------------------------------------------------


def _check_args(namespace, layers, br_namespace):
    """Raise ValueError on a caller's mistake; the text never raises."""
    for name, value in (("namespace", namespace), ("br_namespace", br_namespace)):
        if not isinstance(value, str) or not ID_TOKEN_RE.fullmatch(value):
            raise ValueError(f"{name} {value!r} does not match {ID_TOKEN_RE.pattern}")
    unknown = set(layers) - set(LAYERS)
    if unknown:
        raise ValueError(f"layers holds codes that are not layers: {sorted(unknown)}")


def _width(line):
    """The width of a line's leading spaces and tabs, a tab counting as four."""
    return len(line[: len(line) - len(line.lstrip(" \t"))].expandtabs(4))


def _closer(lines, i, no_closer):
    """The index of the line that closes a fence opened on line i, or None."""
    m = FENCE_RE.fullmatch(lines[i])
    if m is None:
        return None
    run = m["run"]
    char = run[0]
    if char == "`" and "`" in m["info"]:
        return None
    if len(run) >= no_closer.get(char, float("inf")):
        return None  # a shorter run of this character already found no closer
    for j in range(i + 1, len(lines)):
        c = FENCE_RE.fullmatch(lines[j])
        if c and c["run"][0] == char and len(c["run"]) >= len(run) and c["info"].strip() == "":
            return j
    no_closer[char] = len(run)
    return None


def fence_spans(lines) -> list[tuple[int, int]]:
    """The (opener, closer) index pairs of the fenced blocks, 0-based, in order,
    pairing fences only. An opener with no closer is plain text, so it never
    hides the rest of the file. The reader and the portfolio file's reader pair
    fences together with HTML comments, in document order (_scan, hidden_lines);
    this fence-only pairing is kept for callers that show a file's blocks."""
    spans = []
    no_closer = {}
    i = 0
    while i < len(lines):
        j = _closer(lines, i, no_closer)
        if j is None:
            i += 1
        else:
            spans.append((i, j))
            i = j + 1
    return spans


def _content_column(line, m):
    """The column an item's text starts at, a tab counting as four. For an
    empty item, or text five or more columns past the marker (code on the
    page), it is one past the marker."""
    after = len(line[: m.end("marker")].expandtabs(4)) + 1
    rest = m["rest"]
    if rest is None or not rest.strip() or _text_is_code(line, m):
        return after
    return len(line[: m.start("rest")].expandtabs(4))


def _text_is_code(line, m):
    """Whether an item's text starts five or more columns past its marker,
    which a page shows as indented code."""
    after = len(line[: m.end("marker")].expandtabs(4)) + 1
    return m["rest"] is not None and len(line[: m.start("rest")].expandtabs(4)) - after >= 4


class _Layout(NamedTuple):
    hidden: list  # bool per line: fenced or commented
    skip: list  # bool per line: a comment line a requirement's text joins across
    nested: list  # bool per line: an item inside an enclosing list item
    comment: tuple | None  # (index, message): the comment that hides every line after it
    fences: list  # (opener, closer) index pairs, paired in document order
    listed: list  # bool per line: an item a page shows as a list item, not text or code
    raw: list  # bool per line: after an unclosed fence opener in an open item (code on a page)


def _scan(lines):
    """The layout of `lines`, read once from the top so that fences and
    comments pair in document order. docs/requirements-format.md states the
    rules."""
    n = len(lines)
    hidden, skip, nested, listed, raw, fences = [False] * n, [False] * n, [False] * n, [False] * n, [False] * n, []
    # The content column of the outermost open list item. A block comment (at
    # most 3 columns in) never sits in a nested item (content column at least
    # 4), so this one integer bounds it.
    open_col = None
    empty = False  # the outermost open item is empty, and no line has followed it yet
    # Only an item on a line that surely starts a list encloses: not one that continues paragraph
    # text outside every enclosing item, nor one inside an HTML block (a `<tag` line up to a blank
    # line). Such a line still bounds its text and its comments' skipped lines, but no comment
    # leaks from it: on a page there is no list item to end.
    para = html = encloses = False
    unclosed = False  # an unclosed fence opener in the open item: a page shows code to the item's end
    no_closer = {}
    fence_end = -1  # the last line of the open fence
    opener = bound = leak = None  # the open comment's first line, the column a line must reach, and
    #                               that column again when an enclosing item bounds it (it may leak)
    for i, line in enumerate(lines):
        if opener is not None:  # inside a comment
            if line.strip() and bound is not None and _width(line) < bound:
                if leak is not None:
                    hidden[i:] = [True] * (n - i)
                    return _Layout(hidden, skip, nested, (opener, LEAKED), fences, listed, raw)
                bound = open_col = None  # a line that encloses nothing ends here; its comment runs on
            hidden[i] = True
            skip[i] = bound is not None
            if "-->" in line:
                opener = None
            continue
        if not line.strip():
            hidden[i] = i <= fence_end
            raw[i] = unclosed
            para = html = False
            if empty:  # an item starts with at most one blank line: an empty one ends here
                open_col, empty = None, False
            continue
        if open_col is not None and _width(line) < open_col:
            open_col = None
        if open_col is None:
            unclosed = False
        raw[i] = unclosed
        empty = False
        if i <= fence_end:
            hidden[i] = True
            para = False
            continue
        j = _closer(lines, i, no_closer)
        if j is not None:
            fences.append((i, j))
            fence_end = j
            hidden[i] = True
            para = False
            continue
        f = FENCE_RE.fullmatch(line)
        if f is not None and open_col is not None and not (f["run"][0] == "`" and "`" in f["info"]):
            unclosed = True  # no closer: a page shows code to the item's end; the reader reads it as text
            continue
        body = line.lstrip(" \t")
        # Inside an HTML block, or after an unclosed fence in the item, a `<!--` line is content.
        if body.startswith("<!--") and _width(line) <= 3 and not (html or unclosed):
            hidden[i] = True
            para = False
            skip[i] = open_col is not None
            if _comment_end(body) == -1:
                opener, bound = i, open_col
                leak = open_col if encloses else None
            continue
        m = ITEM_RE.fullmatch(line)
        if m is None or (_width(line) <= 3 and BREAK_RE.fullmatch(line)):
            if _width(line) <= 3 and HTML_START_RE.match(line):
                html = True
            # Text outside every enclosing item is a paragraph a list-shaped line may only continue.
            para = (m is None and (open_col is None or not encloses)
                    and not (_width(line) <= 3 and (HEADING_START_RE.match(line) or BREAK_RE.fullmatch(line))))
            continue
        nested[i] = open_col is not None and encloses
        if open_col is None:
            open_col = _content_column(line, m)
            encloses = not (para or html)
            empty = m["rest"] is None or not m["rest"].strip()
        listed[i] = encloses and not unclosed
    return _Layout(hidden, skip, nested, None if opener is None else (opener, UNCLOSED), fences, listed, raw)


def hidden_lines(lines):
    """(hidden, fences, comment): which lines a fence or a block HTML comment
    hides; the fence (opener, closer) index pairs; and the (index, message) of a
    comment that hides every line after it, or None. All are read in document
    order as the reader reads them. The portfolio file's reader finds its
    sections with it."""
    layout = _scan(lines)
    return layout.hidden, layout.fences, layout.comment


def _opens_comment(rest):
    """Whether an item's text starts with a `<!--` that does not close on its line."""
    return rest is not None and rest.startswith("<!--") and _comment_end(rest) == -1


def _comment_end(text):
    """Where the `-->` closing the `<!--` that starts `text` begins, or -1.
    The search starts inside the opener, so `<!-->` closes."""
    return text.find("-->", 2)


def _comment_outside_code(text):
    """Whether a `<!--` in `text` lies outside every backtick code span. A span
    runs from a run of n backticks to the next run of exactly n; a run with no
    partner is literal. Linear: each run's partner comes from one right-to-left
    pass."""
    if "<!--" not in text:
        return False
    runs = [(m.start(), m.end()) for m in re.finditer(r"`+", text)]
    partner, last = [None] * len(runs), {}
    for k in range(len(runs) - 1, -1, -1):
        width = runs[k][1] - runs[k][0]
        partner[k] = last.get(width)
        last[width] = k
    pos = k = 0
    while k < len(runs):
        if partner[k] is None:
            k += 1
            continue
        if "<!--" in text[pos : runs[k][0]]:
            return True
        pos = runs[partner[k]][1]
        k = partner[k] + 1
    return "<!--" in text[pos:]


def _reasons(marker, c, nested):
    """Why an ID-shaped item is not a requirement, in the documented order."""
    why = []
    if marker not in ("-", "*", "+"):
        why.append(_WHY["ordered"])
    if any(ch in "*_`~" for ch in c["open"] + c["close"]):
        why.append(_WHY["marks"])
    if c["namespace"] is not None:
        why.append(_WHY["namespace"])
    if " " in c["close"] or "\t" in c["close"]:
        why.append(_WHY["space"])
    if c["code"] not in LAYERS:
        why.append(_WHY["code"].format(code=c["code"]))
    if not c["dash"]:
        why.append(_WHY["dash"])
    if c["digits"].startswith("0"):
        why.append(_WHY["number"].format(digits=c["digits"]))
    if len(c["digits"]) > MAX_DIGITS:
        why.append(_WHY["long"])
    if c["suffix"]:
        why.append(_WHY["suffix"])
    if nested:
        why.append(_WHY["nested"])
    return why


def _ending_block(text):
    """The meta block that ends `text`, or None."""
    last = None
    for m in BLOCK_RE.finditer(text):
        last = m
    return last if last is not None and last.end() == len(text) else None


def _split(body, sep):
    """`body` split at `sep` outside [...] and quotes; None if a [ or quote is unclosed."""
    parts, current, depth, quote = [], [], 0, None
    for ch in body:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth < 0:
                return None
        elif ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if quote or depth:
        return None
    parts.append("".join(current))
    return parts


def _unquote(item):
    item = item.strip()
    if len(item) >= 2 and item[0] == item[-1] and item[0] in "'\"":
        return item[1:-1]
    return item


def _block_entries(block):
    """([(key, value)], None) from a meta block, a value being a str or a list
    of str; or (None, why) when the block cannot be read."""
    parts = _split(block[1:-1], ",")
    if parts is None:
        return None, "an unclosed [ or quote"
    entries = []
    for entry in parts:
        key, colon, value = entry.partition(":")
        key = key.strip()
        if not colon or not _KEY_RE.fullmatch(key):
            return None, "an entry with no key"
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = [_unquote(item) for item in _split(value[1:-1], ",") or []]
            entries.append((key, [item for item in items if item]))
        else:
            entries.append((key, _unquote(value)))
    return entries, None


class _Reader:
    def __init__(self, namespace, source, first_line, layers, br_namespace):
        self.namespace = namespace
        self.source = source
        self.first_line = first_line
        self.layers = layers
        self.br_namespace = br_namespace
        self.requirements = {}
        self.report = []

    def add(self, line, kind, message):
        self.report.append(ReportEntry(self.source, line, kind, message))

    def candidate(self, line, m, nested):
        """Report an ID-shaped item that is not a requirement; return the
        candidate match when it is one."""
        rest = m["rest"]
        c = CANDIDATE_RE.match(rest) if rest is not None else None
        if c is None:
            return None  # prose
        why = _reasons(m["marker"], c, nested)
        if why:
            self.add(line, "malformed_item", f"{rest[: c.end()]!r} is not a requirement: {'; '.join(why)}")
            return None
        return c

    def item(self, index, m, continuation, nested, listed, raw):
        """Read the item on line `index`. `listed` and `raw` are _scan's per-line flags: a
        START report needs a list item, and a line after an unclosed fence is code on a page."""
        line = self.first_line + index
        has_nested = False
        openers = set()  # nested items that start with an unclosed <!--: START or nothing, never INLINE
        for k, text in continuation:
            sub = ITEM_RE.fullmatch(text)
            if sub is not None:
                has_nested = True
                self.candidate(self.first_line + k, sub, nested=True)
                if _opens_comment(sub["rest"]):
                    if listed[k] and not _text_is_code(text, sub):
                        self.add(self.first_line + k, "malformed_comment", START)
                    openers.add(k)
        rest = m["rest"]
        if _opens_comment(rest):
            # Not when a page shows the line as code: text past the marker, or 4 in with no item around it.
            if listed[index] and not (_text_is_code(m.string, m) or (not nested and _width(m["indent"]) >= 4)):
                self.add(line, "malformed_comment", START)
        elif rest is not None and rest.startswith("<!--"):
            k = _comment_end(rest)
            if k != -1 and CANDIDATE_RE.match(rest[k + 3 :].lstrip(" \t")):
                self.add(line, "malformed_comment", BEFORE_ID)
                return
        c = self.candidate(line, m, nested=nested)
        if c is None:
            return
        rid = ReqId(self.namespace, c["code"], int(c["digits"]))  # _reasons found no fault
        # A misplaced or duplicate item is dropped before its block is read.
        if rid.code not in self.layers:
            allowed = ", ".join(code for code in LAYERS if code in self.layers)
            self.add(line, "misplaced", f"{rid} is a {rid.code} item, a layer this file does not hold ({allowed})")
            return
        first = self.requirements.get(str(rid))
        if first is not None:
            self.add(line, "duplicate_id",
                     f"{rid} is also defined on line {first.line}; the first definition is kept")
            return
        tail = rest[c.end() :]
        parts = ((self.first_line + k, t) for k, t in continuation if k not in openers and not raw[k])
        for at, part in chain([(line, tail)], parts):
            if _comment_outside_code(part):
                self.add(at, "malformed_comment", INLINE)
                break
        # The block closes the first line, or else the joined text. With a nested
        # item in the text, the block that ends it may be that item's: it is not read.
        head = tail.strip()
        block = _ending_block(head)
        if block is not None:
            head = head[: block.start()].rstrip()
        parts = [head] + [text.strip() for _, text in continuation]
        text = " ".join(part for part in parts if part)
        second = None
        if block is None:
            block = None if has_nested else _ending_block(text)
            if block is not None:
                text = text[: block.start()].rstrip()
        else:
            second = _ending_block(text)
        starts = set()
        for other in BLOCK_RE.finditer(text):
            starts.add(other.start())
            if second is not None and other.start() == second.start():
                self.add(line, "malformed_meta",
                         "a second meta block on one item; it stays in the text and is not read")
            else:
                self.add(line, "malformed_meta",
                         "a meta block inside the text; it stays in the text and is not read")
        # A block opener no match consumed has no closing brace, or holds one: once per item.
        # A block's body holds no brace, so a consumed opener is always a block's start.
        for opener in _OPENER_RE.finditer(text):
            if opener.start() not in starts:
                self.add(line, "malformed_meta", f"the meta block starting {opener.group(0)!r} cannot be "
                                                 "read: an unclosed or nested brace")
                break
        refines, needs = self.links(line, rid, block)
        self.requirements[str(rid)] = Requirement(rid, rid.code, text, refines, needs, self.source, line)

    def links(self, line, rid, block):
        """(refines, needs) read from an item's meta block."""
        refines, needs = None, []
        if block is None:
            return refines, needs
        entries, why = _block_entries(block.group(0))
        if why is not None:
            self.add(line, "malformed_meta", f"the meta block {block.group(0)!r} cannot be read: {why}")
            return refines, needs
        seen = set()
        for key, value in entries:
            if key not in ("refines", "needs"):
                self.add(line, "malformed_meta",
                         f"unknown key {key!r} in the meta block (a requirement's keys are refines and needs)")
                continue
            if key in seen:
                self.add(line, "malformed_meta", f"key {key!r} is repeated in the meta block; the first is kept")
                continue
            seen.add(key)
            if key == "refines":
                if isinstance(value, list):
                    self.add(line, "malformed_meta", "refines: takes one target, not a list; it is not read")
                else:
                    refines = self.target(line, key, value)
            else:
                targets = value if isinstance(value, list) else [value]
                if rid.code != "UC":
                    self.add(line, "misplaced", f"needs: on {rid}, which is not a use case; it is not read")
                elif not targets:
                    self.add(line, "malformed_meta", "needs: [] names no target")
                else:
                    needs = [t for t in (self.target(line, key, v) for v in targets) if t is not None]
        return refines, needs

    def target(self, line, key, value):
        """A link target as a ReqId, or None (reported) when it cannot be read."""
        t = parse_id(value, self.namespace)
        if t is None:
            self.add(line, "malformed_link", f"{key}: {value!r} is not a requirement ID")
            return None
        if t.namespace == self.br_namespace and self.namespace != self.br_namespace:
            self.add(line, "misplaced", f"{key}: {t} is in the installation namespace {self.br_namespace!r}, "
                                        "which only the portfolio file may link to; it is not read")
            return None
        return t

    def own(self, t):
        """`t`'s key when it names a requirement of this file's namespace, else None."""
        return str(t) if t is not None and t.namespace == self.namespace else None

    def check_links(self):
        """Report own-namespace targets that are undefined. Another site's
        targets are returned unresolved, for the cross-file reader."""
        for r in self.requirements.values():
            for key, targets in (("refines", [r.refines]), ("needs", r.needs)):
                for t in targets:
                    own = self.own(t)
                    if own is not None and own not in self.requirements:
                        self.add(r.line, "undefined_link", f"{key}: {t} is not defined in this file")

    def check_cycles(self):
        """Report each refines: cycle once, at its earliest-line member. Each
        requirement refines at most one other: walk the paths in file order."""
        reqs = self.requirements

        def follow(key):
            nxt = self.own(reqs[key].refines)
            return nxt if nxt in reqs else None

        state = {}  # key -> "path" while on the current walk, then "done"
        for start in reqs:
            path, node = [], start
            while node is not None and node not in state:
                state[node] = "path"
                path.append(node)
                node = follow(node)
            if node is not None and state[node] == "path":
                cycle = path[path.index(node) :]
                first = cycle.index(min(cycle, key=lambda k: reqs[k].line))
                cycle = cycle[first:] + cycle[:first]
                head = reqs[cycle[0]]
                if len(cycle) == 1:
                    self.add(head.line, "refines_cycle", f"{head.id} refines itself")
                else:
                    self.add(head.line, "refines_cycle", "refines cycle: " + " -> ".join(cycle + cycle[:1]))
            for key in path:
                state[key] = "done"

    def result(self):
        self.check_links()
        self.check_cycles()
        self.report.sort(key=lambda e: (e.line is not None, e.line or 0, _KIND_ORDER[e.kind]))
        return RequirementSet(self.namespace, self.requirements, self.report)


def split_lines(text):
    """A text's lines, split at each LF with a trailing CR removed: never
    splitlines(), which also breaks at U+2028 and others. A leading byte-order
    mark is skipped, also when a caller passes a file's first section."""
    text = text.removeprefix(_BOM)
    return [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]


def read_utf8(roots, path, label, *, key, site=None):
    """(text, None), or (None, reason) when the file at `path` cannot be read
    under `roots` or is not UTF-8. `label` names the file in the reason ("the
    requirements file"); `key` names it in a refusal."""
    try:
        data = read_bytes_confined(roots, path, key=key, site=site)
    except (OSError, ValueError, OutsideRoots) as e:
        # ValueError: an embedded NUL, or a path too long for Windows.
        return None, f"cannot read {label}: {error_reason(e)}"
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError as e:
        return None, f"{label} is not valid UTF-8 at byte {e.start}"


def parse_requirements(text, namespace, *, source, first_line=1, layers, br_namespace) -> RequirementSet:
    """Read a requirements file's text. `source` labels every requirement and
    report entry; `first_line` is the line of a larger file the text starts on.
    """
    _check_args(namespace, layers, br_namespace)
    reader = _Reader(namespace, source, first_line, frozenset(layers), br_namespace)
    lines = split_lines(text)
    hidden, skip, nested, comment, _, listed, raw = _scan(lines)
    if comment is not None:
        reader.add(first_line + comment[0], "malformed_comment", comment[1])
    i = 0
    while i < len(lines):
        m = None if hidden[i] else ITEM_RE.fullmatch(lines[i])
        if m is None:
            i += 1
            continue
        # The item's text runs on through lines indented deeper than its marker,
        # and across the comment lines inside it.
        indent = _width(m["indent"])
        end = i + 1
        while end < len(lines) and (skip[end] or (not hidden[end] and lines[end].strip()
                                                  and _width(lines[end]) > indent)):
            end += 1
        continuation = [(k, lines[k]) for k in range(i + 1, end) if not skip[k]]
        reader.item(i, m, continuation, nested=nested[i], listed=listed, raw=raw)
        i = end
    return reader.result()


def read_requirements(roots, path, namespace, *, layers, br_namespace) -> RequirementSet:
    """Read a requirements file under `roots` as UTF-8; a file that cannot be
    read gives an empty set with one `unreadable` entry, never an exception."""
    _check_args(namespace, layers, br_namespace)
    source = str(path)

    text, reason = read_utf8(roots, path, "the requirements file", key=f"site.{namespace}.requirements")
    if text is None:
        return RequirementSet(namespace, {}, [ReportEntry(source, None, "unreadable", reason)])
    return parse_requirements(text, namespace, source=source, first_line=1,
                              layers=layers, br_namespace=br_namespace)


# --- in the trace ----------------------------------------------------------------


def in_trace_sites(config, sites):
    """({site name: requirements path}, warnings) for the sites in the trace.

    A site is in the trace only when its `requirements` key is set. A site
    whose name is not a usable namespace (it equals br_namespace, or fails
    ID_TOKEN_RE) is still a site, but its key is not read and it is warned.
    """
    traced, warnings = {}, []
    br = config.requirements.br_namespace
    for site in sites:
        override = config.sites.get(site.name)
        path = override.requirements if override is not None else None
        if path is None:
            continue
        name = shown(site.name)
        prefix = f"{config.path}: key 'site.{name}.requirements' is not read: site {name}"
        if not ID_TOKEN_RE.fullmatch(site.name):
            why = f"{prefix}'s name does not match {ID_TOKEN_RE.pattern}, so it cannot be in the trace"
        elif site.name == br:
            why = (f"{prefix} has the name of the installation namespace (br_namespace = {br!r}), "
                   "so it cannot be in the trace")
        else:
            traced[site.name] = path
            continue
        warnings.append(SiteWarning(f"site.{site.name}.requirements", why))
    return traced, warnings
