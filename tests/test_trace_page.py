"""The trace on the worksite page (#20): the page's `trace` data, the build's
trace line, the chips and panels, and the selection.

The model tests need no Node; the page-script tests take the `node` fixture.
Sample literals are hand-derived from samples/lighthouse/.
"""

import re

import pytest

from gantry import build
from gantry.build import build_all
from gantry.config import load_config
from gantry.sites import config_roots, load_sites
from gantry.trace import build_site_trace, load_catalog
from gantry.views.worksite import page_model
from helpers_config import sample_build, write_site_tree
from page_safety import assert_page_safe
from sample_roles import SMALL
from test_samples import GANTRY_SAMPLE

# One in-trace site whose FR-1 is served only by Issue-1, held out by its
# cycle with Issue-2.
CYCLE_REQS = "# R\n\n## Functional requirements\n\n- FR-1: Served only by a held-out leaf.\n"
CYCLE = {
    "alpha": ("# Alpha\n\n"
              "- [ ] One {issue: Issue-1, depends_on: [Issue-2], satisfies: alpha:FR-1}\n"
              "- [ ] Two {issue: Issue-2, depends_on: [Issue-1]}\n"
              "- [ ] Three {issue: Issue-3}\n"),
}


def installation(tmp_path, wbs, requirements=None, portfolio=None):
    """An installation in tmp_path: `wbs` maps each site name to its WBS.md
    text, in config order; `requirements` maps a site name to its
    requirements text, which puts it in the trace; `portfolio` is the
    portfolio file's business-requirements text. Returns the config path."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    tables = []
    for name, text in (requirements or {}).items():
        (tmp_path / f"req-{name}.md").write_bytes(text.encode("utf-8"))
        tables.append(f'[site.{name}]\nrequirements = "req-{name}.md"\n')
    raw = {}
    if portfolio is not None:
        (tmp_path / "portfolio.md").write_bytes(("# P\n\n## Business requirements\n\n" + portfolio).encode("utf-8"))
        raw["portfolio_file"] = '"portfolio.md"'
    sites = {name: text.encode("utf-8") for name, text in wbs.items()}
    return write_site_tree(tmp_path, sites, tail="".join(tables), **raw)


def built(config_path):
    """build_all over the installation at config_path."""
    return build_all(load_config(config_path))


def trace_of(page):
    """The page's embedded `trace` data, after the static safety check."""
    return assert_page_safe(page, [])["trace"]


def sample(tmp_path):
    """build_all on a samples copy."""
    return sample_build(tmp_path)


def sample_catalog():
    config = load_config(GANTRY_SAMPLE)
    return load_catalog(config_roots(config), config, load_sites(config)[0])


# --- Task 1: the samples ----------------------------------------------------------


def test_sample_trace_shapes(tmp_path):
    result = sample(tmp_path)
    inputs = result.inputs[SMALL]
    trace = build_site_trace(SMALL, inputs.data.leaves, sample_catalog())
    report = trace.report
    assert report.unserved == ("lighthouse:UC-2", "lighthouse:FR-2")
    assert report.untraced == ("Issue-4", "Issue-5", "Issue-9")
    assert len(report.undefined) == 1 and "lighthouse:FR-90" in report.undefined[0].message
    assert [(s.leaf, s.how) for s in trace.serving["lighthouse:SR-1"]] == [
        ("Issue-3", "direct"), ("Issue-6", "indirect"), ("Issue-7", "indirect"), ("Issue-8", "direct"),
        ("Issue-10", "indirect"), ("Issue-11", "direct")]
    leaves = {leaf["key"]: leaf for leaf in page_model(SMALL, inputs.data, default_unit_hours=6,
                                                      inputs=inputs)["leaves"]}
    assert leaves["Issue-7"]["wave_full"]
    assert leaves["Issue-10"]["linked"] and not leaves["Issue-10"]["wave_full"]
    assert not leaves["Issue-11"]["linked"]

