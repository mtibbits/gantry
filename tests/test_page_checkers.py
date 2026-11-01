"""The static page-safety checkers, their rules file and known-bad pages (#54).

Every test page other than the two committed known-bad pages is built here, in
memory, so nothing else under tests/page_script/ is ever committed. #53's
harness tests reuse the helpers below and extend EXPECTED rather than adding a
twin of the commit-check test.
"""

import copy
import html
import json
import re

import pytest

import commit_check
import page_safety
from page_safety import (
    CSP_CONTENT,
    CSP_META,
    HOSTILE,
    RULE_KEYS,
    RULES,
    RULES_PATH,
    URL_ATTRS,
    Finding,
    assert_no_external_resources,
    assert_page_safe,
    check_rules,
    page_findings,
)
from test_samples import ROOT
from test_source_scan import ALLOW

COMMENT_OPEN = "<" + "!--"
KNOWN_BAD = RULES_PATH.parent / "known_bad"


# --- the rules file and HOSTILE -------------------------------------------------


def test_page_rules_well_formed():
    # A fresh parse of the file bytes, so AC3 has a red of its own even though
    # the module already checked RULES at import.
    fresh = json.loads(RULES_PATH.read_bytes())
    checked = check_rules(fresh)
    assert set(checked) == {"allowed_attrs", "allowed_tags", "own_script_ids"}
    assert all(isinstance(v, frozenset) for v in checked.values())
    for key, items in fresh.items():
        assert items == sorted(set(items)), key
    assert "gantry-data" in checked["own_script_ids"]
    assert "script" in checked["allowed_tags"]
    assert "details" not in checked["allowed_tags"]
    assert not [a for a in checked["allowed_attrs"] if a.startswith("on")]


def _with(key, value):
    rules = copy.deepcopy(RULES)
    rules[key] = value
    return rules


def _without(key):
    rules = copy.deepcopy(RULES)
    del rules[key]
    return rules


def _appended(key, item):
    rules = copy.deepcopy(RULES)
    rules[key].append(item)
    return rules


MALFORMED = [
    ("not_object", [], "page rules: not an object"),
    ("missing_key", _without("own_script_ids"), "page rules: keys must be exactly"),
    ("extra_key", _with("extra", []), "page rules: keys must be exactly"),
    ("not_list", _with("allowed_tags", "div"), "page rules: allowed_tags is not a list"),
    ("upper", _appended("allowed_tags", "DIV"), "is not a non-empty lower-case string"),
    ("empty", _appended("allowed_tags", ""), "is not a non-empty lower-case string"),
    ("not_str", _appended("allowed_tags", 1), "is not a non-empty lower-case string"),
    ("duplicate", _with("allowed_tags", ["p", "p"]), "page rules: allowed_tags item 'p' is listed twice"),
    ("handler", _appended("allowed_attrs", "onclick"), "page rules: allowed_attrs item 'onclick' is an event handler"),
]


