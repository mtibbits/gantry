"""The trace across sites (#21): which sites were not read, the installation
trace, the page's foreign leaves and links, and the full build that joins them.

The model tests need no Node; the page-script tests take the `node` fixture.
"""

import copy
from pathlib import PurePath

from gantry import build, sites
from gantry.config import load_config
from gantry.inputs import Inputs
from gantry.model import SiteData
from gantry.sites import SiteEntry, config_roots, load_sites
from gantry.trace import (FOREIGN_SOURCE, INSTALLATION_SCOPE, MESSAGES, ForeignLeaf, build_installation_trace,
                          build_site_trace, sites_not_read)
from sample_roles import NO_WBS, UNREADABLE
from test_samples import GANTRY_SAMPLE
from test_trace import catalog, leaf, rid, tree

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
