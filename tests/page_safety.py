"""Static page-safety checkers for Gantry's generated pages (#54).

A plain module, not a test module, imported as page_safety the way
helpers_config is. It imports only the standard library. See
docs/page-safety.md for the rules, the rules file and the limits.
"""

import json
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
RULES_PATH = HERE / "page_script" / "page_rules.json"

CSP_CONTENT = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
CSP_META = '<meta http-equiv="Content-Security-Policy" content="' + CSP_CONTENT + '">'
RULE_KEYS = ("allowed_attrs", "allowed_tags", "own_script_ids")
DATA_ID = "gantry-data"
DATA_TYPE = "application/json"

# Strings a view's tests plant in a page, one per context a value can break
# out of. Tests index it, so the order is fixed.
HOSTILE = (
    "<script>gantrypwn(1)</script>",
    "</script><script>gantrypwn(2)</script>",
    "<img src=x onerror=gantrypwn(3)>",
    '"><svg onload=gantrypwn(4)>',
    "' onmouseover='gantrypwn(5)",
    "<" + "!-- gantrypwn(6)",
    "a" + chr(0x2028) + "b" + chr(0x2029) + "gantrypwn(7)",
    "__proto__",
    "{{DATA}}",
    "&amp;lt;b&amp;gt;",
)


def check_rules(rules):
    """{key: frozenset} for a page-rules object; ValueError naming its defect."""
    if not isinstance(rules, dict):
        raise ValueError("page rules: not an object")
    if sorted(rules) != list(RULE_KEYS):
        raise ValueError(f"page rules: keys must be exactly {', '.join(RULE_KEYS)}; got {sorted(rules)}")
    checked = {}
    for key in RULE_KEYS:
        items = rules[key]
        if not isinstance(items, list):
            raise ValueError(f"page rules: {key} is not a list")
        seen = set()
        for item in items:
            if not (isinstance(item, str) and item and item == item.lower()):
                raise ValueError(f"page rules: {key} item {item!r} is not a non-empty lower-case string")
            if item in seen:
                raise ValueError(f"page rules: {key} item {item!r} is listed twice")
            if key == "allowed_attrs" and item.startswith("on"):
                raise ValueError(f"page rules: {key} item {item!r} is an event handler")
            seen.add(item)
        checked[key] = frozenset(seen)
    return checked


# The plain dict json.loads gives; checked at import, so a bad file is a
# collection error for every importer.
RULES = json.loads(RULES_PATH.read_bytes())
check_rules(RULES)


def _resolve(rules):
    # RULES is read at call time, never bound as a default.
    return check_rules(RULES if rules is None else rules)


# --- one scan, shared by both checkers -------------------------------------------


class _Event(NamedTuple):
    kind: str  # start, end, startend, text, comment or decl
    tag: str | None
    attrs: tuple
    data: str | None
    line: int


class _Scanner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events = []

    def _add(self, kind, tag=None, attrs=(), data=None):
        self.events.append(_Event(kind, tag, tuple(attrs), data, self.getpos()[0]))

    def handle_starttag(self, tag, attrs):
        self._add("start", tag, attrs)

    def handle_startendtag(self, tag, attrs):
        # A start tag only: the base class would also synthesise an end tag.
        self._add("startend", tag, attrs)

    def handle_endtag(self, tag):
        self._add("end", tag)

    def handle_data(self, data):
        last = self.events[-1] if self.events else None
        if last is not None and last.kind == "text":
            # Text arrives in pieces (script and style bodies above all).
            self.events[-1] = last._replace(data=last.data + data)
        else:
            self._add("text", data=data)

    # Python versions route the comment-like forms through different
    # callbacks; counting each callback once makes the result independent
    # of the routing.
    def handle_comment(self, data):
        self._add("comment", data=data)

    def handle_pi(self, data):
        self._add("comment", data=data)

    def unknown_decl(self, data):
        self._add("comment", data=data)

    def handle_decl(self, decl):
        self._add("decl" if decl.lower().startswith("doctype") else "comment", data=decl)


def _scan(page):
    scanner = _Scanner()
    scanner.feed(page)
    scanner.close()
    return scanner.events


def _attr(event, name):
    """The value of the first attribute called name, or None."""
    return next((v for k, v in event.attrs if k == name), None)


# --- the page rules ---------------------------------------------------------------


class Finding(NamedTuple):
    rule: str  # tag, attribute, comment or script
    tag: str | None
    attr: str | None
    line: int


def _findings(events, checked):
    found = []
    for e in events:
        if e.kind == "comment":
            found.append(Finding("comment", None, None, e.line))
        if e.kind not in ("start", "startend"):
            continue
        if e.tag not in checked["allowed_tags"]:
            found.append(Finding("tag", e.tag, None, e.line))
        found += [Finding("attribute", e.tag, k, e.line) for k, _ in e.attrs if k not in checked["allowed_attrs"]]
        if e.tag == "script" and _attr(e, "id") not in checked["own_script_ids"]:
            found.append(Finding("script", "script", _attr(e, "id"), e.line))
    return found


def page_findings(page, rules=None):
    """Each break of the page rules, in document order."""
    return _findings(_scan(page), _resolve(rules))


def _finding_line(f):
    if f.rule == "tag":
        return f"tag not allowed: <{f.tag}> (line {f.line})"
    if f.rule == "attribute":
        return f"attribute not allowed: {f.attr} on <{f.tag}> (line {f.line})"
    if f.rule == "comment":
        return f"comment (line {f.line})"
    opener = "<script>" if f.attr is None else f'<script id="{f.attr}">'
    return f"script id not the page's own: {opener} (line {f.line})"


def _refuse_constant(name):
    raise ValueError(f"{name} is not JSON")


def _strings(node):
    """Every string value and object key, at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
    elif isinstance(node, str):
        yield node


def _data_texts(events):
    """The text of each embedded-data block."""
    texts = []
    for i, e in enumerate(events):
        if e.kind == "start" and e.tag == "script" and _attr(e, "id") == DATA_ID and _attr(e, "type") == DATA_TYPE:
            after = events[i + 1] if i + 1 < len(events) else None
            texts.append(after.data if after is not None and after.kind == "text" else "")
    return texts


def assert_page_safe(page, planted, rules=None):
    """The page's embedded data, or one AssertionError listing every problem."""
    if isinstance(planted, str):
        raise TypeError("planted must be a collection of strings, not one string")
    planted = list(planted)
    for p in planted:
        if not isinstance(p, str):
            raise TypeError(f"planted item {p!r} is not a string")
    checked = _resolve(rules)
    events = _scan(page)
    lines = [_finding_line(f) for f in _findings(events, checked)]
    texts = _data_texts(events)
    data = None
    if len(texts) != 1:
        lines.append(f'embedded data: expected exactly one <script type="{DATA_TYPE}" id="{DATA_ID}">, found {len(texts)}')
    else:
        try:
            data = json.loads(texts[0], parse_constant=_refuse_constant)
        except ValueError as exc:
            lines.append(f"embedded data is not JSON: {exc}")
        else:
            present = set(_strings(data))
            lines += [f"planted value not in the embedded data: {p!r}" for p in planted if p not in present]
    if lines:
        raise AssertionError("page breaks the page rules:\n  " + "\n  ".join(lines))
    return data
