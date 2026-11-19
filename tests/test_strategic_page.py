"""The strategic page (#23): its groups and panels, figures and sources, the
report panel, page safety and repeatability. The model's values are in
tests/test_strategic.py."""

import math
import subprocess
import sys

from commit_check import path_form_hits
from gantry.build import build_all, write_all
from gantry.config import load_config
from gantry.portfolio_file import format_entry
from gantry.views.strategic import pct
from helpers_config import append_bytes, replace_bytes, sample_projects, toml_key, write_site_tree
from helpers_strategic import page_data, placement_line, placement_problems
from page_safety import HOSTILE, assert_no_external_resources, assert_page_safe
from sample_roles import BUSY, NO_WBS, OWN_UNIT, SMALL, UNESTIMATED, UNREADABLE
from test_index_page import data_strings
from test_samples import ROOT
from test_strategic import ESTIMATES_ROW, samples, with_estimates_row
from test_worksite_page import by_id, find, text_of, tree

BR_TEXTS = ["Keep the harbor safe to work in, all year round.", "Keep the market supplied every week.",
            "Bring in a full harvest each autumn."]
NO_WBS_REASON = "no WBS.md in the planning directory"
UNREADABLE_REASON = "WBS.md is not valid UTF-8 at byte 88"
SECTION_LABELS = {None: "the portfolio file", "business requirements": "business requirements",
                  "settings": "settings"}


def page(copy):
    """(the written strategic.html's text, the BuildResult write_all returned) for a samples copy."""
    result, _, error = write_all(build_all(load_config(copy / "gantry.sample.toml"), samples=copy))
    assert error is None and result.strategic_error is None
    return (copy / "out" / "strategic.html").read_bytes().decode("utf-8"), result


def demo(tmp_path):
    return page(samples(tmp_path))


def figures(t, kind, within=None):
    return [i for i in find(t, "span", within=within) if t.elements[i]["attrs"].get("data-figure") == kind]


def panels(t, within=None):
    """[(shown site name, li index)] for every panel, in page order."""
    return [(text_of(t, find(t, None, ("name",), within=li)[0]), li) for li in find(t, "li", ("panel",), within)]


def panel_of(t, site):
    """The index of the site's first panel."""
    return next(li for name, li in panels(t) if name == site)


def sections(t):
    return find(t, "section", ("group",))


# --- groups and placement (AC1, AC11) --------------------------------------------------


def test_demo_groups(tmp_path):
    text, _ = demo(tmp_path)
    data = page_data(text)
    groups = [(g["kind"], g["id"], g["sites"]) for g in data["groups"]]
    assert groups == [("br", "life:BR-1", [BUSY]), ("br", "life:BR-2", [BUSY]), ("br", "life:BR-3", [OWN_UNIT]),
                      ("no_requirement", None, [UNESTIMATED]), ("no_share", None, [SMALL]),
                      ("not_built", None, [NO_WBS, UNREADABLE])]
    assert [g["text"] for g in data["groups"][:3]] == BR_TEXTS
    assert all(set(g) == {"kind", "id", "text", "sites"} for g in data["groups"])  # no threshold, no objective
    reasons = {p["site"]: p["reason"] for p in data["panels"]}
    assert (reasons[NO_WBS], reasons[UNREADABLE]) == (NO_WBS_REASON, UNREADABLE_REASON)
    # The page shows what the data says.
    t = tree(text)
    found = sections(t)
    assert [t.elements[s]["classes"] for s in found] == [["group", "g-br"]] * 3 + [
        ["group", "g-no-requirement"], ["group", "g-no-share"], ["group", "g-not-built"]]
    headings = [text_of(t, find(t, "h2", within=s)[0]) for s in found]
    for heading, (rid, br_text) in zip(headings, zip(["life:BR-1", "life:BR-2", "life:BR-3"], BR_TEXTS)):
        assert heading.startswith(f"{rid}: {br_text} ")
    assert [h.split(" ", 4)[:4] for h in headings[3:]] == [
        ["Serves", "no", "listed", "requirement"], ["No", "intended", "share", "set"], ["Not", "built", "nothing", "to"]]
    assert [[name for name, _ in panels(t, s)] for s in found] == [g[2] for g in groups]
    not_built = found[-1]
    assert text_of(t, find(t, "span", ("reason",), within=panel_of(t, NO_WBS))[0]) == f"not built: {NO_WBS_REASON}"
    assert f"not built: {UNREADABLE_REASON}" in text_of(t, not_built)
    assert placement_problems(data) == [] and len(data["projects"]) == 6


