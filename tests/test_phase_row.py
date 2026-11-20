"""The workflow phase row on the worksite page (#173): the row, its tooltip,
legend, embedded data, geometry and CSS precedence.

Every expected row and text is a hand-written literal, never computed
through gantry.phases or leafbox.
"""

import html
import json
import re

import pytest

import helpers_phases
import helpers_portfolio as hp
from gantry import devagent_records as dr
from gantry.views import leafbox, worksite
from page_safety import HOSTILE, assert_no_external_resources, assert_page_safe
from test_worksite_page import STRUCTURAL, ancestors, find, text_of, tree

FRESH = ("## Revision 1\n\n- [ ]  0. pull\n- [-]  1. research\n- [ ]  2. draft\n- [ ]  8. branch\n"
         "- [ ]  9. implement\n- [ ] 10. quality\n- [ ] 20. mergemr\n- [ ] 24. cleanup\n")
IMPLEMENTING = FRESH.replace("[ ]  0.", "[x]  0.").replace("[ ]  2.", "[x]  2.").replace("[ ]  8.", "[x]  8.")
FRESH_TEXT = "phases: plan current; implement, check, merge, wrap up ahead"
IMPLEMENTING_TEXT = "phases: plan done; implement current; check, merge, wrap up ahead"
ABSENT = dr.Record(dr.ABSENT, None, None)


def folder(issue, checklist=None, plan=False):
    record = ABSENT if checklist is None else dr.Record(dr.PRESENT, dr.read_checklist(checklist), None)
    plan_record = dr.Record(dr.PRESENT, dr.read_plan("## Tasks\n- [ ] Task 1\n"), None) if plan else ABSENT
    return dr.IssueRecords(issue, ABSENT, record, plan_record)


def records(**folders):
    return dr.SiteRecords(dr.READ, None, dict(sorted(folders.items())), {})


def chain_site(*issues):
    """A chain of P1 leaves, each drawn as a full box."""
    nodes = [hp.node(f"Leaf {issue}", issue=issue, priority="P1",
                     **({"depends_on": [issues[i - 1]]} if i else {})) for i, issue in enumerate(issues)]
    return hp.site(*nodes)


def render(data, recs):
    m = worksite.page_model("alpha", data, default_unit_hours=6, records=recs)
    return worksite.render_model(m, stamp=""), m


def nodes(t):
    return {t.elements[i]["attrs"]["data-item"]: i for i in find(t, "g", ["node"])}


def full_form(t, node):
    (i,) = find(t, "g", ["full-form"], within=node)
    return i


def own_title(t, i):
    """The text of element i's first child when it is a <title>, else None."""
    kids = [j for j, el in enumerate(t.elements) if el["parent"] == i]
    return text_of(t, kids[0]) if kids and t.elements[kids[0]]["tag"] == "title" else None


def cells(t, within):
    return [[c for c in t.elements[i]["classes"] if c.startswith("ph-") and c != "ph-cell"][0]
            for i in find(t, "g", ["ph-cell"], within=within)]


def test_row_drawn_per_leaf():
    data = chain_site("Issue-1", "Issue-2", "Issue-3")
    page, _ = render(data, records(**{"Issue-1": folder("Issue-1", FRESH), "Issue-3": folder("Issue-3", plan=True)}))
    t = tree(page)
    found = nodes(t)
    assert sorted(found) == ["Issue-1", "Issue-2", "Issue-3"]
    rows = find(t, "g", ["ph-row"], within=found["Issue-1"])
    assert len(rows) == 1 and full_form(t, found["Issue-1"]) in ancestors(t, rows[0])
    assert cells(t, rows[0]) == ["ph-current", "ph-ahead", "ph-ahead", "ph-ahead", "ph-ahead"]
    assert find(t, "g", ["ph-row"], within=found["Issue-2"]) == []
    assert find(t, "g", ["ph-row"], within=found["Issue-3"]) == []


def test_tooltip_states_row():
    data = chain_site("Issue-1", "Issue-2", "Issue-3")
    unread = dr.Record(dr.UNREADABLE, None, "checklist.md is a link")
    recs = records(**{"Issue-1": folder("Issue-1", IMPLEMENTING),
                      "Issue-2": dr.IssueRecords("Issue-2", ABSENT, unread, ABSENT)})
    t = tree(render(data, recs)[0])
    found = nodes(t)
    tips = {key: own_title(t, full_form(t, i)) for key, i in found.items()}
    assert tips["Issue-1"].endswith(" · " + IMPLEMENTING_TEXT)
    assert tips["Issue-1"].startswith(own_title(t, found["Issue-1"]))
    assert tips["Issue-2"].endswith(" · phases unknown: checklist.md is a link")
    assert tips["Issue-3"] is None
    for i in found.values():
        assert "phases" not in own_title(t, i)