@pytest.mark.parametrize("rules,message", [(r, m) for _, r, m in MALFORMED], ids=[i for i, _, _ in MALFORMED])
def test_check_rules_refuses_malformed(rules, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        check_rules(rules)


def test_hostile_covers_each_context():
    assert len(HOSTILE) == 11
    assert len(set(HOSTILE)) == 11
    for prefix in ("<script>", "</script>", "<img ", '"><svg ', "' on", COMMENT_OPEN, "&amp;", "</title>"):
        assert any(h.startswith(prefix) for h in HOSTILE), prefix
    assert any(chr(0x2028) in h and chr(0x2029) in h for h in HOSTILE)
    assert "__proto__" in HOSTILE
    assert "{{DATA}}" in HOSTILE


# --- page helpers (#53 reuses them) ---------------------------------------------


def esc(v):
    return html.escape(v, quote=True)


def embed(obj):
    """JSON safe inside a script element: no <, > or & survives."""
    text = json.dumps(obj, ensure_ascii=True)
    for ch, code in (("<", "u003c"), (">", "u003e"), ("&", "u0026")):
        text = text.replace(ch, chr(92) + code)
    return text


def line_of(page, needle):
    """1-based line of the only line holding needle (split on LF only)."""
    hits = [i for i, line in enumerate(page.split("\n")) if needle in line]
    assert len(hits) == 1, (needle, hits)
    return 1 + hits[0]


RENDER_SCRIPT = "\n".join([
    'const data = JSON.parse(document.getElementById("gantry-data").textContent);',
    'const out = document.getElementById("out");',
    "for (const v of data.values) {",
    '  const li = document.createElement("li");',
    "  li.textContent = v;",
    '  li.setAttribute("data-item", v);',
    "  out.appendChild(li);",
    "}",
])


def hostile_page(values=HOSTILE, data_values=HOSTILE, keyed=None, script=RENDER_SCRIPT, body_extra=""):
    """A clean page rendering each value as text, with the values in its data."""
    data = {"values": list(data_values), "keyed": {"__proto__": "proto-key"} if keyed is None else keyed}
    lines = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        CSP_META,
        "<title>Hostile values</title>",
        '<script type="application/json" id="gantry-data">' + embed(data) + "</script>",
        "</head>",
        "<body>",
        '<ul id="static">',
        *[f'<li data-item="{esc(v)}">{esc(v)}</li>' for v in values],
        "</ul>",
        '<ul id="out"></ul>',
        "<button disabled>x</button>",
        *([body_extra] if body_extra else []),
        '<script id="gantry-page">' + script + "</script>",
        "</body>",
        "</html>",
    ]
    return "\n".join(lines) + "\n"


def message(excinfo):
    return str(excinfo.value)


# --- page_findings and assert_page_safe -------------------------------------------


def test_page_safe_flags_element_context():
    text = (KNOWN_BAD / "element_context.html").read_bytes().decode("ascii")
    assert HOSTILE[2] in text
    n = line_of(text, "onerror=")
    assert page_findings(text) == [
        Finding("tag", "img", None, n),
        Finding("attribute", "img", "src", n),
        Finding("attribute", "img", "onerror", n),
    ]
    with pytest.raises(AssertionError) as excinfo:
        assert_page_safe(text, ())
    assert f"tag not allowed: <img> (line {n})" in message(excinfo)


def test_page_safe_flags_attribute_context():
    text = (KNOWN_BAD / "attribute_context.html").read_bytes().decode("ascii")
    assert HOSTILE[3] in text
    n = line_of(text, "onload=")
    assert page_findings(text) == [Finding("attribute", "svg", "onload", n)]
    with pytest.raises(AssertionError) as excinfo:
        assert_page_safe(text, ())
    assert f"attribute not allowed: onload on <svg> (line {n})" in message(excinfo)


ONE_PER_RULE = [
    ("comment", COMMENT_OPEN + " c -->", ("comment", None, None), "comment"),
    ("pi", "<?x?>", ("comment", None, None), "comment"),
    ("cdata", "<![CDATA[x]]>", ("comment", None, None), "comment"),
    ("ms_if", "<![if x]>", ("comment", None, None), "comment"),
    ("stray_script", '<script id="stray">1</script>', ("script", "script", "stray"),
     "script id not the page's own: <script id=\"stray\">"),
    ("idless_script", "<script>1</script>", ("script", "script", None), "script id not the page's own: <script>"),
    ("tag", "<details>note</details>", ("tag", "details", None), "tag not allowed: <details>"),
    ("attribute", '<div onclick="x">y</div>', ("attribute", "div", "onclick"), "attribute not allowed: onclick on <div>"),
]