PLACED = {
    "projects": ["a", "b", "c"],
    "groups": [{"kind": "br", "id": "x:BR-1", "text": "t", "sites": ["a"]},
               {"kind": "br", "id": "x:BR-2", "text": "u", "sites": ["a"]},
               {"kind": "no_share", "id": None, "text": None, "sites": ["b"]},
               {"kind": "not_built", "id": None, "text": None, "sites": ["c"]}],
    "panels": [{"site": "a", "status": "built", "reason": None}, {"site": "b", "status": "built", "reason": None},
               {"site": "c", "status": "not_built", "reason": "no WBS.md in the planning directory"}],
}


def planted(change):
    import copy

    data = copy.deepcopy(PLACED)
    change(data)
    return data


def test_placement_problems_controls():
    assert placement_problems(PLACED) == []
    bad = {
        "br and other": lambda d: d["groups"][2]["sites"].append("a"),
        "two others": lambda d: d["groups"][3]["sites"].append("b"),
        "none": lambda d: d["groups"][2]["sites"].remove("b"),
        "no reason": lambda d: d["panels"][2].update(reason=""),
    }
    for name, change in bad.items():
        assert placement_problems(planted(change)) != [], name


def test_placement_projects_match_config(tmp_path):
    text, _ = demo(tmp_path)
    assert page_data(text)["projects"] == sample_projects()


ONE_LINER = ("import sys; sys.path.insert(0, 'tests'); from helpers_strategic import placement_line; "
             "print(placement_line(sys.argv[1]))")


def test_placement_line_subprocess(tmp_path):
    copy = samples(tmp_path)
    page(copy)
    path = copy / "out" / "strategic.html"
    ran = subprocess.run([sys.executable, "-c", ONE_LINER, str(path)], cwd=ROOT, capture_output=True, check=True)
    # splitlines: a Windows child writes CRLF on its text stdout.
    assert ran.stdout.decode("utf-8").splitlines() == ["6 configured projects, 6 placed"]
    assert placement_line(path) == "6 configured projects, 6 placed"


def test_requirement_placed_site_without_share_placed_once(tmp_path):
    """AC1 and AC11 on a fixture that could break them: harbor serves life:BR-1 and life:BR-2 and has no
    share. It is under those two headings only, and each of its panels says it has no intended share."""
    text, _ = page(samples(tmp_path, "orchard = 60, quarry = 40"))
    data = page_data(text)
    assert [(g["kind"], g["id"]) for g in data["groups"] if BUSY in g["sites"]] == [
        ("br", "life:BR-1"), ("br", "life:BR-2")]
    assert [g["sites"] for g in data["groups"] if g["kind"] == "no_share"] == [[SMALL]]
    assert placement_problems(data) == []
    t = tree(text)
    harbor = [li for name, li in panels(t) if name == BUSY]
    assert len(harbor) == 2
    for li in harbor:
        assert "no intended share set" in text_of(t, li)
        assert figures(t, "intended", li) == [] and figures(t, "gap", li) == []


# --- panels (AC2, AC6) -------------------------------------------------------------------