def test_phase_legend_not_hidden():
    t = tree(render(chain_site("Issue-1"), records())[0])
    (legend,) = [i for i in find(t, "div", ["legend"]) if leafbox.PHASE_LEGEND in text_of(t, i)]
    assert not any("aria-hidden" in t.elements[a]["attrs"] for a in [legend, *ancestors(t, legend)])
    text = text_of(t, legend)
    for word in ("done", "current", "stuck", "parked", "blocked outside", "ahead", "not in this workflow",
                 "unknown"):
        assert word in text, word
    assert leafbox.PHASE_LIMIT in text
    assert len(find(t, "g", ["ph-row"], within=legend)) == 8


def test_phase_legend_drawn_without_rows():
    page = worksite.render_page("alpha", chain_site("Issue-1"), default_unit_hours=6)
    assert html.escape(leafbox.PHASE_LEGEND) in page and html.escape(leafbox.PHASE_LIMIT) in page


def embedded(page):
    (data,) = re.findall(r'<script type="application/json" id="gantry-data">(.*?)</script>', page, flags=re.S)
    return json.loads(data)


def test_embedded_data_only_look_names():
    data = chain_site("Issue-1", "Issue-2", "Issue-3")
    unread = dr.Record(dr.UNREADABLE, None, "checklist.md is a link")
    recs = records(**{"Issue-1": folder("Issue-1", IMPLEMENTING),
                      "Issue-2": dr.IssueRecords("Issue-2", ABSENT, unread, ABSENT)})
    page, m = render(data, recs)
    assert m["leaves"][1]["phases_why"] == "checklist.md is a link"
    leaves = embedded(page)["leaves"]
    assert [leaf["phases"] for leaf in leaves] == [["done", "current", "ahead", "ahead", "ahead"], ["unknown"] * 5,
                                                   None]
    assert all(not key.startswith("phases_") for leaf in leaves for key in leaf)
    assert "checklist.md is a link" not in json.dumps(embedded(page))


# --- geometry ---------------------------------------------------------------------------

ASCENT, DESCENT, MONO = 0.8, 0.25, 0.6
FONT = {"nid": 12, "nmeta": 10, "ntitle": 10.5, "band-t": 9.5, "chip-t": 8, "chip-more": 8}
# The top line's key and its priority and hours overlap already at 63de8e0, when the key
# and priority are long; #173 leaves the top line alone, and the overlap is a follow-up.
PRE_EXISTING = {("nid", "nmeta")}


def worst_case_svg():
    key = "Issue-1234567890"
    nodes_ = [hp.node("The quick brown fox jumps over the lazy dog", issue=key, priority="urgent!",
                      depends_on=["Issue-2"]), hp.node("B", issue="Issue-2")]
    recs = records(**{key: folder(key, IMPLEMENTING)})
    m = worksite.page_model("alpha", hp.site(*nodes_), default_unit_hours=6, records=recs)
    (leaf,) = [leaf for leaf in m["leaves"] if leaf["key"] == key]
    leaf["models"], leaf["mc"], leaf["wave_full"] = ["opus", "sonnet"], ["m-opus", "m-sonnet"], True
    m["trace"] = {"leaves": {key: [("BR-001", "BR-001"), ("BR-002", "BR-002"), ("BR-003", "BR-003")]}}
    return key, worksite._network_svg(m)


def num(el, name):
    return float(el["attrs"][name])


