"""The trace across sites (#21): which sites were not read, the installation
trace, the page's foreign leaves and links, and the full build that joins them.

The model tests need no Node; the page-script tests take the `node` fixture.
"""

import copy
import re
from pathlib import PurePath
from urllib.parse import quote, unquote

from gantry import build, links, sites
from gantry.config import load_config
from gantry.inputs import Inputs
from gantry.model import SiteData
from gantry.sites import SiteEntry, config_roots, load_sites
from gantry.trace import (FOREIGN_SOURCE, INSTALLATION_SCOPE, MESSAGES, ForeignLeaf, build_installation_trace,
                          build_site_trace, load_catalog, page_trace, sites_not_read)
from gantry.render import CLASS_TOKENS
from gantry.views import worksite
from helpers_config import demo_build, write_site_tree
from page_safety import HOSTILE, assert_no_external_resources, assert_page_safe
from page_script.harness import Planted, open_page, run_function
from sample_roles import BUSY, NO_WBS, READABLE, SMALL, UNESTIMATED, UNREADABLE
from test_crosssite import force_render
from test_samples import GANTRY_SAMPLE
from test_trace import catalog, leaf, rid, tree
from test_requirement_path import box_items
from test_trace_page import (EMPTY_MARKS, NO_VIEW, by_id, hostile_leaf, installation, marks_after, report_items, req_li,
                             row, sample, select, trace_of)
from test_worksite_page import STRUCTURAL, find, text_of
from test_worksite_page import tree as page_tree

HERE = PurePath("/planning")


# --- Task 2: which sites were not read ----------------------------------------------

# One site per class, in config order: (name, SiteEntry.problem, the build's reason or None when built).
# The first rows are not read; the NO_LEAVES rows prove the site has no leaves; the last is built.
NOT_READ_CLASSES = [
    ("bad name", "name does not match [A-Za-z0-9_.-]+", "name does not match [A-Za-z0-9_.-]+"),
    ("Clash", None, build.CLASH.format(earlier="clash")),
    ("unreadable", None, "WBS.md is not valid UTF-8"),
    ("raises", None, "render error: ValueError: boom"),
    ("tracefault", None, "render error: KeyError: 'x'"),
    ("empty", "devdoc_dir is empty", "devdoc_dir is empty"),
    ("notfile", "WBS.md is not a file", "WBS.md is not a file"),
    ("notable", "[project.notable] is not a table", "[project.notable] is not a table"),
    ("notstring", "devdoc_dir is not a string", "devdoc_dir is not a string"),
    ("badpath", "devdoc_dir is not an absolute or relative path", "devdoc_dir is not an absolute or relative path"),
    ("notdir", "planning directory is not a directory", "planning directory is not a directory"),
    ("cantcheck", "cannot check WBS.md: permission denied", "cannot check WBS.md: permission denied"),
    ("outside", "devdoc_dir of site outside is outside every root", "devdoc_dir of site outside is outside every root"),
    ("nodevdoc", sites.NO_DEVDOC_DIR, sites.NO_DEVDOC_DIR),
    ("noplanning", sites.NO_PLANNING_DIR, sites.NO_PLANNING_DIR),
    ("nowbs", sites.NO_WBS, sites.NO_WBS),
    ("built", None, None),
]


def test_no_leaves_texts_unchanged():
    # The constants replace literals: the texts the index and the CLI print stay put.
    assert sites.NO_DEVDOC_DIR == "no devdoc_dir"
    assert sites.NO_PLANNING_DIR == "planning directory does not exist"
    assert sites.NO_WBS == "no WBS.md in the planning directory"
    assert sites.NO_LEAVES == frozenset({sites.NO_DEVDOC_DIR, sites.NO_PLANNING_DIR, sites.NO_WBS})


def test_not_read_classes():
    entries = [SiteEntry(name, None if problem else HERE / name, problem) for name, problem, _ in NOT_READ_CLASSES]
    unbuilt = {name: reason for name, _, reason in NOT_READ_CLASSES if reason is not None}
    expected = tuple(name for name, problem, reason in NOT_READ_CLASSES
                     if reason is not None and problem not in sites.NO_LEAVES)
    assert expected[:5] == ("bad name", "Clash", "unreadable", "raises", "tracefault")
    assert len(expected) == len(NOT_READ_CLASSES) - 4
    assert sites_not_read(entries, unbuilt) == expected


def test_not_read_is_config_order():
    entries = [SiteEntry(name, HERE / name, None) for name in ("zeta", "alpha", "mid")]
    assert sites_not_read(entries, {"mid": "x", "zeta": "y"}) == ("zeta", "mid")
    assert sites_not_read(entries, {}) == ()


def test_not_read_samples():
    config = load_config(GANTRY_SAMPLE)
    roots = config_roots(config)
    entries = load_sites(config, roots)[0]
    _, reasons = build.read_sites(config, entries, roots=roots)
    assert NO_WBS in reasons
    assert sites_not_read(entries, reasons) == (UNREADABLE,)


# --- Task 3: the installation trace -------------------------------------------------


def entry(name):
    return SiteEntry(name, HERE / name, None)