@pytest.mark.parametrize("extra,expected,line", [c[1:] for c in ONE_PER_RULE], ids=[c[0] for c in ONE_PER_RULE])
def test_page_findings_one_per_rule(extra, expected, line):
    page = hostile_page(body_extra=extra)
    n = line_of(page, extra)
    findings = page_findings(page)
    assert [(f.rule, f.tag, f.attr) for f in findings] == [expected]
    assert findings[0].line == n
    with pytest.raises(AssertionError) as excinfo:
        assert_page_safe(page, HOSTILE)
    assert f"{line} (line {n})" in message(excinfo)


def test_page_safe_flags_raw_title():
    # Every view's title is record-derived; a raw write breaks out of it.
    page = hostile_page().replace("<title>Hostile values</title>", "<title>" + "".join(HOSTILE) + "</title>")
    with pytest.raises(AssertionError, match="script id not the page's own: <script>"):
        assert_page_safe(page, HOSTILE)


@pytest.mark.parametrize("element", ["title", "style"])
def test_svg_raw_text_is_markup(element):
    # Inside svg a browser parses no child as raw text.
    raw = hostile_page(body_extra=f"<svg><g><{element}>{HOSTILE[2]}</{element}></g></svg>")
    n = line_of(raw, "<svg><g>")
    assert Finding("tag", "img", None, n) in page_findings(raw)
    escaped = hostile_page(body_extra=f"<svg><g><{element}>{esc(HOSTILE[2])}</{element}></g></svg>")
    assert page_findings(escaped) == []


def test_svg_script_is_markup():
    # An own-id script is no cover inside svg: its img child breaks out and runs.
    raw = hostile_page(body_extra=f'<svg><script id="gantry-page">{HOSTILE[2]}</script></svg>')
    n = line_of(raw, "<svg><script")
    findings = page_findings(raw)
    assert Finding("tag", "img", None, n) in findings
    assert Finding("attribute", "img", "onerror", n) in findings


def test_page_safe_passes_clean_page():
    page = hostile_page()
    assert page_findings(page) == []
    assert assert_page_safe(page, HOSTILE) == {"values": list(HOSTILE), "keyed": {"__proto__": "proto-key"}}
    # HOSTILE in attribute values does not trip the URL rules.
    assert_no_external_resources(page)


@pytest.mark.parametrize("i", range(len(HOSTILE)))
def test_page_safe_names_value_missing_from_data(i):
    dropped = HOSTILE[i]
    keyed = {"k": "v"} if dropped == "__proto__" else None
    page = hostile_page(data_values=[v for v in HOSTILE if v != dropped], keyed=keyed)
    with pytest.raises(AssertionError) as excinfo:
        assert_page_safe(page, HOSTILE)
    text = message(excinfo)
    assert "planted value not in the embedded data: " + repr(dropped) in text
    assert text.count("planted value not in the embedded data") == 1


def _data_block(page):
    return next(line for line in page.split("\n") if 'id="gantry-data"' in line)


def _no_block(page):
    return page.replace(_data_block(page) + "\n", "")


def _two_blocks(page):
    return page.replace('<ul id="out"></ul>', '<ul id="out"></ul>\n' + _data_block(page))


def _wrong_type(page):
    return page.replace('type="application/json"', 'type="text/plain"')


@pytest.mark.parametrize("build,found", [
    (_no_block, "found 0"),
    (_two_blocks, "found 2"),
    (_wrong_type, "found 0"),
], ids=["none", "two", "wrong_type"])
def test_page_safe_data_block_count(build, found):
    page = build(hostile_page())
    with pytest.raises(AssertionError) as excinfo:
        assert_page_safe(page, HOSTILE)
    assert 'embedded data: expected exactly one <script type="application/json" id="gantry-data">, ' + found in message(excinfo)


def test_page_safe_refuses_non_json_data():
    page = hostile_page(keyed={"a": float("nan")})
    assert "NaN" in _data_block(page)
    with pytest.raises(AssertionError, match="embedded data is not JSON"):
        assert_page_safe(page, HOSTILE)