def test_not_built_with_share_and_serves(tmp_path):
    copy = samples(tmp_path, "harbor = 40, orchard = 30, quarry = 20, bakery = 10")
    replace_bytes(copy / "portfolio.md", 'serves = ["BR-3"]\n', 'serves = ["BR-3"]\n\n[site.bakery]\nserves = ["BR-1"]\n')
    text, _ = page(copy)
    data = page_data(text)
    assert [g["kind"] for g in data["groups"] if NO_WBS in g["sites"]] == ["not_built"]
    t = tree(text)
    assert [name for name, _ in panels(t)].count(NO_WBS) == 1
    li = panel_of(t, NO_WBS)
    body = text_of(t, li)
    for wanted in ("intended 10% (allocation from 2026-01-05)", "names life:BR-1 in serves",
                   "no page, so no open work counted"):
        assert wanted in body, wanted
    assert [len(figures(t, kind, li)) for kind in ("intended", "backlog", "gap", "hours")] == [1, 0, 0, 0]
    # The Not built group sums the share as given; it has no backlog share to add.
    (group_sum,) = figures(t, "group-sum", find(t, "section", ("g-not-built",))[0])
    assert text_of(t, group_sum) == "intended 10% as given"
    assert t.elements[group_sum]["attrs"]["data-source"] == "the portfolio file, allocation from 2026-01-05"


def test_intended_names_allocation_and_no_portfolio(tmp_path):
    text, _ = demo(tmp_path / "demo")
    t = tree(text)
    assert "intended 50% (allocation from 2026-01-05)" in text_of(t, panel_of(t, BUSY))
    copy = samples(tmp_path / "bare")
    replace_bytes(copy / "gantry.sample.toml", 'portfolio_file = "portfolio.md"\n', "")
    text, _ = page(copy)
    t = tree(text)
    assert text_of(t, by_id(t, "notes")) == "no portfolio file configured"
    found = panels(t)
    assert len(found) == 6
    assert all("no intended share set" in text_of(t, li) for _, li in found)
    assert figures(t, "intended") == [] and figures(t, "gap") == []
    # The gaps note is shown only when an allocation applies.
    assert text_of(t, by_id(t, "off-page")) == ""


# --- figures (AC7) -----------------------------------------------------------------------


def test_figures_found_by_kind(tmp_path):
    text, _ = demo(tmp_path)
    t = tree(text)
    counts = {kind: len(figures(t, kind)) for kind in ("hours", "backlog", "intended", "gap", "total", "group-sum",
                                                       "headline", "index-share")}
    assert counts == {"hours": 5, "backlog": 5, "intended": 4, "gap": 4, "total": 1, "group-sum": 6,
                      "headline": 0, "index-share": 0}
    for kind in counts:
        for i in figures(t, kind):
            attrs = t.elements[i]["attrs"]
            assert attrs["data-source"], (kind, i)
            source = t.elements[i + 1]
            assert source["tag"] == "span" and source["classes"] == ["source"], (kind, i)
            assert "".join(source["text"]) == attrs["data-source"]
            assert ("data-unestimated" in attrs) == (kind in ("hours", "backlog", "total", "group-sum")), (kind, i)
    (quarry_hours,) = figures(t, "hours", panel_of(t, UNESTIMATED))
    assert t.elements[quarry_hours]["attrs"]["data-unestimated"] == "5"
    assert text_of(t, by_id(t, "plans-only")) == "plans and estimates only"



def test_figure_sources_and_counts(tmp_path):
    """Each kind's data-source as docs/strategic-page.md tables it, the
    unestimated counts the total and each group sum carry, and the overlap note."""
    text, _ = demo(tmp_path)
    t = tree(text)
    allocation, computed = "the portfolio file, allocation from 2026-01-05", "computed from open work"
    for kind, wanted in (("intended", allocation), ("backlog", computed), ("gap", computed),
                         ("total", "each built site's WBS.md and estimates file")):
        assert {t.elements[i]["attrs"]["data-source"] for i in figures(t, kind)} == {wanted}, kind
    hours = {name: int(t.elements[figures(t, "hours", li)[0]]["attrs"]["data-unestimated"])
             for name, li in panels(t) if figures(t, "hours", li)}
    (total,) = figures(t, "total")
    assert t.elements[total]["attrs"]["data-unestimated"] == str(sum(hours.values())) == "12"
    sections = find(t, "section", ("group",))
    assert len(sections) == 6
    for section in sections:
        (group_sum,) = figures(t, "group-sum", find(t, "h2", within=section)[0])
        attrs, parts = t.elements[group_sum]["attrs"], text_of(t, group_sum)
        members = [name for name, _ in panels(t, section)]
        assert attrs["data-unestimated"] == str(sum(hours.get(name, 0) for name in members)), members
        joined = "; ".join(s for s, shown in ((allocation, "intended" in parts), (computed, "backlog share" in parts))
                           if shown)
        assert attrs["data-source"] == (joined or "no intended or backlog share in this group"), parts
    notes = [text_of(t, li) for li in find(t, "li", within=by_id(t, "notes"))]
    assert notes == ["Groups overlap when a site serves several requirements, so group sums do not add up to the total."]