def text_box(el, text):
    cls = next(c for c in el["classes"] if c in FONT)
    size, x, y = FONT[cls], num(el, "x"), num(el, "y")
    w = len(text) * MONO * size
    anchor = el["attrs"].get("text-anchor")
    left = x - w if anchor == "end" else x - w / 2 if anchor == "middle" else x
    return cls, (left, y - ASCENT * size, left + w, y + DESCENT * size)


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def test_box_geometry_no_overlap():
    key, svg = worst_case_svg()
    t = tree(svg)
    node = nodes(t)[key]
    (box,) = [i for i in find(t, "rect", ["box"], within=node)]
    bx, by = num(t.elements[box], "x"), num(t.elements[box], "y")
    assert (worksite.BW, worksite.BH, worksite.ROWH) == (152, 78, 92)
    texts = [text_box(t.elements[i], text_of(t, i)) for i in find(t, "text", within=node)
             if not set(t.elements[i]["classes"]) & {"ph-t"}]
    by_class = {}
    for cls, b in texts:
        by_class.setdefault(cls, []).append(b)
    assert len(by_class["ntitle"]) == 2 and len(by_class["band-t"]) == 2 and len(by_class["chip-t"]) == 3
    (row,) = find(t, "g", ["ph-row"], within=node)
    rects = [t.elements[i] for i in find(t, "rect", ["ph-box"], within=row)]
    assert len(rects) == 5
    row_box = (min(num(r, "x") for r in rects), min(num(r, "y") for r in rects),
               max(num(r, "x") + 10 for r in rects), max(num(r, "y") + 10 for r in rects))
    assert row_box == (bx + 8, by + 48, bx + 70, by + 58) and row_box[2] <= bx + worksite.BW - 8
    bands = [t.elements[i] for i in find(t, "rect", ["band"], within=node)]
    band_top = min(num(b, "y") for b in bands)
    chips = [t.elements[i] for i in find(t, "rect", ["chip-box"], within=node)]
    top_line = by_class["nid"] + by_class["nmeta"]
    layers = [
        (min(b[1] for b in top_line), max(b[3] for b in top_line)),
        (by_class["ntitle"][0][1], by_class["ntitle"][0][3]),
        (by_class["ntitle"][1][1], by_class["ntitle"][1][3]),
        (row_box[1], row_box[3]),
        (band_top, by + worksite.BH),
        (min(num(c, "y") for c in chips), max(num(c, "y") + num(c, "height") for c in chips)),
    ]
    for upper, lower in zip(layers, layers[1:]):
        assert upper[1] <= lower[0], (upper, lower)
    assert layers[-1][1] <= by + worksite.ROWH
    for cls, b in texts:
        assert not overlaps(row_box, b), cls
    for b in by_class["band-t"]:
        assert band_top <= b[1] and b[3] <= by + worksite.BH
    for i, (ca, a) in enumerate(texts):
        for cb, b in texts[i + 1:]:
            if overlaps(a, b):
                assert (ca, cb) in PRE_EXISTING or (cb, ca) in PRE_EXISTING, (ca, cb)
    # The exclusion is not stale: the worst-case top line still overlaps.
    assert overlaps(by_class["nid"][0], by_class["nmeta"][0])


# --- CSS precedence ---------------------------------------------------------------------

def specificity(selector):
    """(ids, classes, types) of one selector."""
    s = re.sub(r":not\(([^)]*)\)", r" \1", selector)
    ids = len(re.findall(r"#[\w-]+", s))
    classes = len(re.findall(r"\.[\w-]+", s)) + len(re.findall(r":[\w-]+", s))
    types = len(re.findall(r"(?:^|[\s>+~])([a-z][\w-]*)", s))
    return ids, classes, types


def style_rules(page):
    (style,) = re.findall(r"<style>(.*?)</style>", page, flags=re.S)
    style = re.sub(r"/\*.*?\*/", "", style, flags=re.S)
    found = []
    for selectors, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", style):
        for selector in selectors.split(","):
            found.append((selector.strip(), body))
    return found


def sets_paint(body):
    return re.search(r"(?:^|[;\s])(fill|stroke)\s*:", body) is not None


def test_phase_rules_win():
    page, _ = render(chain_site("Issue-1"), records(**{"Issue-1": folder("Issue-1", FRESH)}))
    rules = style_rules(page)
    phase = [(s, specificity(s)) for s, body in rules if ".ph-" in s and sets_paint(body)]
    assert len(phase) >= 6
    bare = [(s, specificity(s)) for s, body in rules if s.startswith(".node")
            and re.search(r"(?:^|\s)(text|rect|circle|path|line)$", s) and sets_paint(body)]
    assert ".node text" in [s for s, _ in bare]
    for s, spec in phase:
        assert all(spec > other for _, other in bare), s
    selectors = [s for s, _ in rules]
    subjects = {".node.crit .box": "box", ".node.near .box": "box", ".node.crit text.nid": "nid",
                ".node.xsite .outline": "outline", ".dot": "dot"}
    t = tree(page)
    row_classes = {c for r in find(t, "g", ["ph-row"]) for i in [r, *find(t, within=r)]
                   for c in t.elements[i]["classes"]}
    for selector, subject in subjects.items():
        assert selector in selectors, selector
        assert subject not in row_classes, subject