def test_page_safe_tag_outside_rules():
    page = hostile_page(body_extra="<details>note</details>")
    with pytest.raises(AssertionError, match="tag not allowed: <details>"):
        assert_page_safe(page, HOSTILE)
    rules2 = _appended("allowed_tags", "details")
    assert assert_page_safe(page, HOSTILE, rules=rules2)["values"] == list(HOSTILE)
    assert page_findings(page, rules=rules2) == []


def test_rules_default_resolved_at_call_time(monkeypatch):
    page = hostile_page(body_extra="<details>note</details>")
    monkeypatch.setattr(page_safety, "RULES", _appended("allowed_tags", "details"))
    assert assert_page_safe(page, HOSTILE)["values"] == list(HOSTILE)


def test_rules_argument_is_checked():
    page = hostile_page()
    with pytest.raises(ValueError, match="keys must be exactly"):
        page_findings(page, rules={"allowed_tags": []})
    with pytest.raises(ValueError, match="keys must be exactly"):
        assert_page_safe(page, HOSTILE, rules={"allowed_tags": []})


def test_page_safe_refuses_bare_string_planted():
    with pytest.raises(TypeError, match="not one string"):
        assert_page_safe(hostile_page(), HOSTILE[0])
    with pytest.raises(TypeError, match="7"):
        assert_page_safe(hostile_page(), ["x", 7])


# --- assert_no_external_resources --------------------------------------------------

HEAD = '<meta charset="utf-8">\n' + CSP_META + "\n<title>t</title>"
S2 = "/" * 2
F3 = "file:" + "/" * 3


def ext_page(head=HEAD, body="", explicit_head=True):
    lines = ["<!doctype html>", '<html lang="en">']
    lines += ["<head>", head, "</head>"] if explicit_head else [head]
    lines += ["<body>", body, "</body>", "</html>"]
    return "\n".join(lines) + "\n"


def external_lines(page):
    """The problem lines assert_no_external_resources raises with."""
    with pytest.raises(AssertionError) as excinfo:
        assert_no_external_resources(page)
    first, *lines = message(excinfo).split("\n")
    assert first == "page may load external resources:"
    return [line.removeprefix("  ") for line in lines]


def _link(href):
    return ext_page(body=f'<a href="{href}">x</a>')


def _https(tag, attr, inner=""):
    return ext_page(body=f'<{tag} {attr}="https://example.invalid/a">{inner}</{tag}>')


def _charset_after_body_element():
    page = ext_page(explicit_head=False)
    return page.replace('<html lang="en">\n', '<html lang="en">\n<p>x</p>\n', 1)