# --- the report panel (AC8) ------------------------------------------------------------


def test_report_panel_lists_entries(tmp_path):
    copy = samples(tmp_path, "harbor = 45, orchard = 30, quarry = 20, ferry = 5")
    replace_bytes(copy / "portfolio.md", "- BR-3: Bring in a full harvest each autumn.\n",
                  "- BR-3: Bring in a full harvest each autumn.\n- BR-2: Keep the market supplied twice a week.\n")
    replace_bytes(copy / "portfolio.md", 'issue = "Issue-5"', 'issue = "Issue-99"')
    text, result = page(copy)
    entries = result.portfolio_entries
    assert len(entries) >= 3
    assert {"duplicate_id", "unknown_project", "milestone_no_leaf"} <= {e.kind for e in entries}
    t = tree(text)
    report = by_id(t, "report")
    lines = [text_of(t, li) for li in find(t, "li", within=report)]
    assert lines == [f"{SECTION_LABELS[e.section]}: {format_entry(e)}" for e in entries]
    assert text_of(t, find(t, "h2", within=report)[0]) == "missing or unreadable"
    data = page_data(text)
    strings = [text, *data_strings(data)]
    for s in strings:
        assert path_form_hits(s) == [], s[:80]
        assert str(tmp_path) not in s and tmp_path.as_posix() not in s


def test_report_panel_names_the_file(tmp_path):
    """An entry about the whole file (no section) is labelled "the portfolio file", never by its path."""
    copy = samples(tmp_path)
    (copy / "portfolio.md").unlink()
    text, result = page(copy)
    (entry,) = result.portfolio_entries
    assert entry.section is None and entry.kind == "portfolio_unreadable"
    t = tree(text)
    assert [text_of(t, li) for li in find(t, "li", within=by_id(t, "report"))] == [
        f"the portfolio file: {format_entry(entry)}"]
    assert [r["section"] for r in page_data(text)["report"]] == ["the portfolio file"]


