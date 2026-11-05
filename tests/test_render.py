"""The page render helpers: escaping, the one-pass fill, the JSON embedding
and the fixed class sets (#9)."""

import json
import re

import pytest

from gantry.render import (
    CLASS_TOKENS,
    MODEL_CLASSES,
    STATE_CLASSES,
    Markup,
    embed_json,
    esc,
    fill,
    model_class,
    slack_class,
    state_class,
)
from page_safety import HOSTILE


def test_esc_escapes_five():
    assert esc("&<>\"'") == "&amp;&lt;&gt;&quot;&#x27;"
    assert esc(3) == "3"


def test_fill_single_pass():
    assert fill("{{A}}|{{B}}", A="{{B}}", B=Markup("<i>x</i>")) == "{{B}}|<i>x</i>"
    assert fill("{{A}}", A="<i>") == "&lt;i&gt;"


def test_fill_refuses_missing_and_unused():
    with pytest.raises(ValueError) as exc:
        fill("{{A}} {{B}}", A="a")
    assert str(exc.value) == "template placeholder B has no value"
    with pytest.raises(ValueError) as exc:
        fill("{{A}}", A="a", C="c")
    assert str(exc.value) == "template value C is not used"


@pytest.mark.parametrize("value", [*HOSTILE, {"__proto__": {"polluted": HOSTILE[0]}, "k": [HOSTILE[6]]}])
def test_embed_json_round_trip(value):
    text = embed_json(value)
    assert isinstance(text, Markup)
    assert json.loads(text) == value
    for bad in ("<", ">", "&", "\u2028", "\u2029"):
        assert bad not in text


FALLBACKS = ["Opus", HOSTILE[3], None, ["opus"], "opus x", "done"]


def test_class_lookup_both_branches():
    for key, token in MODEL_CLASSES.items():
        assert model_class(key) == token
    for key, token in STATE_CLASSES.items():
        assert state_class(key) == token
    for value in FALLBACKS:
        assert model_class(value) == "m-other"
        assert state_class(value) == "st-other"
    assert model_class("opus") == "m-opus"
    assert state_class("in_progress") == "st-wip"
    assert [slack_class(s) for s in (0, 1, 3, 4)] == ["crit", "near", "near", "free"]


def test_class_tokens_are_safe():
    for token in CLASS_TOKENS:
        assert re.fullmatch(r"[a-z][a-z0-9-]*", token), token
    assert CLASS_TOKENS == {
        "m-haiku", "m-sonnet", "m-opus", "m-fable", "m-other",
        "st-pending", "st-wip", "st-stuck", "st-blocked", "st-parked", "st-other",
        "crit", "near", "free", "unest", "pr-hi", "pr-p0",
    }