def test_specificity_counter():
    assert specificity(".node text") == (0, 1, 1)
    assert specificity(".ph-row text.ph-t") == (0, 2, 1)
    assert specificity(".node.crit text.nid") == (0, 3, 1)
    assert specificity("#a .b") == (1, 1, 0)


def test_page_classes_are_structural():
    page = helpers_phases.fixture_page()
    used = {c for attr in re.findall(r'class="([^"]*)"', page) for c in attr.split() if c.startswith("ph-")}
    assert used and used <= STRUCTURAL


# --- safety -----------------------------------------------------------------------------

@pytest.mark.parametrize("hostile", HOSTILE)
def test_hostile_titles_with_rows(hostile):
    nodes_ = [hp.node(hostile, issue="Issue-1", priority="P1", depends_on=["Issue-2"]),
              hp.node("B", issue="Issue-2", priority="P1")]
    recs = records(**{"Issue-1": folder("Issue-1", IMPLEMENTING), "Issue-2": folder("Issue-2", FRESH)})
    page, _ = render(hp.site(*nodes_), recs)
    assert page.count('class="ph-row"') >= 2
    assert_page_safe(page, [hostile])
    assert_no_external_resources(page)


def test_checklist_text_never_reaches_page():
    planted = "".join(f"# {h}\n" for h in HOSTILE)
    text = planted + IMPLEMENTING + "\n## Log\n" + "".join(f"- {h}\n" for h in HOSTILE)
    page, _ = render(chain_site("Issue-1", "Issue-2"), records(**{"Issue-1": folder("Issue-1", text)}))
    assert page.count('class="ph-row"') == 1 + 8  # the row and the legend swatches
    for hostile in HOSTILE:
        if hostile in ("__proto__", "{{DATA}}"):
            continue  # plain words the page may hold for its own reasons; checked as planted below
        assert hostile not in page
    assert_page_safe(page, [])


def test_fixture_page_every_look_every_fill():
    page = helpers_phases.fixture_page()
    t = tree(page)
    seen = set()
    for i in find(t, "g", ["node"]):
        fill = next(c for c in t.elements[i]["classes"] if c in ("crit", "near", "free"))
        for look in cells(t, i):
            seen.add((fill, look.removeprefix("ph-")))
    looks = ("done", "current", "stuck", "parked", "blocked", "ahead", "none", "unknown")
    assert seen == {(fill, look) for fill in ("crit", "near", "free") for look in looks}
    assert_page_safe(page, [])
    assert_no_external_resources(page)


# --- the samples (Task 8) ------------------------------------------------------------------

from helpers_config import demo_build, sample_build  # noqa: E402
from test_samples import ROOT  # noqa: E402

D, C, S, P, B, A, U = "done", "current", "stuck", "parked", "blocked", "ahead", "unknown"
# Every drawn sample leaf that has a row (network node or foreign-only leaf): its looks.
SAMPLE_ROWS = {
    ("harbor", "Issue-6"): [C, A, A, A, A],
    ("harbor", "Issue-7"): [C, A, A, A, A],
    ("harbor", "Issue-12"): [P, A, A, A, A],
    ("harbor", "Issue-15"): [C, A, A, A, A],
    ("harbor", "Issue-16"): [D, D, S, A, A],
    ("quarry", "Issue-3"): [D, D, C, A, A],
    ("quarry", "Issue-5"): [D, C, A, A, A],
    ("quarry", "Issue-9"): [P, A, A, A, A],
    ("lighthouse", "Issue-9"): [D, D, D, B, A],
    ("lighthouse", "Issue-12"): [U] * 5,
}
# Each of those leaves' WBS.md state, unchanged by #173.
SAMPLE_STATES = {
    ("harbor", "Issue-6"): "in_progress", ("harbor", "Issue-7"): "pending", ("harbor", "Issue-12"): "pending",
    ("harbor", "Issue-15"): "pending", ("harbor", "Issue-16"): "pending", ("quarry", "Issue-3"): "pending",
    ("quarry", "Issue-5"): "in_progress", ("quarry", "Issue-9"): "pending", ("lighthouse", "Issue-9"): "pending",
    ("lighthouse", "Issue-12"): "pending",
}
# Drawn leaves with no row: quarry Issue-8 has no folder; quarry Issue-7 is the
# foreign-only control for harbor Issue-7, which has one.
SAMPLE_NO_ROW = {("quarry", "Issue-8"), ("quarry", "Issue-7")}