def test_data_block_matches_model(tmp_path):
    """The gantry-data block is the page's machine-readable contract: each panel's entry carries the
    model's unrounded figures, and the totals, as-of date and report carry theirs."""
    from gantry.strategic import strategic_model

    copy = samples(tmp_path, "harbor = 35, orchard = 30, quarry = 20, bakery = 10, ferry = 5")
    replace_bytes(copy / "portfolio.md", 'issue = "Issue-5"', 'issue = "Issue-99"')
    text, result = page(copy)
    data, model = page_data(text), strategic_model(result)

    def same(a, b):
        return a == b if a is None or b is None else math.isclose(a, b)

    assert [p["site"] for p in data["panels"]] == list(model.panels)
    for entry in data["panels"]:
        p = model.panels[entry["site"]]
        b = p.backlog
        assert (entry["status"], entry["reason"], entry["open_leaves"], entry["held_out"], entry["estimates"],
                entry["intended"], entry["serves"]) == ("built" if p.built else "not_built", p.reason, p.open_leaves,
                                                       p.held_out, p.estimates, p.intended, list(p.serves))
        assert same(entry["comparable"], p.comparable) and same(entry["gap"], p.gap), entry["site"]
        assert same(entry["backlog_share"], b.share if b else None), entry["site"]
        if b is not None:
            assert (entry["unestimated_count"], entry["non_time"], entry["non_time_split"]) == (
                b.unestimated_count, b.non_time, b.non_time_split)
    # Literals, so a value the block and the model both lost would still fail.
    harbor = next(e for e in data["panels"] if e["site"] == BUSY)
    assert math.isclose(harbor["comparable"], 35 / 85 * 100)
    assert math.isclose(harbor["gap"], abs(130 / 424 * 100 - 35 / 85 * 100))
    assert harbor["serves"] == ["life:BR-1", "life:BR-2"] and harbor["held_out"] == 0
    assert (data["as_of"], data["portfolio"]) == ("2026-01-20", "configured")
    assert data["totals"]["not_built_share"] == 10 and data["totals"]["not_in_config_share"] == 5
    assert data["totals"]["unestimated_count"] == model.total_unestimated
    assert [(r["kind"], r["line"], r["key_path"], r["message"]) for r in data["report"]] == [
        (e.kind, e.line, e.key_path, e.message) for e in model.report]
    assert any(r["line"] is not None for r in data["report"])
    bare = samples(tmp_path / "bare")
    replace_bytes(bare / "gantry.sample.toml", 'portfolio_file = "portfolio.md"\n', "")
    assert page_data(page(bare)[0])["portfolio"] == "not configured"


def test_not_built_name_is_shown_escaped(tmp_path):
    """A not-built project's name is printed as on stderr: a tab in it is escaped (I4)."""
    copy = samples(tmp_path)
    append_bytes(copy / "devagent" / "config.toml", '\n[project."bad\\tname"]\ndevdoc_dir = "../ghost"\n')
    text, _ = page(copy)
    t = tree(text)
    assert "bad\\tname" in [name for name, _ in panels(t)]
    assert "bad\tname" in page_data(text)["projects"]


def test_report_panel_empty(tmp_path):
    text, _ = demo(tmp_path)
    t = tree(text)
    assert [text_of(t, li) for li in find(t, "li", within=by_id(t, "report"))] == ["nothing missing or unreadable"]


# --- page safety (AC9) -------------------------------------------------------------------

ODD_ESTIMATES = 'estimates-2026-01-15-"<b>odd.md'


def test_strategic_hostile(tmp_path):
    copy = samples(tmp_path, f"harbor = 40, orchard = 30, quarry = 20, {toml_key(HOSTILE[7])} = 5, "
                             f"{toml_key(HOSTILE[10])} = 5")
    replace_bytes(copy / "portfolio.md", "- BR-3: Bring in a full harvest each autumn.\n",
                  f"- BR-3: Bring in a full harvest each autumn.\n- BR-4: {HOSTILE[0]}\n")
    append_bytes(copy / "devagent" / "config.toml", f'\n[project.{toml_key(HOSTILE[3])}]\ndevdoc_dir = "../ghost"\n')
    planted_values = [HOSTILE[0], HOSTILE[3], HOSTILE[7], HOSTILE[10]]
    try:
        (copy / OWN_UNIT / ODD_ESTIMATES).write_bytes(ESTIMATES_ROW.encode("utf-8"))
        planted_values.append(ODD_ESTIMATES)
    except OSError:
        pass  # a file system that refuses the name: the attribute leg is skipped there (S6)
    text, result = page(copy)
    unknown = [e.message for e in result.portfolio_entries if e.kind == "unknown_project"]
    assert len(unknown) == 2
    data = assert_page_safe(text, planted_values + unknown)
    assert_no_external_resources(text)
    assert HOSTILE[3] in data["projects"]
    if ODD_ESTIMATES in planted_values:
        t = tree(text)
        (hours,) = figures(t, "hours", panel_of(t, OWN_UNIT))
        assert t.elements[hours]["attrs"]["data-source"] == f"WBS.md and {ODD_ESTIMATES}"


def test_no_page_script(tmp_path):
    text, _ = demo(tmp_path)
    assert text.count("<script") == 1
    assert '<script type="application/json" id="gantry-data">' in text