EXTERNAL_FAILS = [
    ("http_src", ext_page(body='<script src="http://example.invalid/a.js"></script>'), "src on <script>"),
    ("https_href", ext_page(body='<link rel="stylesheet" href="https://example.invalid/a.css">'), "href on <link>"),
    ("protocol_relative_src", ext_page(body='<img src="' + S2 + 'example.invalid/a.png">'), "src on <img>"),
    ("relative_src", ext_page(body='<img src="a.png">'), "src on <img>"),
    ("file_href", ext_page(body='<link rel="stylesheet" href="' + F3 + 'a.css">'), "href on <link>"),
    ("data_href", ext_page(body='<link rel="stylesheet" href="data:text/css,p{}">'), "href on <link>"),
    ("xlink_href", ext_page(body='<svg><use xlink:href="https://example.invalid/s.svg#a"></use></svg>'),
     "xlink:href on <use>"),
    ("action", _https("form", "action"), "action on <form>"),
    ("formaction", _https("button", "formaction", "x"), "formaction on <button>"),
    ("poster", _https("video", "poster"), "poster on <video>"),
    ("data", _https("object", "data"), "data on <object>"),
    ("srcset_second", ext_page(body='<img srcset="#a 1x, https://example.invalid/b.png 2x">'), "srcset on <img>"),
    ("https_link", _link("https://example.invalid/x.html"), "href on <a>"),
    ("javascript_link", _link("javascript:gantrypwn(1)"), "href on <a>"),
    ("protocol_relative_link", _link(S2 + "example.invalid/x.html"), "href on <a>"),
    ("file_link", _link(F3 + "x.html"), "href on <a>"),
    ("data_link", _link("data:text/html,x"), "href on <a>"),
    ("root_relative_link", _link("/x.html"), "href on <a>"),
    ("query_link", _link("other.html?x=1"), "href on <a>"),
    ("backslash_link", _link(chr(92) + "x.html"), "href on <a>"),
    ("tab_scheme_link", _link("java" + "\t" + "script:x"), "href on <a>"),
    ("blob_src", ext_page(body='<img src="blob:https://example.invalid/1">'), "src on <img>"),
    ("meta_refresh", ext_page(head=HEAD + '\n<meta http-equiv="refresh" content="0; url=other.html">'),
     "meta http-equiv refresh is not the CSP"),
    ("import_style", ext_page(body='<style>@import "a.css";</style>'), "@import in <style>"),
    ("import_style_attr", ext_page(body="<p style=\"@import 'a.css'\">x</p>"), "@import in style on <p>"),
    ("url_style", ext_page(body="<style>p { background: url(https://example.invalid/a.png) }</style>"),
     "url() other than url(#id) in <style>"),
    # An svg style's sheet is its text pieces joined, whatever child tags split them.
    ("import_svg_style_split", ext_page(body='<svg><style><g></g>@imp<g></g>ort "https://example.invalid/a.css";</style></svg>'),
     "@import in <style>"),
    ("url_filter_attr", ext_page(body='<svg><rect filter="url(https://example.invalid/f.svg#f)"></rect></svg>'),
     "url() other than url(#id) in filter on <rect>"),
    ("no_csp", ext_page(head='<meta charset="utf-8">\n<title>t</title>'), "no CSP meta"),
    ("other_csp", ext_page(head=HEAD.replace(CSP_CONTENT, "default-src *")), "CSP meta content is not the page CSP"),
    ("csp_not_after_charset", ext_page(head='<meta charset="utf-8">\n<title>t</title>\n' + CSP_META),
     'is not directly followed by the CSP meta'),
    # Only ASCII whitespace is ignorable in the head; a no-break space opens the body.
    ("nbsp_between_metas", ext_page(head=HEAD.replace("\n", "&nbsp;\n", 1)),
     "is not directly followed by the CSP meta"),
    ("nbsp_before_charset", ext_page(head="&nbsp;" + HEAD), "<head> does not open with"),
    ("head_end_tag_first", ext_page(head="<head></head>\n" + HEAD, explicit_head=False), "<head> does not open with"),
    ("charset_after_body_element", _charset_after_body_element(), "is not the first element of the head"),
]


@pytest.mark.parametrize("text,substring", [c[1:] for c in EXTERNAL_FAILS], ids=[c[0] for c in EXTERNAL_FAILS])
def test_no_external_resources_fails(text, substring):
    lines = external_lines(text)
    matched = [line for line in lines if substring in line]
    assert matched, lines
    # Only an absence has no location.
    if substring != "no CSP meta":
        assert re.search(r" \(line \d+\)$", matched[0]), lines


def test_no_charset_reports_only_the_absence():
    # With no charset meta, the order checks that hang off it are skipped.
    assert external_lines(ext_page(head=CSP_META + "\n<title>t</title>")) == ['no <meta charset="utf-8">']


def test_head_opens_with_title_lines():
    page = ext_page(head='<title>t</title>\n<meta charset="utf-8">\n' + CSP_META)
    n = line_of(page, "<title>t</title>")
    assert external_lines(page) == [
        f'<head> does not open with <meta charset="utf-8"> (line {n})',
        f'<meta charset="utf-8"> is not the first element of the head (line {n})',
    ]


