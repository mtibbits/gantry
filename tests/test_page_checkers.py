"""The static page-safety checkers, their rules file and known-bad pages (#54).

Every test page other than the two committed known-bad pages is built here, in
memory, so nothing else under tests/page_script/ is ever committed. #53's
harness tests reuse the helpers below and extend EXPECTED rather than adding a
twin of the commit-check test.
"""

import copy
import json
import re

import pytest

from page_safety import (
    HOSTILE,
    RULES,
    RULES_PATH,
    check_rules,
)

COMMENT_OPEN = "<" + "!--"


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
