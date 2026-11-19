"""The shared leaf box rule (#170): one cut, wrap, band and class rule for the
worksite network's boxes and the week view's placements.

Every expected value is a hand-worked literal (found with the worksite's
_cut/_wrap at 0f801f4), never computed through leafbox.
"""

import re
from pathlib import Path

import pytest

from gantry import render
from gantry.model import Models
from gantry.views import leafbox
from gantry.views.week import render_week
from test_week_page import STAMP, make_plan, make_result
from test_worksite_page import page_for


def test_cut_literals():
    assert leafbox.cut("abcdefghijklmnopq", 16) == "abcdefghijklmno…"
    assert leafbox.cut("abcdefghijklmnop", 16) == "abcdefghijklmnop"
    # The space before the cut point is stripped before the ellipsis.
    assert leafbox.cut("abcdefghijklmn pq", 16) == "abcdefghijklmn…"


def test_wrap_literals():
    title = "The quick brown fox jumps over the lazy…"  # a 40-character cut title
    assert leafbox.wrap(title, 25, 2) == ["The quick brown fox jumps", "over the lazy…"]
    assert leafbox.wrap("abcdefghijklmnopqrstuvwxyzABCD xy", 25, 2) == ["abcdefghijklmnopqrstuvwxy", "zABCD xy"]
    assert leafbox.wrap("alpha beta gamma delta epsilon zeta eta theta iota kappa", 25, 2) == [
        "alpha beta gamma delta", "epsilon zeta eta theta…"]


def test_box_texts():
    b = leafbox.box("Issue-12345678901234", "urgent!",
                    "The quick brown fox jumps over the lazy dog again and again", None)
    assert b.key == "Issue-123456789…"
    assert b.priority == "urgen…"
    assert b.title_lines == ["The quick brown fox jumps", "over the lazy…"]
    assert b.band is None
    assert leafbox.box("k", None, "t", None).priority is None
    assert leafbox.box("k", "", "t", None).priority == ""


def test_band():
    assert leafbox.box("k", None, "t", ["opus", None]).band == (("think opus", "m-opus"), ("check none", "m-other"))
    assert leafbox.box("k", None, "t", None).band is None
    assert leafbox.box("k", None, "t", ["sonnet-4-5", "inherit"]).band == (
        ("think sonnet-4…", "m-other"), ("check inherit", "m-other"))


def test_box_classes_order():
    assert leafbox.box_classes("crit", "st-wip", True, True, ("compact",)) == [
        "crit", "st-wip", "compact", "unest", "xsite"]
    assert leafbox.box_classes(None, "st-pending", False, False) == ["st-pending"]


def test_model_tiers():
    assert leafbox.model_tiers(None) is None
    assert leafbox.model_tiers(Models("", None, "estimates_file")) is None
    assert leafbox.model_tiers(Models("opus", None, "estimates_file")) == ["opus", None]
    with pytest.raises(TypeError, match="not Models"):
        leafbox.model_tiers(("opus", None))


# --- the shared cue colours (#170) ---------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "gantry" / "templates"
SHEET = "templates/leaf_colours.css"
CUE_TOKENS = ["--crit", "--crit-ink", "--near", "--near-ink", "--held", "--xsite",
              *(f"--m-{m}{ink}" for m in ("haiku", "sonnet", "opus", "fable", "other") for ink in ("", "-ink"))]
BASE_TOKENS = ["--sheet", "--ink", "--muted", "--rule"]
DARK = "@media (prefers-color-scheme: dark)"


def schemes(css):
    """{"light": [(name, value)], "dark": [...]}: the :root custom properties, each declaration as written."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    light, dark, i = [], [], 0
    while (at := css.find(DARK, i)) != -1:
        light.append(css[i:at])
        start = css.index("{", at) + 1
        depth, j = 1, start
        while depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        dark.append(css[start:j - 1])
        i = j
    light.append(css[i:])

    def props(parts):
        return [(m[1], m[2].strip()) for part in parts for body in re.findall(r":root\s*\{([^}]*)\}", part)
                for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", body)]
    return {"light": props(light), "dark": props(dark)}


def page_style(page):
    (style,) = re.findall(r"<style>(.*?)</style>", page, flags=re.S)
    return style


def test_leaf_colours_shared():
    assert len(CUE_TOKENS) == 16
    sheet = (ROOT / "gantry" / SHEET).read_text(encoding="utf-8")
    for scheme, found in schemes(sheet).items():
        assert sorted(name for name, _ in found) == sorted(CUE_TOKENS), scheme
    for name in ("worksite.html", "week.html"):
        declared = {n for found in schemes((TEMPLATES / name).read_text(encoding="utf-8")).values() for n, _ in found}
        assert declared.isdisjoint(CUE_TOKENS), name
    worksite_page = page_for()
    week_page = render_week(make_plan(), make_result(), STAMP)
    for page in (worksite_page, week_page):
        assert page_style(page).count(sheet.strip()) == 1


def test_box_base_tokens_agree():
    found = {name: {scheme: dict(props) for scheme, props in schemes((TEMPLATES / name).read_text(encoding="utf-8")).items()}
             for name in ("worksite.html", "week.html")}
    for scheme in ("light", "dark"):
        for token in BASE_TOKENS:
            assert found["worksite.html"][scheme][token] == found["week.html"][scheme][token], (scheme, token)


def test_colours_refuses_style_breakout(monkeypatch):
    real = render.asset
    monkeypatch.setattr(render, "asset", lambda name: "a { } </STYLE> b" if name == SHEET else real(name))
    with pytest.raises(ValueError, match="must not hold </style"):
        leafbox.colours()
