"""The workflow phase row fixture page (#173).

A plain module, not a test module. fixture_page() renders 15 leaves, three chains
of five, through page_model and render_model, its model edited so that five boxes
take each fill (crit, near, free) and each fill shows every look. The rows
are hand-written literals, each one a row the phase rule can give.
write_fixture_page(path) writes it for the screenshots.
"""

import copy

import helpers_portfolio as hp
from gantry.views import worksite

D, C, S, P, B, A, N, U = "done", "current", "stuck", "parked", "blocked", "ahead", "none", "unknown"
UNKNOWN_WHY = "checklist.md has a step glyph devagent does not write"
# Reachable rows; together they hold every look.
ROWS = (
    ((D, C, A, N, A), None),
    ((D, S, A, A, A), None),
    ((P, A, A, A, A), None),
    ((D, D, D, B, A), None),
    ((U,) * 5, UNKNOWN_WHY),
)
FILLS = ("crit", "near", "free")


def fixture_model():
    # Three chains of five, one per fill, so the network is five columns wide.
    nodes = [hp.node(f"Leaf {i}", issue=f"Issue-{i}", **({"depends_on": [f"Issue-{i - 1}"]} if i % 5 != 1 else {}))
             for i in range(1, 16)]
    m = copy.deepcopy(worksite.page_model("fixture", hp.site(*nodes, name="fixture"), default_unit_hours=6))
    assert len(m["leaves"]) == 15
    for i, leaf in enumerate(m["leaves"]):
        looks, why = ROWS[i % 5]
        leaf["sclass"] = FILLS[i // 5]
        leaf["wave_full"] = True
        leaf["phases"], leaf["phases_why"] = list(looks), why
    return m


def fixture_page():
    return worksite.render_model(fixture_model(), stamp="")


def write_fixture_page(path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(fixture_page())