def drawn(m):
    return set(m["network"]["nodes"]) | set(m["network"]["foreign_only"])


def test_sample_rows(tmp_path):
    result = sample_build(tmp_path)
    found, states, no_row = {}, {}, set()
    for site, m in result.page_models.items():
        for leaf in m["leaves"]:
            if leaf["key"] not in drawn(m):
                continue
            if leaf["phases"] is not None:
                found[(site, leaf["key"])] = leaf["phases"]
                states[(site, leaf["key"])] = leaf["state"]
            else:
                no_row.add((site, leaf["key"]))
    assert found == SAMPLE_ROWS
    assert states == SAMPLE_STATES
    assert SAMPLE_NO_ROW <= no_row
    (leaf,) = [leaf for leaf in result.page_models["lighthouse"]["leaves"] if leaf["key"] == "Issue-12"]
    assert leaf["phases_why"] == "checklist.md has a step glyph devagent does not write"
    assert (ROOT / "samples" / "harbor" / "Issue-16" / "STUCK").is_file()
    # The written page draws the unknown row with its reason.
    page = result.pages["lighthouse"]
    assert "phases unknown: checklist.md has a step glyph devagent does not write" in page


def test_sample_week_rows(tmp_path):
    """Every sample week placement's row, as a literal (S2): harbor Issue-13 is
    schedule-only; harbor Issue-8 and Issue-14, schedule-only too, are not placed."""
    from gantry.views.week import render_week
    from test_week_page import leaf_lines, placed_nodes, week_row

    result = sample_build(tmp_path)
    fresh = ((C, A, A, A, A), "phases: plan current; implement, check, merge, wrap up ahead")
    want = {
        ("orchard", "Issue-4"): None,
        ("harbor", "1.5"): None,
        ("harbor", "Issue-13"): ((S, A, A, A, A), "phases: plan stuck; implement, check, merge, wrap up ahead"),
        ("quarry", "Issue-3"): ((D, D, C, A, A), "phases: plan, implement done; check current; merge, wrap up ahead"),
        ("harbor", "Issue-6"): fresh,
        ("quarry", "Issue-5"): ((D, C, A, A, A), "phases: plan done; implement current; check, merge, wrap up ahead"),
        ("harbor", "Issue-12"): ((P, A, A, A, A), "phases: plan parked; implement, check, merge, wrap up ahead"),
        ("harbor", "Issue-7"): fresh,
    }
    assert sorted(placed_nodes(result.plan)) == sorted(want)
    assert "Issue-13" not in result.page_models["harbor"]["network"]["nodes"]
    t = tree(render_week(result.plan, result, result.stamp))
    for (site, key), row in want.items():
        boxes = [i for i in leaf_lines(t, key) if t.elements[i]["attrs"].get("data-site") == site]
        assert boxes, key
        assert {week_row(t, i) for i in boxes} == {row}, (site, key)


def page_rows(page):
    """{node key: (cell looks, full-form tooltip's phase text)} of a worksite page."""
    t = tree(page)
    rows = {}
    for key, node in nodes(t).items():
        full = full_form(t, node)
        found = find(t, "g", ["ph-row"], within=full)
        if found:
            rows[key] = (tuple(cells(t, found[0])), own_title(t, full).split(" · ")[-1])
    return rows


def test_site_build_rows_equal_full_build(tmp_path, monkeypatch):
    code, out, _, err = demo_build(tmp_path / "full", monkeypatch)
    assert code == 0, err
    compared = {}
    for site in ("harbor", "quarry", "lighthouse"):
        full = page_rows((out / "sites" / f"{site}.html").read_bytes().decode("utf-8"))
        code, one, _, err = demo_build(tmp_path / site, monkeypatch, "--site", site)
        assert code == 0, err
        alone = page_rows((one / "sites" / f"{site}.html").read_bytes().decode("utf-8"))
        both = set(full) & set(alone)
        assert both and {k: full[k] for k in both} == {k: alone[k] for k in both}, site
        compared[site] = both
    assert all(compared.values())
    assert "Issue-12" in compared["lighthouse"]  # the unknown row