def test_strategic_repeatable(tmp_path):
    first, _ = demo(tmp_path / "one")
    second, _ = demo(tmp_path / "two")
    assert first.encode("utf-8") == second.encode("utf-8")
    t = tree(first)
    (stamp,) = find(t, "p", ("stamp",))
    assert text_of(t, stamp) == "sample data as of 2026-01-20"


# --- texts (B4, B5, B6) ------------------------------------------------------------------


def built_page(tmp_path, sites, tail=""):
    """The written strategic page of a write_site_tree installation: (tree, page text)."""
    result, _, error = write_all(build_all(load_config(write_site_tree(tmp_path, sites, tail=tail))))
    assert error is None and result.strategic_error is None
    text = (tmp_path / "out" / "strategic.html").read_bytes().decode("utf-8")
    return tree(text), text


def test_pct_half_up(tmp_path):
    assert (pct(12.5), pct(30.66), pct(0), pct(99.5)) == ("13%", "31%", "0%", "100%")
    t, _ = built_page(tmp_path, {"a": b"- [ ] Only {issue: Issue-1, est: 130.25h}\n"})
    (hours,) = figures(t, "hours")
    assert text_of(t, hours) == "1 open leaf, 130.3 h"


def test_non_time_texts(tmp_path):
    text, _ = demo(tmp_path / "demo")
    t = tree(text)
    wanted = ("also 13 pt in this site's own unit, left out of totals across sites; 2 leaves, 2 of them at the "
              "default unit (counted as unestimated)")
    assert wanted in [text_of(t, p) for p in find(t, "p", within=panel_of(t, OWN_UNIT))]
    text, _ = page(with_estimates_row(tmp_path / "row"))
    t = tree(text)
    lines = [text_of(t, p) for p in find(t, "p", within=panel_of(t, OWN_UNIT))]
    assert any(line.endswith("2 leaves, 1 of them at the default unit (counted as unestimated), "
                             "1 at estimates-file hours") for line in lines), lines


def test_rendered_texts(tmp_path):
    gaps_note = "Gaps compare each intended share rescaled over built sites."
    # The off-page line (AC4's fixture) and the gaps note.
    text, _ = page(samples(tmp_path / "off", "harbor = 35, orchard = 30, quarry = 20, bakery = 10, ferry = 5"))
    t = tree(text)
    assert text_of(t, by_id(t, "off-page")) == (
        "Intended share outside the gap comparison: 10% on projects not built and 5% on projects not in "
        "devagent's config. " + gaps_note)
    # The demo: the gap, the total and a group with nothing to sum.
    text, result = demo(tmp_path / "demo")
    t = tree(text)
    assert text_of(t, by_id(t, "off-page")) == gaps_note
    (gap,) = figures(t, "gap", panel_of(t, BUSY))
    assert text_of(t, gap) == "gap 19 points against intended 50% rescaled over built sites"
    unestimated = sum(s.unestimated_count for s in result.summaries if s.status == "built")
    (total,) = figures(t, "total")
    assert text_of(t, total) == f"Open work across built sites: 424 h, includes {unestimated} unestimated leaves"
    assert text_of(t, by_id(t, "totals")).endswith(" Site-only units are left out of totals across sites.")
    (not_built_sum,) = figures(t, "group-sum", sections(t)[-1])
    assert text_of(t, not_built_sum) == "nothing to sum"
    (br1_sum,) = figures(t, "group-sum", sections(t)[0])
    assert text_of(t, br1_sum) == "intended 50% as given; backlog share 31%"
    # A requirement no built site serves.
    copy = samples(tmp_path / "unserved")
    replace_bytes(copy / "portfolio.md", "- BR-3: Bring in a full harvest each autumn.\n",
                  "- BR-3: Bring in a full harvest each autumn.\n- BR-4: Mend the nets.\n")
    text, _ = page(copy)
    t = tree(text)
    br4 = sections(t)[3]
    assert "no built site serves this requirement" in text_of(t, br4)
    assert [text_of(t, i) for i in figures(t, "group-sum", br4)] == ["nothing to sum"]
    # No open work anywhere; leaves held out by a cycle; the singulars.
    done = b"- [x] Done {issue: Issue-1}\n"
    held = b"- [ ] A {issue: Issue-1, depends_on: [Issue-2]}\n- [ ] B {issue: Issue-2, depends_on: [Issue-1]}\n"
    t, _ = built_page(tmp_path / "held", {"done": done, "held": held})
    assert "no open work" in text_of(t, panel_of(t, "held"))
    assert figures(t, "backlog") == []
    (hours,) = figures(t, "hours", panel_of(t, "held"))
    assert text_of(t, hours) == "2 open leaves, 0 h; 2 held out by a cycle, not in hours"
    t, _ = built_page(tmp_path / "one", {"a": b"- [ ] Only {issue: Issue-1, est: 3pt}\n"})
    li = panel_of(t, "a")
    (hours,) = figures(t, "hours", li)
    assert text_of(t, hours) == "1 open leaf, 6 h, includes 1 unestimated leaf"
    assert ("also 3 pt in this site's own unit, left out of totals across sites; 1 leaf, 1 of them at the default "
            "unit (counted as unestimated)") in [text_of(t, p) for p in find(t, "p", within=li)]


