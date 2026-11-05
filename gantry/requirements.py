"""Requirements files: the eight layers, the ID grammar and the reader (#15).

A requirement ID is `<namespace>:<code>-<n>` (`gantry:FR-12`), a strict subset
of the `<namespace>:<id>` shape mtibbits/devagent#888 checks in `satisfies:`.
A requirements file is Markdown; a requirement is an unordered list item whose
text starts with a bare ID and a colon (`- FR-12: <text>`). Every other line is
prose, except an ID-shaped item that is not a requirement, which is reported.
The module has its own meta-block reader and shares no code with
gantry/wbs_stopgap.py. docs/requirements-format.md states the rules.
"""

import re
from dataclasses import dataclass
from typing import NamedTuple

# DEFAULT_BR_NAMESPACE is re-exported: its one home is gantry.config.
from gantry.config import DEFAULT_BR_NAMESPACE, ID_TOKEN_RE, error_reason

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

REPORT_KINDS = ("unreadable", "malformed_item", "malformed_meta", "malformed_link",
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
FENCE_RE = re.compile(r"[ \t]*(?P<run>`{3,}|~{3,})(?P<info>.*)")  # fullmatch
BLOCK_RE = re.compile(r"\{[ \t]*[A-Za-z_][A-Za-z0-9_]*[ \t]*:[^{}]*\}")  # finditer
_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BOM = "\N{BYTE ORDER MARK}"

_WHY = {
    "ordered": "an ordered list item (a requirement uses -, * or +)",
    "marks": "marks around the ID (write it plain)",
    "namespace": "a namespace (a requirement is defined with a bare ID)",
    "space": "a space before the colon",
    "code": "{code!r} is not a layer code (BR, BRule, UC, SR, FR, QA, EI, CON)",
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


def _fenced(lines):
    """Which lines a fence hides, opener through closer. An opener with no
    closer is plain text, so it never hides the rest of the file."""
    fenced = [False] * len(lines)
    no_closer = {}
    i = 0
    while i < len(lines):
        j = _closer(lines, i, no_closer)
        if j is None:
            i += 1
        else:
            fenced[i : j + 1] = [True] * (j + 1 - i)
            i = j + 1
    return fenced


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
    """[(key, value)] from a meta block, a value being a str or a list of str."""
    entries = []
    for entry in _split(block[1:-1], ",") or []:
        key, _, value = entry.partition(":")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = [_unquote(item) for item in _split(value[1:-1], ",") or []]
            entries.append((key.strip(), [item for item in items if item]))
        else:
            entries.append((key.strip(), _unquote(value)))
    return entries


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

    def item(self, index, m, continuation):
        line = self.first_line + index
        for k, text in continuation:
            nested = ITEM_RE.fullmatch(text)
            if nested is not None:
                self.candidate(self.first_line + k, nested, nested=True)
        c = self.candidate(line, m, nested=False)
        if c is None:
            return
        rid = parse_id(c["code"] + c["dash"] + c["digits"] + c["suffix"], self.namespace)
        head = m["rest"][c.end() :].strip()
        block = _ending_block(head)
        if block is not None:
            head = head[: block.start()].rstrip()
        parts = [head] + [text.strip() for _, text in continuation]
        text = " ".join(part for part in parts if part)
        if block is None:
            block = _ending_block(text)
            if block is not None:
                text = text[: block.start()].rstrip()
        refines, needs = None, []
        if block is not None:
            for key, value in _block_entries(block.group(0)):
                if key == "refines":
                    refines = parse_id(value, self.namespace)
                elif key == "needs":
                    targets = value if isinstance(value, list) else [value]
                    needs = [parse_id(target, self.namespace) for target in targets]
        self.requirements[str(rid)] = Requirement(rid, rid.code, text, refines, needs, self.source, line)

    def result(self):
        self.report.sort(key=lambda e: (e.line is not None, e.line or 0, _KIND_ORDER[e.kind]))
        return RequirementSet(self.namespace, self.requirements, self.report)


def parse_requirements(text, namespace, *, source, first_line=1, layers, br_namespace) -> RequirementSet:
    """Read a requirements file's text. `source` labels every requirement and
    report entry; `first_line` is the line of a larger file the text starts on.
    """
    _check_args(namespace, layers, br_namespace)
    reader = _Reader(namespace, source, first_line, frozenset(layers), br_namespace)
    # split("\n"), never splitlines(), which also breaks at U+2028 and others.
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    fenced = _fenced(lines)
    i = 0
    while i < len(lines):
        m = None if fenced[i] else ITEM_RE.fullmatch(lines[i])
        if m is None:
            i += 1
            continue
        # The item's text runs on through lines indented deeper than its marker.
        indent = _width(m["indent"])
        end = i + 1
        while end < len(lines) and not fenced[end] and lines[end].strip() and _width(lines[end]) > indent:
            end += 1
        reader.item(i, m, [(k, lines[k]) for k in range(i + 1, end)])
        i = end
    return reader.result()


def read_requirements(path, namespace, *, layers, br_namespace) -> RequirementSet:
    """Read a requirements file as UTF-8; a file that cannot be read gives an
    empty set with one `unreadable` entry, never an exception."""
    _check_args(namespace, layers, br_namespace)
    source = str(path)

    def unreadable(message):
        return RequirementSet(namespace, {}, [ReportEntry(source, None, "unreadable", message)])

    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except (OSError, ValueError) as e:
        # ValueError: an embedded NUL, or a path too long for Windows.
        return unreadable(f"cannot read the requirements file: {error_reason(e)}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        return unreadable(f"the requirements file is not valid UTF-8 at byte {e.start}")
    return parse_requirements(text.removeprefix(_BOM), namespace, source=source, first_line=1,
                              layers=layers, br_namespace=br_namespace)
