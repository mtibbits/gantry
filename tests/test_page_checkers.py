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

import page_safety
from page_safety import (
    CSP_META,
    HOSTILE,
    RULES,
    RULES_PATH,
    Finding,
    assert_page_safe,
    check_rules,
    page_findings,
)

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
    assert len(HOSTILE) == 10
    assert len(set(HOSTILE)) == 10
    for prefix in ("<script>", "</script>", "<img ", '"><svg ', "' on", COMMENT_OPEN, "&amp;"):
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


def test_page_safe_passes_clean_page():
    page = hostile_page()
    assert page_findings(page) == []
    assert assert_page_safe(page, HOSTILE) == {"values": list(HOSTILE), "keyed": {"__proto__": "proto-key"}}


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


def _details_rules():
    rules = copy.deepcopy(RULES)
    rules["allowed_tags"].append("details")
    return rules


def test_page_safe_tag_outside_rules():
    page = hostile_page(body_extra="<details>note</details>")
    with pytest.raises(AssertionError, match="tag not allowed: <details>"):
        assert_page_safe(page, HOSTILE)
    rules2 = _details_rules()
    assert assert_page_safe(page, HOSTILE, rules=rules2)["values"] == list(HOSTILE)
    assert page_findings(page, rules=rules2) == []


def test_rules_default_resolved_at_call_time(monkeypatch):
    page = hostile_page(body_extra="<details>note</details>")
    monkeypatch.setattr(page_safety, "RULES", _details_rules())
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
