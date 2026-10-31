"""Priority: the rank of a priority: value and effective priority (#8)."""

import pytest

from gantry.priority import is_valid, rank

RANK_TABLE = [
    ("P1", 1), ("P2", 2), ("P3", 3), ("P4", 4),
    (None, 3), ("P0", 3), ("P5", 3), ("P7", 3), ("p1", 3), ("[P1]", 3),
    ("P" + "9" * 5000, 3),
    ('"P2"', 2), ("[P1", 1),
]
INVALID = {None, "P0", "P5", "P7", "p1", "[P1]", "P" + "9" * 5000}


@pytest.mark.parametrize(("raw", "expected"), RANK_TABLE)
def test_rank_scale(raw, expected):
    assert rank(raw) == expected
    assert is_valid(raw) is (raw not in INVALID)
