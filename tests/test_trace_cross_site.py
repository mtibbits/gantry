"""The trace across sites (#21): which sites were not read, the installation
trace, the page's foreign leaves and links, and the full build that joins them.

The model tests need no Node; the page-script tests take the `node` fixture.
"""

from pathlib import PurePath

from gantry import build, sites
from gantry.config import load_config
from gantry.sites import SiteEntry, config_roots, load_sites
from gantry.trace import sites_not_read
from sample_roles import NO_WBS, UNREADABLE
from test_samples import GANTRY_SAMPLE

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
