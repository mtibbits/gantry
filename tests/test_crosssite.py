"""Cross-site depends_on resolution (#68): gantry/crosssite.py and its page."""

from gantry.model import REPORT_KINDS
from gantry.wbs_stopgap import parse_wbs


def test_report_kinds_registered():
    i = REPORT_KINDS.index("malformed_ref")
    assert REPORT_KINDS[i + 1:i + 3] == ("foreign_site_absent", "foreign_unresolved")


def test_leaf_defaults_unresolved():
    data = parse_wbs("- [ ] One {issue: Issue-1, depends_on: [beta:Issue-1]}\n", "alpha")
    (leaf,) = data.leaves
    assert leaf.waits_on_other_site is False
    assert leaf.foreign_targets is None
