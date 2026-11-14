"""The path to a selected requirement's work (#77): gantry/path.py, the
`path` record on each trace row, the path box and its marks.

The model tests need no Node; the page-script tests take the `node` fixture.
Sample literals are hand-derived from samples/lighthouse/.
"""

from gantry import build
from gantry.config import load_config
from gantry.inputs import scheduled
from gantry.sites import config_roots, load_sites
from gantry.trace import build_site_trace, load_catalog, page_trace
from sample_roles import SMALL
from test_samples import GANTRY_SAMPLE


def site_read(config_path, site):
    """(page_trace, SiteData) of one site, read and joined as the build does."""
    config = load_config(config_path)
    roots = config_roots(config)
    sites = load_sites(config, roots)[0]
    catalog = load_catalog(roots, config, sites)
    inputs, reasons = build.read_sites(config, sites, roots=roots)
    assert site in inputs, reasons
    data = inputs[site].data
    return page_trace(build_site_trace(site, data.leaves, catalog), catalog, data), data


# --- Task 1: the sample path --------------------------------------------------------


def test_sample_has_a_path():
    page, data = site_read(GANTRY_SAMPLE, SMALL)
    work = {leaf.key for leaf in scheduled(data)}
    found = {}
    for r in page["requirements"]:
        lit = {k for k, _ in r["light"]}
        into = {(a, b) for a, b in data.links if b in lit and a in work and a not in lit}
        if into:
            found[r["label"]] = into
    assert found == {label: {("Issue-12", "Issue-10")} for label in ("UC-3", "SR-1", "FR-1")}
