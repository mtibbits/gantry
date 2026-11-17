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


# The week view's shared display helpers (#38): one hours rule, the
# unestimated label, and the week's class tables outside CLASS_TOKENS.
from gantry import planner  # noqa: E402
from gantry import render  # noqa: E402


def test_hours_text_literals():
    cases = [(135 / 60, "2.25 h"), (120 / 60, "2 h"), (7 / 60, "0.12 h"), (15 / 60, "0.25 h"), (0.0, "0 h"),
             (270 / 60, "4.5 h"), (181.5 / 60, "3.03 h"), (52.5 / 60, "0.88 h"), (1717.5 / 60, "28.63 h")]
    assert [render.hours_text(h) for h, _ in cases] == [text for _, text in cases]


def _whole_minutes(m):
    # Integer-only: m minutes as hundredths of an hour, half up.
    cents = (m * 100 + 30) // 60
    return f"{cents // 100}.{cents % 100:02d}".rstrip("0").rstrip(".") + " h"


def test_hours_text_whole_minutes():
    bad = [m for m in [*range(200_000), 60_000_000_000] if render.hours_text(m / 60) != _whole_minutes(m)]
    assert bad == []


def test_unestimated_text():
    assert render.unestimated_text("3pr") == "unestimated (3 pr)"
    assert render.unestimated_text("2.50pr") == "unestimated (2.5 pr)"
    for raw in ("?w", None, "3 pr", "2h"):
        assert render.unestimated_text(raw) == "unestimated", raw
    assert render.non_time_text("3pr") == "3 pr"
    assert render.non_time_text("2h") is None


def test_week_class_tables():
    assert render.TAG_CLASSES == {"scored": "tag-scored", "measured": "tag-measured",
                                  "declared": "tag-declared", "estimated": "tag-estimated"}
    assert render.TIER_CLASSES == {"p0": "tier-p0", "started": "tier-started", "fair_share": "tier-fair"}
    assert render.REASON_CLASSES == {
        "blocked_externally": "why-blocked", "parked": "why-parked", "waits_on_cycle": "why-cycle",
        "cross_site_cycle": "why-cross-cycle", "waits_on_site_absent": "why-site-absent",
        "waits_on_unresolved": "why-unresolved", "waits_on_unplanned": "why-waits", "beyond_horizon": "why-beyond",
    }
    assert set(render.REASON_CLASSES) == set(planner.UNPLANNED_REASONS)
    assert render.tag_class("measured") == "tag-measured"
    assert render.tier_class("fair_share") == "tier-fair"
    assert render.reason_class("parked") == "why-parked"
    for value in ("unknown", None, ["p0"], 3):
        assert render.tag_class(value) == "tag-other"
        assert render.tier_class(value) == "tier-other"
        assert render.reason_class(value) == "why-other"
    week = render.WEEK_CLASSES
    assert week == {*render.TAG_CLASSES.values(), "tag-other", *render.TIER_CLASSES.values(), "tier-other",
                    *render.REASON_CLASSES.values(), "why-other"}
    for token in week:
        assert re.fullmatch(r"[a-z][a-z0-9-]*", token), token
    assert not week & CLASS_TOKENS
