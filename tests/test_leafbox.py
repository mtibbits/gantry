"""The shared leaf box rule (#170): one cut, wrap, band and class rule for the
worksite network's boxes and the week view's placements.

Every expected value is a hand-worked literal (found with the worksite's
_cut/_wrap at 0f801f4), never computed through leafbox.
"""

import pytest

from gantry.model import Models
from gantry.views import leafbox


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