def test_hours_source_no_file_read(tmp_path):
    copy = samples(tmp_path)
    (copy / OWN_UNIT / "estimates-2026-01-15-x.md").write_bytes(b"| # | hours |\n| #6 | \xff |\n")
    text, _ = page(copy)
    t = tree(text)
    (orchard,) = figures(t, "hours", panel_of(t, OWN_UNIT))
    assert t.elements[orchard]["attrs"]["data-source"] == "WBS.md, no estimates file read"
    harbor = figures(t, "hours", panel_of(t, BUSY))[0]
    assert t.elements[harbor]["attrs"]["data-source"] == "WBS.md and estimates-2026-01-15-effort-and-model-tiers.md"


# --- the doc (AC12) ----------------------------------------------------------------------

SERVING = ("real pages are meant to be served only on a private network, for example a tailnet; Gantry writes "
           "them to your output folder and publishes nothing itself")


def test_strategic_doc_states_texts():
    from gantry.views import strategic as view

    doc = (ROOT / "docs" / "strategic-page.md").read_bytes().decode("utf-8")
    assert [text for text in view.STRATEGIC_TEXTS if f"`{text}`" not in doc] == []
    assert [kind for kind in view.FIGURE_KINDS if f"`{kind}`" not in doc] == []
    for name in ("`data-figure`", "`data-source`", "`data-unestimated`", "`strategic.html`", ONE_LINER):
        assert name in doc, name
    assert SERVING in " ".join(doc.split())


def test_render_failed_site_counts_as_not_built(tmp_path, monkeypatch):
    """A site read but not rendered is not built (I2): its hours leave every share."""
    from gantry.views import worksite

    render_model = worksite.render_model

    def failing(m, **kw):
        if m["site"] == SMALL:
            raise ValueError("forced")
        return render_model(m, **kw)

    monkeypatch.setattr(worksite, "render_model", failing)
    text, _ = demo(tmp_path)
    t = tree(text)
    assert [g["sites"] for g in page_data(text)["groups"] if g["kind"] == "not_built"] == [[SMALL, NO_WBS, UNREADABLE]]
    (total,) = figures(t, "total")
    assert text_of(t, total).startswith("Open work across built sites: 250 h, ")  # harbor 130, orchard 90, quarry 30
    shares = {site: text_of(t, figures(t, "backlog", panel_of(t, site))[0]) for site in (BUSY, OWN_UNIT, UNESTIMATED)}
    assert shares == {BUSY: "backlog share, not effort: 52%",
                      OWN_UNIT: "backlog share, not effort: 36%, includes 5 unestimated leaves",
                      UNESTIMATED: "backlog share, not effort: 12%, includes 5 unestimated leaves"}