CLEAN_BODY = "\n".join([
    "<style>marker { fill: url(#marker) } p { background: url( \"#a\" ) }</style>",
    '<svg><defs><marker id="arrow"></marker></defs><path d="M0 0" marker-end="url(#arrow)"></path>'
    '<use href="#m"></use></svg>',
    '<a href="#leaf">x</a>',
    '<img src="">',
    '<a href="#leaf%2D1">x</a>',
    '<li data-item="x" data-value="y">z</li>',
    '<a href="other.html#leaf-1">x</a>',
    '<a href="sites/other.html#leaf-1">x</a>',
    '<a href="../index.html">x</a>',
    "<button disabled>x</button>",
    "<a href>x</a>",
])


def test_no_external_resources_passes_clean_page():
    assert_no_external_resources(ext_page(body=CLEAN_BODY))


def test_no_external_resources_without_explicit_head():
    assert_no_external_resources(ext_page(body=CLEAN_BODY, explicit_head=False))


# --- the committed page files ---------------------------------------------------------

PAGE_SCRIPT = "tests/page_script"
# Every file under tests/page_script; #53 adds its own here.
EXPECTED = {"page_rules.json", "known_bad/element_context.html", "known_bad/attribute_context.html"}
ASCII_LF = sorted({f"{PAGE_SCRIPT}/{rel}" for rel in EXPECTED} | {"tests/page_safety.py", "tests/test_page_checkers.py"})
SKIP_DIRS = {"__pycache__", "node_modules"}
# docs/ has no eol rule and may be CRLF on a Windows checkout: the commit
# check, not the byte test.
DOC = "docs/page-safety.md"


def test_page_script_files_pass_commit_check():
    folder = ROOT / PAGE_SCRIPT
    found = {
        p.relative_to(folder).as_posix()
        for p in folder.rglob("*")
        if p.is_file() and not SKIP_DIRS & set(p.relative_to(folder).parts)
    }
    assert found == EXPECTED

    # tests/** is eol=lf, so these hold printable ASCII and LF only.
    for rel in ASCII_LF:
        data = (ROOT / rel).read_bytes()
        assert all(32 <= b < 127 or b == 10 for b in data), rel

    for rel in ASCII_LF + [DOC]:
        assert commit_check.check_file(rel, (ROOT / rel).read_bytes(), ALLOW) == [], rel

    # The same predicate refuses a page outside the known-bad folder.
    data = (ROOT / PAGE_SCRIPT / "known_bad" / "element_context.html").read_bytes()
    findings = commit_check.check_file(f"{PAGE_SCRIPT}/element_context.html", data, [])
    assert any("generated-output (html page)" in f for f in findings), findings


# --- the doc ------------------------------------------------------------------------------


def test_page_safety_doc_states_contract():
    doc = (ROOT / DOC).read_bytes().decode("utf-8")
    assert CSP_META in doc
    names = [
        "HOSTILE", "RULES", "check_rules", "page_findings", "assert_page_safe",
        "assert_no_external_resources", "CSP_META", "page_rules.json", "tests/page_script/known_bad/",
        *RULE_KEYS, *sorted(URL_ATTRS),
    ]
    assert [n for n in names if f"`{n}`" not in doc] == []
    assert "\n## Limits" in doc
    assert COMMENT_OPEN not in doc


# Each documented blind spot, built: a fix that closes one reddens here, and
# the doc's Limits section changes with it.
LIMITS = [
    ("script_sink", lambda: assert_page_safe(
        hostile_page(script="document.getElementById('out').innerHTML = '<b>' + 'x' + '</b>';"), HOSTILE)),
    ("css_escape", lambda: assert_no_external_resources(
        ext_page(body="<style>p { background: u" + chr(92) + "72l(https://example.invalid/a.png) }</style>"))),
    ("image_set", lambda: assert_no_external_resources(
        ext_page(body='<style>p { background: image-set("https://example.invalid/a.png" 1x) }</style>'))),
]


@pytest.mark.parametrize("check", [c[1] for c in LIMITS], ids=[c[0] for c in LIMITS])
def test_documented_limits_hold(check):
    check()