def inputs_of(leaves):
    return Inputs(SiteData(tuple(leaves), (), (), (), ()), None, None)


def installation_trace(cat, leaves, *, order=None, unbuilt=None):
    """build_installation_trace over in-memory sites: `leaves` maps each read
    site to its leaves; `order` is the config order (default: `leaves`')."""
    order = order or list(leaves)
    inputs = {name: inputs_of(found) for name, found in leaves.items()}
    traces = {name: build_site_trace(name, found, cat) for name, found in leaves.items()}
    return build_installation_trace([entry(n) for n in order], inputs, traces, unbuilt or {}, cat), traces


ALPHA = "- FR-1: a\n- FR-2: b\n"


def test_foreign_leaf_found_without_config(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": ALPHA, "beta": None, "gamma": None}))
    before, _ = installation_trace(cat, {"alpha": [], "beta": [leaf("B1", 2)], "gamma": [leaf("G1", 2)]})
    assert before["alpha"].foreign == () and "alpha:FR-1" in before["alpha"].report.unserved
    after, _ = installation_trace(cat, {"alpha": [], "beta": [leaf("B1", 2, satisfies="alpha:FR-1")],
                                        "gamma": [leaf("G1", 2, satisfies="alpha:FR-1")]})
    alpha = after["alpha"]
    assert [(f.site, f.key) for f in alpha.foreign] == [("beta", "B1"), ("gamma", "G1")]
    assert alpha.foreign[0] == ForeignLeaf("beta", "B1", 2, "B1", "B1", "pending", True, (rid("alpha:FR-1"),),
                                           (("alpha:FR-1", "direct"),))
    assert alpha.report.unserved == ("alpha:FR-2",)


def test_foreign_order_config_then_wbs(tmp_path):
    cat = catalog(tree(tmp_path, {"zeta": None, "alpha": ALPHA, "beta": None}))
    found, _ = installation_trace(cat, {"zeta": [leaf("L9", 2, satisfies="alpha:FR-1")], "alpha": [],
                                        "beta": [leaf("L2", 2, satisfies="alpha:FR-2"),
                                                 leaf("L1", 3, satisfies="alpha:FR-1")]})
    assert [(f.site, f.key) for f in found["alpha"].foreign] == [("zeta", "L9"), ("beta", "L2"), ("beta", "L1")]
    assert list(found) == ["zeta", "alpha", "beta"]


def test_foreign_indirect_counts(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": "- SR-1: s\n- FR-1: a {refines: SR-1}\n", "beta": None}))
    found, _ = installation_trace(cat, {"alpha": [], "beta": [leaf("B1", 2, satisfies="alpha:FR-1")]})
    (f,) = found["alpha"].foreign
    assert f.serves == (("alpha:SR-1", "indirect"), ("alpha:FR-1", "direct"))
    assert found["alpha"].report.unserved == ()


def test_foreign_serves_own_requirements_only(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": ALPHA, "gamma": "- FR-1: g\n", "beta": None}))
    found, _ = installation_trace(cat, {"alpha": [], "gamma": [],
                                        "beta": [leaf("B1", 2, satisfies="[alpha:FR-1, gamma:FR-1]")]})
    (f,) = found["alpha"].foreign
    assert f.ids == (rid("alpha:FR-1"), rid("gamma:FR-1")) and f.serves == (("alpha:FR-1", "direct"),)
    assert [g.serves for g in found["gamma"].foreign] == [(("gamma:FR-1", "direct"),)]


def test_foreign_skipped_never_serves(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": ALPHA, "beta": None}))
    found, _ = installation_trace(cat, {"alpha": [], "beta": [leaf("B1", 2, "-", satisfies="alpha:FR-1")]})
    assert found["alpha"].foreign == ()
    assert "alpha:FR-1" in found["alpha"].report.unserved


def test_foreign_finished_leaf_kept_with_state(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": ALPHA, "beta": None}))
    found, _ = installation_trace(cat, {"alpha": [], "beta": [leaf("B1", 2, "x", satisfies="alpha:FR-1")]})
    (f,) = found["alpha"].foreign
    assert (f.state, f.open) == ("done", False)
    assert "alpha:FR-1" not in found["alpha"].report.unserved


def test_undefined_foreign_listed_on_owner(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": ALPHA, "beta": None}))
    found, traces = installation_trace(cat, {"alpha": [], "beta": [leaf("B1", 5, satisfies="alpha:FR-99")]})
    (e,) = found["alpha"].report.undefined_foreign
    assert (e.source, e.line, e.kind) == ("beta WBS.md", 5, "undefined_foreign")
    assert e.source == FOREIGN_SOURCE.format(site="beta")
    assert e.message.startswith("leaf 'B1': satisfies entry alpha:FR-99 names no requirement")
    assert [x.kind for x in found["beta"].report.undefined] == ["undefined_id"]
    assert found["beta"].report.undefined_foreign == ()
    cat = catalog(tree(tmp_path / "off", {"alpha": None, "beta": None}))
    found, _ = installation_trace(cat, {"alpha": [], "beta": [leaf("B1", 5, satisfies="alpha:FR-99")]})
    assert found["alpha"].report.undefined_foreign == ()


def test_undefined_foreign_order_config_then_line(tmp_path):
    cat = catalog(tree(tmp_path, {"zeta": None, "alpha": ALPHA, "beta": None}))
    found, _ = installation_trace(cat, {"zeta": [leaf("Z1", 9, satisfies="alpha:FR-7")], "alpha": [],
                                        "beta": [leaf("B2", 4, satisfies="alpha:FR-8"),
                                                 leaf("B1", 3, satisfies="alpha:FR-9")]})
    assert [(e.source, e.line) for e in found["alpha"].report.undefined_foreign] == [
        ("zeta WBS.md", 9), ("beta WBS.md", 3), ("beta WBS.md", 4)]


def test_scope_and_not_read_set_on_every_site(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": ALPHA, "beta": None, "marsh": None}))
    found, traces = installation_trace(cat, {"alpha": [], "beta": [leaf("B1", 2)]},
                                       order=["alpha", "marsh", "beta"], unbuilt={"marsh": "unreadable"})
    assert list(found) == ["alpha", "beta"]
    for t in found.values():
        assert t.report.unserved_scope == INSTALLATION_SCOPE == "installation"
        assert t.report.sites_not_read == ("marsh",)
    assert found["beta"].report.unserved is None and found["beta"].report.untraced is None
    assert found["beta"].foreign == ()
    assert found["beta"].report == build.replace(traces["beta"].report, unserved_scope="installation",
                                                 sites_not_read=("marsh",))


def test_inputs_not_mutated(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": ALPHA, "beta": None}))
    leaves = {"alpha": [], "beta": [leaf("B1", 2, satisfies="[alpha:FR-1, alpha:FR-9]")]}
    traces = {name: build_site_trace(name, found, cat) for name, found in leaves.items()}
    kept = copy.deepcopy(traces)
    build_installation_trace([entry(n) for n in leaves], {n: inputs_of(v) for n, v in leaves.items()}, traces, {}, cat)
    assert traces == kept


def test_undefined_foreign_message():
    assert MESSAGES["undefined_foreign"] == "leaf {key!r}: satisfies entry {rid} names no requirement: {why}"
    assert FOREIGN_SOURCE == "{site} WBS.md"


# --- Task 4: the page's `trace` data ------------------------------------------------

TRACE_KEYS = ["in_trace", "scope", "requirements", "leaves", "report", "not_read", "foreign"]


def sample_installation(config_path=GANTRY_SAMPLE):
    """(page traces, SiteTraces, catalog, inputs) of an installation (the
    sample's by default), widened as a full build widens them, every read
    site built."""
    config = load_config(config_path)
    roots = config_roots(config)
    entries = load_sites(config, roots)[0]
    cat = load_catalog(roots, config, entries)
    inputs, reasons = build.read_sites(config, entries, roots=roots)
    traces = {name: build_site_trace(name, i.data.leaves, cat) for name, i in inputs.items()}
    wide = build_installation_trace(entries, inputs, traces, reasons, cat)
    pages = {name: page_trace(t, cat, inputs[name].data, built=frozenset(inputs)) for name, t in wide.items()}
    return pages, wide, cat, inputs


def test_trace_data_keys(tmp_path):
    result = sample(tmp_path)
    for name in sorted(READABLE):
        trace = trace_of(result.pages[name])
        assert list(trace) == TRACE_KEYS, name
    pages, *_ = sample_installation()
    for name, trace in pages.items():
        assert list(trace) == TRACE_KEYS, name
        for r in trace["requirements"]:
            assert list(r)[-3:] == ["finished", "foreign", "counts"], (name, r["id"])
    assert [r["id"] for r in pages[UNESTIMATED]["requirements"]] == ["lighthouse:FR-2"]


def test_foreign_index_not_key():
    pages, *_ = sample_installation()
    trace = pages[SMALL]
    fr2 = row(trace, "lighthouse:FR-2")
    assert fr2["foreign"] == [[0, "direct"]]
    assert trace["foreign"][0][:2] == ["quarry", "Issue-3"]
    # Lighthouse's own Issue-3 is finished and serves SR-1, never FR-2.
    assert all(k != "Issue-3" for k, _ in fr2["light"]) and fr2["finished"] == []
    assert all(r["foreign"] == [] for r in pages[UNESTIMATED]["requirements"])


def test_foreign_record_and_not_read():
    pages, *_ = sample_installation()
    trace = pages[SMALL]
    assert trace["scope"] == "installation" and trace["not_read"] == [UNREADABLE]
    assert trace["foreign"] == [["quarry", "Issue-3", "Issue-3", "Grade the road", "pending", True,
                                 [["lighthouse:FR-2", "FR-2", None]], "quarry.html#leaf-Issue-3"]]
    assert pages[UNESTIMATED]["foreign"] == [] and pages[UNESTIMATED]["not_read"] == [UNREADABLE]


def test_foreign_href_needs_a_built_page():
    _, wide, cat, inputs = sample_installation()
    page = page_trace(wide[SMALL], cat, inputs[SMALL].data, built=frozenset({SMALL}))
    assert page["foreign"][0][-1] is None


def test_site_scope_page_trace_has_no_foreign():
    _, _, cat, inputs = sample_installation()
    page = page_trace(build_site_trace(SMALL, inputs[SMALL].data.leaves, cat), cat, inputs[SMALL].data)
    assert page["scope"] == "site" and page["not_read"] is None and page["foreign"] == []
    assert "undefined_foreign" not in [name for name, _ in page["report"]]
    assert all(r["foreign"] == [] for r in page["requirements"])


def test_id_href_rules():
    built = frozenset({"alpha", "beta", "life"})
    assert links.id_href(rid("beta:FR-1"), "alpha", built, "life") == "beta.html"
    assert links.id_href(rid("alpha:FR-1"), "alpha", built, "life") is None
    assert links.id_href(rid("life:BR-1"), "alpha", built, "life") is None
    assert links.id_href(rid("zeta:FR-1"), "alpha", built, "life") is None
    assert links.id_href(rid("beta:FR-1"), "alpha", frozenset({"alpha"}), "life") is None


def test_undefined_foreign_in_report(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": ALPHA, "beta": None}))
    found, _ = installation_trace(cat, {"alpha": [], "beta": [leaf("B1", 5, satisfies="alpha:FR-99")]})
    page = page_trace(found["alpha"], cat, inputs_of([]).data, built=frozenset({"alpha", "beta"}))
    names = [name for name, _ in page["report"]]
    assert names[names.index("undefined") + 1] == "undefined_foreign" and names[-2:] == ["unserved", "untraced"]
    (item,) = dict(page["report"])["undefined_foreign"]
    assert item[:2] == ["beta WBS.md", 5]
    assert "undefined_foreign" not in [name for name, _ in page_trace(found["beta"], cat, inputs_of([]).data)["report"]]


def test_undefined_foreign_rendered_source(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": ALPHA, "beta": None}))
    found, _ = installation_trace(cat, {"alpha": [], "beta": [leaf("B1", 5, satisfies="alpha:FR-99")]})
    data = inputs_of([]).data
    page = worksite.render_page("alpha", data, default_unit_hours=6,
                                trace=page_trace(found["alpha"], cat, data, built=frozenset({"alpha", "beta"})))
    label = worksite.TRACE_LABELS["undefined_foreign"]
    assert label == "Undefined IDs on other sites"
    count, (item,) = report_items(page_tree(page))[label]
    assert count == f"{label}: 1"
    assert item.startswith("beta WBS.md line 5: leaf 'B1': satisfies entry alpha:FR-99 names no requirement")


# --- Task 5: the path box ------------------------------------------------------------


def installation_models(config_path=GANTRY_SAMPLE):
    """{site: page model} with the widened trace, as a full build renders it."""
    pages, _, _, inputs = sample_installation(config_path)
    config = load_config(config_path)
    return {name: worksite.page_model(name, inputs[name].data, default_unit_hours=config.default_unit_hours,
                             inputs=inputs[name], trace=pages[name]) for name in pages}


def path_of(m, rid_):
    return row(m["trace"], rid_)["path"]


NOT_READ_MARSH = worksite.PATH_NOT_READ.format(sites=UNREADABLE)


def test_sample_own_rows_installation_scope():
    models = installation_models()
    fr2 = path_of(models[SMALL], "lighthouse:FR-2")
    assert fr2["state"] == "path" and fr2["order"] == [] and fr2["lines"] == []
    assert fr2["incomplete"] == ["also served on quarry by Issue-3, which this page does not plan", NOT_READ_MARSH]
    uc2 = path_of(models[SMALL], "lighthouse:UC-2")
    assert uc2["lines"] == [worksite.PATH_UNSERVED_SITE] and uc2["incomplete"] == [NOT_READ_MARSH]
    # A fully served row still says a site was not read (D1): its leaves may serve it too.
    assert NOT_READ_MARSH in path_of(models[SMALL], "lighthouse:FR-1")["incomplete"]
    # Another namespace's row keeps the site forms and no not-read reason (D2).
    assert path_of(models[SMALL], "life:BR-1")["incomplete"] == []
    quarry = path_of(models[UNESTIMATED], "lighthouse:FR-2")
    assert NOT_READ_MARSH not in quarry["incomplete"] and quarry["state"] == "path"


TWO_REQS = "# R\n\n## Functional requirements\n\n- FR-1: One.\n"


def test_whole_installation_uses_unscoped_texts(tmp_path):
    wbs = {"alpha": "# Alpha\n\n- [ ] One {issue: Issue-1}\n", "beta": "# Beta\n\n- [ ] Two {issue: Issue-2}\n"}
    fr1 = path_of(installation_models(installation(tmp_path / "a", wbs, {"alpha": TWO_REQS}))["alpha"], "alpha:FR-1")
    assert fr1["lines"] == [worksite.PATH_UNSERVED] and fr1["incomplete"] == []
    wbs["beta"] = "# Beta\n\n- [x] Two {issue: Issue-2, satisfies: alpha:FR-1}\n"
    fr1 = path_of(installation_models(installation(tmp_path / "b", wbs, {"alpha": TWO_REQS}))["alpha"], "alpha:FR-1")
    assert fr1["state"] == "finished" and fr1["lines"] == [worksite.PATH_FINISHED] and fr1["incomplete"] == []


# --- Task 6: the full build ----------------------------------------------------------


def test_samples_installation_trace(tmp_path):
    trace = trace_of(sample(tmp_path).pages[SMALL])
    assert trace["foreign"][0][:5] == ["quarry", "Issue-3", "Issue-3", "Grade the road", "pending"]
    assert row(trace, "lighthouse:FR-2")["foreign"] == [[0, "direct"]]
    assert dict(trace["report"])["unserved"] == [["lighthouse:UC-2", "UC-2"]]
    assert trace["scope"] == "installation" and trace["not_read"] == [UNREADABLE]


def test_not_read_names_unparseable_not_absent(tmp_path):
    result = sample(tmp_path)
    for name in READABLE:
        not_read = trace_of(result.pages[name])["not_read"]
        assert not_read == [UNREADABLE] and NO_WBS not in not_read, name


def test_site_build_keeps_site_scope(tmp_path, monkeypatch):
    code, out, stdout, err = demo_build(tmp_path, monkeypatch, "--site", SMALL)
    assert code == 0, err
    page = (out / "sites" / f"{SMALL}.html").read_text(encoding="utf-8")
    trace = trace_of(page)
    assert trace["scope"] == "site" and trace["not_read"] is None and trace["foreign"] == []
    assert "Unserved requirements (served only within this site)" in report_items(page_tree(page))
    assert stdout.splitlines()[-1].endswith(", unserved 2, untraced 3")


HREF_RE = re.compile(r'^([^/#?:]+)\.html(?:#(.+))?$')


def test_foreign_hrefs_land(tmp_path, monkeypatch):
    code, out, _, err = demo_build(tmp_path, monkeypatch)
    assert code == 0, err
    checked = 0
    for page_path in sorted((out / "sites").glob("*.html")):
        trace = trace_of(page_path.read_text(encoding="utf-8"))
        hrefs = [f[7] for f in trace["foreign"] if f[7] is not None]
        hrefs += [i[2] for f in trace["foreign"] for i in f[6] if i[2] is not None]
        for href in hrefs:
            m = HREF_RE.match(href)
            assert m, href
            target = out / "sites" / f"{unquote(m.group(1))}.html"
            assert target.is_file(), href
            if m.group(2) is not None:
                assert f'id="{m.group(2)}"' in target.read_text(encoding="utf-8"), href
            checked += 1
    assert checked >= 1


def test_render_failed_site_unlinked(tmp_path, monkeypatch):
    calls = force_render(monkeypatch, lambda site, n: site == "beta")
    config = write_site_tree(tmp_path, {
        "alpha": b"- [ ] One {issue: Issue-1}\n",
        "beta": b"- [ ] Two {issue: Issue-2, satisfies: alpha:FR-1}\n",
    }, tail='[site.alpha]\nrequirements = "req-alpha.md"\n')
    (tmp_path / "req-alpha.md").write_bytes(TWO_REQS.encode("utf-8"))
    result = build.build_all(load_config(config))
    assert list(result.pages) == ["alpha"] and calls == {"alpha": 2, "beta": 1}
    page = result.pages["alpha"]
    trace = trace_of(page)
    assert [f[:2] + f[7:] for f in trace["foreign"]] == [["beta", "Issue-2", None]]
    assert "beta.html" not in page
    assert dict(trace["report"])["unserved"] == []
    # A render failure is not a read failure: beta's leaves were read and counted.
    assert trace["not_read"] == []


def test_row_link_target_failed_rerenders(tmp_path, monkeypatch):
    # beta's own leaf names alpha:FR-1, so beta's row links to alpha.html. When
    # alpha fails to render, beta is rendered again without the link (#21).
    calls = force_render(monkeypatch, lambda site, n: site == "alpha")
    config = write_site_tree(tmp_path, {
        "alpha": b"- [ ] One {issue: Issue-1}\n",
        "beta": b"- [ ] Two {issue: Issue-2, satisfies: alpha:FR-1}\n",
    }, tail='[site.alpha]\nrequirements = "req-alpha.md"\n')
    (tmp_path / "req-alpha.md").write_bytes(TWO_REQS.encode("utf-8"))
    result = build.build_all(load_config(config))
    assert list(result.pages) == ["beta"] and calls == {"alpha": 1, "beta": 2}
    assert "alpha.html" not in result.pages["beta"]


def test_foreign_id_target_failed_rerenders(tmp_path, monkeypatch):
    # beta's leaf serves alpha and names gamma:FR-1 too, so alpha's panel links
    # gamma.html. When gamma fails to render, alpha (through its foreign leaf's
    # IDs) and beta (through its own leaf's) are rendered again without it.
    calls = force_render(monkeypatch, lambda site, n: site == "gamma")
    config = write_site_tree(tmp_path, {
        "alpha": b"- [ ] One {issue: Issue-1}\n",
        "beta": b"- [ ] Two {issue: Issue-2, satisfies: [alpha:FR-1, gamma:FR-1]}\n",
        "gamma": b"- [ ] Three {issue: Issue-3}\n",
    }, tail='[site.alpha]\nrequirements = "req-alpha.md"\n[site.gamma]\nrequirements = "req-gamma.md"\n')
    for name in ("alpha", "gamma"):
        (tmp_path / f"req-{name}.md").write_bytes(TWO_REQS.encode("utf-8"))
    result = build.build_all(load_config(config))
    assert list(result.pages) == ["alpha", "beta"] and calls == {"alpha": 2, "beta": 2, "gamma": 1}
    assert "gamma.html" not in result.pages["alpha"] and "gamma.html" not in result.pages["beta"]
    assert [i[0] for i in trace_of(result.pages["alpha"])["foreign"][0][6]] == ["alpha:FR-1", "gamma:FR-1"]


def test_trace_fault_site_not_read(tmp_path, monkeypatch):
    real = build.build_site_trace

    def failing(site, leaves, catalog):
        if site == BUSY:
            raise RuntimeError("trace fault")
        return real(site, leaves, catalog)

    monkeypatch.setattr(build, "build_site_trace", failing)
    result = sample(tmp_path)
    assert trace_of(result.pages[SMALL])["not_read"] == [BUSY, UNREADABLE]
    assert BUSY not in result.pages


# --- Task 7: the page: panel, labels, ID links --------------------------------------


def panel_items(page):
    t = page_tree(page)
    return t, [i for i in find(t, "li", ["xleaf"])]


def test_sample_panel_rendered(tmp_path):
    page = sample(tmp_path).pages[SMALL]
    t, items = panel_items(page)
    section = by_id(t, "served-elsewhere")
    (h2,) = find(t, "h2", within=section)
    assert text_of(t, h2) == worksite.XSITE_HEADING == "Served from other sites"
    (li,) = items
    assert t.elements[li]["attrs"]["data-item"] == "0"
    assert text_of(t, li) == "quarry \u00b7 Issue-3 \u00b7 Grade the road \u00b7 open \u00b7 FR-2"
    (a,) = find(t, "a", within=li)
    assert t.elements[a]["attrs"]["href"] == "quarry.html#leaf-Issue-3"
    assert text_of(t, a) == "quarry \u00b7 Issue-3 \u00b7 Grade the road"
    (ul,) = find(t, "ul", ["xsite"], within=section)
    assert {"xsite", "xleaf"} <= STRUCTURAL


def test_panel_item_forms(tmp_path):
    cat = catalog(tree(tmp_path, {"alpha": ALPHA, "beta": None, "gamma": "- FR-1: g\n"}))
    found, _ = installation_trace(cat, {"alpha": [], "beta": [
        leaf("B1", 2, "x", satisfies="[alpha:FR-1, gamma:FR-1]"), leaf("B2", 3, "~", satisfies="alpha:FR-2")],
        "gamma": []})
    data = inputs_of([]).data
    page = worksite.render_page("alpha", data, default_unit_hours=6,
                                trace=page_trace(found["alpha"], cat, data, built=frozenset({"alpha", "gamma"})))
    t, items = panel_items(page)
    assert [text_of(t, i) for i in items] == [
        "beta \u00b7 B1 \u00b7 B1 \u00b7 finished \u00b7 FR-1, gamma:FR-1",
        "beta \u00b7 B2 \u00b7 B2 \u00b7 in progress \u00b7 FR-2"]
    # beta has no page: its leaf is plain text; gamma does, so gamma's ID links.
    assert [(t.elements[a]["attrs"]["href"], text_of(t, a)) for a in find(t, "a", within=items[0])] == [
        ("gamma.html", "gamma:FR-1")]
    assert [text_of(t, x) for x in find(t, "span", ["mono"], within=items[0])] == ["FR-1"]


def test_no_panel_without_foreign(tmp_path, monkeypatch):
    wbs = {"alpha": "# Alpha\n\n- [ ] One {issue: Issue-1, satisfies: alpha:FR-1}\n",
           "beta": "# Beta\n\n- [ ] Two {issue: Issue-2}\n"}
    result = build.build_all(load_config(installation(tmp_path, wbs, {"alpha": TWO_REQS})))
    page = result.pages["alpha"]
    assert "served-elsewhere" not in page and find(page_tree(page), "li", ["xleaf"]) == []
    code, out, _, err = demo_build(tmp_path / "site", monkeypatch, "--site", SMALL)
    assert code == 0, err
    assert "served-elsewhere" not in (out / "sites" / f"{SMALL}.html").read_text(encoding="utf-8")


def row_links(t, rid_):
    li = req_li(t, rid_)
    return [(t.elements[a]["attrs"]["href"], text_of(t, a)) for a in find(t, "a", within=li)]


def test_row_links_built_site(tmp_path):
    result = sample(tmp_path)
    quarry = result.pages[UNESTIMATED]
    assert '</button> <a href="lighthouse.html">lighthouse</a>' in quarry
    assert row_links(page_tree(quarry), "lighthouse:FR-2") == [("lighthouse.html", "lighthouse")]
    t = page_tree(result.pages[SMALL])
    assert row_links(t, "life:BR-1") == [] and row_links(t, "lighthouse:FR-2") == []


def test_no_row_links_in_site_build(tmp_path, monkeypatch):
    code, out, _, err = demo_build(tmp_path, monkeypatch, "--site", UNESTIMATED)
    assert code == 0, err
    page = (out / "sites" / f"{UNESTIMATED}.html").read_text(encoding="utf-8")
    assert row_links(page_tree(page), "lighthouse:FR-2") == [] and "lighthouse.html" not in page


# --- Task 8: the script: foreign lights (Node) ----------------------------------------

MARKED = "g.node.req-direct, g.node.req-indirect, g.node.req-upstream, rect.seg.req-direct, " \
         "rect.seg.req-indirect, rect.seg.req-upstream"


def lit_items(page):
    return [(el.attrs.get("data-item"), sorted(c for c in el.classes if c.startswith("req-")))
            for el in page.query("li.xleaf")]


def test_marks_foreign_from_row(node, tmp_path):
    page = sample(tmp_path).pages[SMALL]
    path = tmp_path / "lighthouse.html"
    path.write_bytes(page.encode("utf-8"))
    trace = trace_of(page)
    marks = marks_after(path, trace, [select("lighthouse:FR-2")])
    assert marks["foreign"] == [[0, "direct"]]
    # Foreign lights never dim the network: FR-2 has no leaf on this site.
    assert marks["leaves"] == [] and marks["selecting"] is False
    assert EMPTY_MARKS["foreign"] == []


def test_select_lights_foreign_leaf(node, tmp_path):
    path = tmp_path / "lighthouse.html"
    path.write_bytes(sample(tmp_path).pages[SMALL].encode("utf-8"))
    with open_page(path) as page:
        assert lit_items(page) == [("0", [])]
        page.drive("requirement", "lighthouse:FR-2")
        assert lit_items(page) == [("0", ["req-direct"])]
        assert page.query(MARKED) == []
        page.key("Escape")
        assert lit_items(page) == [("0", [])]
        page.drive("requirement", "lighthouse:SR-1")
        assert lit_items(page) == [("0", [])]


UC_REQS = "# R\n\n## Use cases\n\n- UC-1: Use. {needs: [FR-1]}\n\n## Functional requirements\n\n- FR-1: One.\n"


def test_use_case_lights_foreign_through_needs(node, tmp_path):
    wbs = {"alpha": "# Alpha\n\n- [ ] One {issue: Issue-1}\n",
           "beta": "# Beta\n\n- [ ] Two {issue: Issue-2, satisfies: alpha:FR-1}\n"}
    result = build.build_all(load_config(installation(tmp_path, wbs, {"alpha": UC_REQS})))
    path = tmp_path / "alpha.html"
    path.write_bytes(result.pages["alpha"].encode("utf-8"))
    with open_page(path) as page:
        page.drive("requirement", "alpha:UC-1")
        assert lit_items(page) == [("0", ["req-indirect"])]
        page.drive("requirement", "alpha:FR-1")
        assert lit_items(page) == [("0", ["req-direct"])]


def test_foreign_index_proto_safe(node, tmp_path):
    path = tmp_path / "lighthouse.html"
    page = sample(tmp_path).pages[SMALL]
    path.write_bytes(page.encode("utf-8"))
    trace = trace_of(page)
    for r in trace["requirements"]:
        r["foreign"] = [["__proto__", "direct"], ["0", "direct"], [0, "__proto__"], "x", None]
    marks = run_function(path, "gantryTrace.marks", [trace, {"selected": "lighthouse:FR-2", "hover": None}, NO_VIEW])
    assert marks["foreign"] == []
    trace["requirements"] = [{**r, "foreign": None} for r in trace["requirements"]]
    assert run_function(path, "gantryTrace.marks",
                        [trace, {"selected": "lighthouse:FR-2", "hover": None}, NO_VIEW])["foreign"] == []


# --- Task 9: the hostile leg -----------------------------------------------------------


def hostile_foreign_site(tmp_path):
    """(page, page trace, planted): alpha in the trace with FR-1, served on
    beta by a leaf keyed and numbered HOSTILE[0] and titled HOSTILE[2], and
    by a leaf keyed __proto__. Leaves are built in memory."""
    cat = catalog(tree(tmp_path, {"alpha": "- FR-1: One.\n", "beta": None}))
    own = hostile_leaf("Issue-1", "Issue-1", 1)
    beta = (hostile_leaf(HOSTILE[0], HOSTILE[0], 1, title=HOSTILE[2], satisfies="alpha:FR-1"),
            hostile_leaf("__proto__", "__proto__", 2, title=HOSTILE[4], satisfies="alpha:FR-1"))
    found, _ = installation_trace(cat, {"alpha": [own], "beta": list(beta)})
    data = SiteData((own,), (), (), (), ())
    trace = page_trace(found["alpha"], cat, data, built=frozenset({"alpha", "beta"}))
    page = worksite.render_page("alpha", data, default_unit_hours=6, trace=trace)
    return page, trace, [HOSTILE[0], HOSTILE[2], HOSTILE[4], "__proto__"]


def test_hostile_foreign_page_is_safe(tmp_path):
    page, trace, planted = hostile_foreign_site(tmp_path)
    data = assert_page_safe(page, planted)
    assert_no_external_resources(page)
    assert [f[1] for f in data["trace"]["foreign"]] == [HOSTILE[0], "__proto__"]
    assert trace["foreign"][0][7] == "beta.html#leaf-" + quote(HOSTILE[0], safe="")
    assert row(trace, "alpha:FR-1")["foreign"] == [[0, "direct"], [1, "direct"]]
    t = page_tree(page)
    used = {c for el in t.elements for c in el["classes"]}
    assert used - CLASS_TOKENS - STRUCTURAL == set()
    assert len(find(t, "li", ["xleaf"])) == 2


def test_hostile_foreign_under_harness(node, tmp_path):
    page_text, _, planted = hostile_foreign_site(tmp_path)
    path = tmp_path / "hostile.html"
    path.write_bytes(page_text.encode("utf-8"))
    with open_page(path) as page:
        assert page.planted_nodes(planted) == Planted([], [], [])
        page.drive("requirement", "alpha:FR-1")
        assert page.planted_nodes(planted) == Planted([], [], [])
        assert lit_items(page) == [("0", ["req-direct"]), ("1", ["req-direct"])]
        assert box_items(page, "path-why") == [
            "also served on beta by " + HOSTILE[0] + ", which this page does not plan",
            "also served on beta by __proto__, which this page does not plan"]
        used = {c for e in page.query("[class]") for c in e.classes}
        assert used - CLASS_TOKENS - STRUCTURAL == set()


# --- Task 11: the docs -----------------------------------------------------------------

DOCS = PurePath(__file__).parent.parent / "docs"


def doc(name):
    return " ".join(open(DOCS / name, encoding="utf-8").read().split())


def test_doc_states_cross_site():
    page = doc("worksite-page.md")
    for text in ("`served-elsewhere`", "`not_read`", "`<name>.html#leaf-<key>`", "`<name>.html`",
                 "`undefined_foreign`", f"`{worksite.XSITE_HEADING}`", f"`{worksite.UNSERVED_NOT_COUNTED}`",
                 f"`{worksite.PATH_NOT_READ}`", f"`{FOREIGN_SOURCE}`", "`installation`"):
        assert text in page, text
    fmt = doc("requirements-format.md")
    for text in ("`sites_not_read`", "`undefined_foreign`", '"installation"', "--site"):
        assert text in fmt, text


def test_no_stale_cross_site_promises():
    root = PurePath(__file__).parent.parent
    for rel in ("docs/worksite-page.md", "docs/requirements-format.md", "gantry/trace.py", "gantry/path.py",
                "gantry/build.py", "gantry/views/worksite.py", "tests/test_trace_page.py"):
        text = " ".join(open(root / rel, encoding="utf-8").read().split())
        for stale in ("#21 widens", "wait for #21", "Provisional (#21", "every page today uses the site forms",
                      "#21 adds its keys"):
            assert stale not in text, (rel, stale)


# --- Final review fixes -----------------------------------------------------------------


def test_finished_elsewhere_with_unread_site(tmp_path):
    # Only another site's finished leaf serves FR-1 and a third site is unreadable:
    # nothing on alpha serves it, so the status may not say "on this site".
    config = write_site_tree(tmp_path, {
        "alpha": b"# Alpha\n\n- [ ] One {issue: Issue-1}\n",
        "beta": b"# Beta\n\n- [x] Two {issue: Issue-2, satisfies: alpha:FR-1}\n",
        "gamma": b"# Gamma\n\n- [ ] Bad \xff {issue: Issue-3}\n",
    }, tail='[site.alpha]\nrequirements = "req-alpha.md"\n')
    (tmp_path / "req-alpha.md").write_bytes(TWO_REQS.encode("utf-8"))
    result = build.build_all(load_config(config))
    fr1 = row(trace_of(result.pages["alpha"]), "alpha:FR-1")["path"]
    assert fr1["state"] == "finished"
    assert fr1["lines"] == [worksite.PATH_FINISHED]
    assert fr1["incomplete"] == [worksite.PATH_NOT_READ.format(sites="gamma")]


def test_br_namespace_site_rows_keep_site_forms(tmp_path):
    # A site named like br_namespace is never in the trace: its rows are not
    # covered by the installation trace, so they keep the --site forms (D2).
    wbs = {"life": "# Life\n\n- [x] Done {issue: Issue-1, satisfies: life:BR-1}\n",
           "alpha": "# Alpha\n\n- [ ] Open {issue: Issue-2, satisfies: life:BR-1}\n"}
    result = build.build_all(load_config(installation(tmp_path, wbs, {"alpha": TWO_REQS}, portfolio="- BR-1: b\n")))
    trace = trace_of(result.pages["life"])
    assert trace["in_trace"] is False and trace["scope"] == "installation"
    br1 = row(trace, "life:BR-1")["path"]
    assert br1["lines"] == [worksite.PATH_FINISHED_SITE] and br1["incomplete"] == []
