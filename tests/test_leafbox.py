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
from test_week_page import STAMP, TEMPLATES, make_plan, make_result, media_split
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
SHEET = "templates/leaf_colours.css"
CUE_TOKENS = ["--crit", "--crit-ink", "--near", "--near-ink", "--held", "--xsite", "--done",
              *(f"--m-{m}{ink}" for m in ("haiku", "sonnet", "opus", "fable", "other") for ink in ("", "-ink"))]
BASE_TOKENS = ["--sheet", "--ink", "--muted", "--rule"]
DARK = "@media (prefers-color-scheme: dark)"


def schemes(css):
    """{"light": [(name, value)], "dark": [...]}: the :root custom properties, each declaration as written."""
    light, dark = media_split(css, DARK)

    def props(parts):
        return [(m[1], m[2].strip()) for part in parts for body in re.findall(r":root\s*\{([^}]*)\}", part)
                for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", body)]
    return {"light": props(light), "dark": props(dark)}


def page_style(page):
    (style,) = re.findall(r"<style>(.*?)</style>", page, flags=re.S)
    return style


def test_leaf_colours_shared():
    assert len(CUE_TOKENS) == 17
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


# --- the workflow phase row (#173) ------------------------------------------------------

from gantry.phases import PhaseRow  # noqa: E402
from page_safety import HOSTILE, page_findings  # noqa: E402

PHASE_SHEET = "templates/leaf_phases.css"


def cell(x, look, mark):
    return f'<g class="ph-cell ph-{look}"><rect class="ph-box" x="{x}" y="3" width="10" height="10" rx="1.5"/>{mark}</g>'


def test_phase_row_marks():
    looks = ["done", "current", "stuck", "parked", "blocked"]
    assert "".join(leafbox.phase_row(looks, 4, 3)) == "".join([
        '<g class="ph-row">',
        cell(4, "done", '<path class="ph-mark" d="M6.2,8.2 L8.3,10.4 L11.8,5.8"/>'),
        cell(17, "current", '<circle class="ph-mark" cx="22" cy="8" r="2.6"/>'),
        cell(30, "stuck", '<text class="ph-t" x="35" y="11.3" text-anchor="middle">!</text>'),
        cell(43, "parked", '<text class="ph-t" x="48" y="11.3" text-anchor="middle">P</text>'),
        cell(56, "blocked", '<text class="ph-t" x="61" y="11.3" text-anchor="middle">?</text>'),
        "</g>",
    ])
    assert "".join(leafbox.phase_row(["ahead", "none", "unknown", "ahead", "ahead"], 4, 3)) == "".join([
        '<g class="ph-row">',
        cell(4, "ahead", ""),
        cell(17, "none", '<line class="ph-mark" x1="19.5" y1="8" x2="24.5" y2="8"/>'),
        cell(30, "unknown", '<path class="ph-mark" d="M32,5 L38,11 M38,5 L32,11"/>'),
        cell(43, "ahead", ""),
        cell(56, "ahead", ""),
        "</g>",
    ])
    assert (leafbox.PH_CELL, leafbox.PH_GAP, leafbox.PH_WIDTH) == (10, 3, 62)
    for bad in (["done"] * 4, [HOSTILE[3]] * 5, ["done"] * 6, "ahead"):
        with pytest.raises(ValueError):
            leafbox.phase_row(bad, 0, 0)


def test_phase_row_carries_no_box_class():
    markup = "".join(leafbox.phase_row(["done", "current", "none", "unknown", "stuck"], 0, 0))
    classes = {c for attr in re.findall(r'class="([^"]*)"', markup) for c in attr.split()}
    assert classes.isdisjoint({"box", "outline", "dot", "nid", "nmeta", "ntitle", "band"})


def test_phase_text_literals():
    D, C, A, N = "done", "current", "ahead", "none"
    assert leafbox.phase_text(PhaseRow((D, C, A, A, A), None)) == \
        "phases: plan done; implement current; check, merge, wrap up ahead"
    assert leafbox.phase_text(PhaseRow((C, A, A, N, A), None)) == \
        "phases: plan current; implement, check ahead; merge not in this workflow; wrap up ahead"
    assert leafbox.phase_text(PhaseRow(("unknown",) * 5, "checklist.md is a link")) == \
        "phases unknown: checklist.md is a link"
    assert leafbox.phase_text(PhaseRow(("blocked", "stuck", "parked", D, D), None)) == \
        "phases: plan blocked outside; implement stuck; check parked; merge, wrap up done"
    with pytest.raises(ValueError):
        leafbox.phase_text(PhaseRow((D, "unknown", A, A, A), None))


def test_phase_svg_escapes():
    svg = leafbox.phase_svg(PhaseRow(("unknown",) * 5, HOSTILE[3]))
    assert svg.startswith('<svg class="ph-svg" viewBox="0 0 62 10" width="62" height="10" role="img" aria-label="')
    assert HOSTILE[3] not in svg and "&quot;&gt;&lt;svg onload=gantrypwn(4)&gt;" in svg
    assert svg.count("<svg") == 1
    svg = leafbox.phase_svg(PhaseRow(("unknown",) * 5, HOSTILE[10]))
    assert HOSTILE[10] not in svg and svg.count("</title>") == 1
    assert "<title>phases unknown: &lt;/title&gt;" in svg
    for why in (HOSTILE[3], HOSTILE[10]):
        page = f"<p>{leafbox.phase_svg(PhaseRow(('unknown',) * 5, why))}</p>"
        assert page_findings(page) == []
    fresh = leafbox.phase_svg(PhaseRow(("current", "ahead", "ahead", "ahead", "ahead"), None))
    assert '<title>phases: plan current; implement, check, merge, wrap up ahead</title><g class="ph-row">' in fresh


def test_phase_swatch():
    assert leafbox.phase_swatch("stuck") == (
        '<svg class="ph-svg" viewBox="0 0 10 10" width="10" height="10" aria-hidden="true"><g class="ph-row">'
        '<g class="ph-cell ph-stuck"><rect class="ph-box" x="0" y="0" width="10" height="10" rx="1.5"/>'
        '<text class="ph-t" x="5" y="8.3" text-anchor="middle">!</text></g></g></svg>')
    with pytest.raises(ValueError):
        leafbox.phase_swatch("bogus")


def test_phase_constants():
    assert leafbox.PHASE_LEGEND == ("Workflow phase row, left to right: plan, implement, check, merge, wrap up, "
                                    "from each issue's checklist.md")
    assert leafbox.PHASE_LIMIT == ("The row shows this build machine's copy of each checklist, which may lag an "
                                   "issue running on another machine.")


def test_phases_css_selectors_and_no_animation():
    sheet = (ROOT / "gantry" / PHASE_SHEET).read_text(encoding="utf-8")
    assert str(leafbox.phases_css()) == sheet
    body = re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)
    selectors = [s.strip() for block in re.findall(r"([^{}]+)\{", body) for s in block.split(",")]
    assert selectors and all(s.startswith(".ph-row") or s == ".ph-svg" for s in selectors), selectors
    assert "animation" not in body and "@keyframes" not in body and "blink" not in body


def test_phases_css_refuses_style_breakout(monkeypatch):
    real = render.asset
    monkeypatch.setattr(render, "asset", lambda name: "a { } </STYLE> b" if name == PHASE_SHEET else real(name))
    with pytest.raises(ValueError, match="must not hold </style"):
        leafbox.phases_css()


def test_num_is_worksite_n():
    from gantry.views import worksite

    assert worksite._n is leafbox.num
    assert [leafbox.num(v) for v in (3.0, 2.25, 8.3, 10)] == ["3", "2.2", "8.3", "10"]
