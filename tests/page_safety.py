"""Static page-safety checkers for Gantry's generated pages (#54).

A plain module, not a test module, imported as page_safety the way
helpers_config is. It imports only the standard library. See
docs/page-safety.md for the rules, the rules file and the limits.
"""

import json
import re
from html.parser import HTMLParser
from itertools import islice
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
    "</title></style></textarea><script>gantrypwn(8)</script>",
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

    @property
    def is_start(self):
        return self.kind in ("start", "startend")


# Inside these a browser parses no element as raw text (foreign content), so
# a script, style or title child there is markup, and html.parser must not
# read it as raw text.
FOREIGN = ("svg", "math")


class _Scanner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events = []
        self.foreign_depth = 0

    def _add(self, kind, tag=None, attrs=(), data=None):
        self.events.append(_Event(kind, tag, tuple(attrs), data, self.getpos()[0]))

    def set_cdata_mode(self, elem, *args, **kwargs):
        if not self.foreign_depth:
            super().set_cdata_mode(elem, *args, **kwargs)

    def handle_starttag(self, tag, attrs):
        if tag in FOREIGN:
            self.foreign_depth += 1
        self._add("start", tag, attrs)

    def handle_startendtag(self, tag, attrs):
        # A start tag only: the base class would also synthesise an end tag.
        self._add("startend", tag, attrs)

    def handle_endtag(self, tag):
        if tag in FOREIGN and self.foreign_depth:
            self.foreign_depth -= 1
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

    handle_pi = unknown_decl = handle_comment

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
        if not e.is_start:
            continue
        if e.tag not in checked["allowed_tags"]:
            found.append(Finding("tag", e.tag, None, e.line))
        found += [Finding("attribute", e.tag, k, e.line) for k, _ in e.attrs if k not in checked["allowed_attrs"]]
        script_id = _attr(e, "id")
        if e.tag == "script" and script_id not in checked["own_script_ids"]:
            found.append(Finding("script", "script", script_id, e.line))
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


# --- external resources -------------------------------------------------------------

URL_ATTRS = frozenset({"src", "href", "xlink:href", "srcset", "action", "formaction", "poster", "data"})
URL_STRIP = "".join(map(chr, range(0x21)))   # what a URL parser strips: C0 controls and space
LINK_RE = re.compile(r"(?![A-Za-z][A-Za-z0-9+.\-]*:)[^\x00-\x20/\\?#][^\x00-\x20\\?#]*(?:#[^\x00-\x20]*)?")
IMPORT_RE = re.compile(r"(?i)@import")
URL_FN_RE = re.compile(r"(?i)url\((?!\s*['\"]?\s*#)")

CHARSET_META = '<meta charset="utf-8">'


def _url_ok(tag, attr, value):
    v = value.strip(URL_STRIP)
    if attr == "srcset":
        return all(not c.split() or c.split()[0].startswith("#") for c in v.split(","))
    return v == "" or v.startswith("#") or (attr == "href" and tag == "a" and LINK_RE.fullmatch(v) is not None)


def _is_csp(event):
    return event.is_start and event.tag == "meta" and (_attr(event, "http-equiv") or "").lower() == "content-security-policy"


def _is_charset(event):
    return event.is_start and event.tag == "meta" and (_attr(event, "charset") or "").lower() == "utf-8"


ASCII_SPACE = " \t\n\r\f"   # the only whitespace a browser ignores in the head


def _blank(event):
    return event.kind == "text" and not event.data.strip(ASCII_SPACE)


def _next_event(events, i):
    """The first event after index i that is not whitespace text, or None."""
    return next((e for e in islice(events, i + 1, None) if not _blank(e)), None)


def _style_lines(text, line):
    lines = []
    if IMPORT_RE.search(text):
        lines.append(f"@import in <style> (line {line})")
    if URL_FN_RE.search(text):
        lines.append(f"url() other than url(#id) in <style> (line {line})")
    return lines


def _resource_lines(events):
    lines = []
    # A style's sheet is all its text up to </style>: in foreign content the
    # text comes in pieces between child tags, and the pieces join.
    style_line, style_text = None, []
    for e in events:
        if style_line is not None:
            if e.kind == "text":
                style_text.append(e.data)
            elif e.kind == "end" and e.tag == "style":
                lines += _style_lines("".join(style_text), style_line)
                style_line, style_text = None, []
        if e.kind == "start" and e.tag == "style" and style_line is None:
            style_line = e.line
        if not e.is_start:
            continue
        for name, value in e.attrs:
            value = value or ""  # a valueless attribute arrives as None
            if name in URL_ATTRS and not _url_ok(e.tag, name, value):
                lines.append(f"{name} on <{e.tag}> is not empty or a #fragment (line {e.line})")
            if name == "style" and IMPORT_RE.search(value):
                lines.append(f"@import in style on <{e.tag}> (line {e.line})")
            if URL_FN_RE.search(value):
                lines.append(f"url() other than url(#id) in {name} on <{e.tag}> (line {e.line})")
        equiv = _attr(e, "http-equiv")
        if e.tag == "meta" and equiv is not None and not _is_csp(e):
            lines.append(f"meta http-equiv {equiv} is not the CSP (line {e.line})")
    if style_line is not None:
        lines += _style_lines("".join(style_text), style_line)
    return lines


def _head_lines(events):
    lines = []
    csps = [e for e in events if _is_csp(e)]
    if not csps:
        lines.append("no CSP meta")
    lines += [f"CSP meta content is not the page CSP (line {e.line})" for e in csps if _attr(e, "content") != CSP_CONTENT]
    at = next((i for i, e in enumerate(events) if _is_charset(e)), None)
    if at is None:
        # The order checks below all hang off the charset meta.
        return lines + [f"no {CHARSET_META}"]
    charset = events[at]
    after = _next_event(events, at)
    if after is None or not _is_csp(after):
        where = charset if after is None else after
        lines.append(f"{CHARSET_META} is not directly followed by the CSP meta (line {where.line})")
    head = next((i for i, e in enumerate(events) if e.kind == "start" and e.tag == "head"), None)
    if head is not None:
        first = _next_event(events, head)
        if first is not charset:
            where = events[head] if first is None else first
            lines.append(f"<head> does not open with {CHARSET_META} (line {where.line})")
    before = next((e for e in events[:at] if (e.is_start and e.tag not in ("html", "head"))
                   or e.kind == "comment" or (e.kind == "text" and not _blank(e))), None)
    if before is not None:
        lines.append(f"{CHARSET_META} is not the first element of the head (line {before.line})")
    return lines


def assert_no_external_resources(page):
    """None, or one AssertionError listing every way the page may load a resource."""
    events = _scan(page)
    lines = _resource_lines(events) + _head_lines(events)
    if lines:
        raise AssertionError("page may load external resources:\n  " + "\n  ".join(lines))
