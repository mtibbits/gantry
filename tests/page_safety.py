"""Static page-safety checkers for Gantry's generated pages (#54).

A plain module, not a test module, imported as page_safety the way
helpers_config is. It imports only the standard library. See
docs/page-safety.md for the rules, the rules file and the limits.
"""

import json
from pathlib import Path

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
