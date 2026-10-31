"""depends_on entries: classification, resolution, rows and cycles (#8)."""

import pytest

from gantry.deps import classify
from gantry.model import DepRef


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("alpha:Issue-1", DepRef("alpha:Issue-1", "local", "alpha", "Issue-1")),
        ("Issue-1", DepRef("Issue-1", "local", "alpha", "Issue-1")),
        ("beta:Issue-1", DepRef("beta:Issue-1", "foreign", "beta", "Issue-1")),
        ("Alpha:Issue-1", DepRef("Alpha:Issue-1", "foreign", "Alpha", "Issue-1")),
        ("beta: Issue-1", DepRef("beta: Issue-1", "malformed", None, None)),
        ("https://x", DepRef("https://x", "malformed", None, None)),
        ("a:b:c", DepRef("a:b:c", "malformed", None, None)),
        (":Issue-1", DepRef(":Issue-1", "malformed", None, None)),
        ("alpha:", DepRef("alpha:", "malformed", None, None)),
    ],
)
def test_classify_table(entry, expected):
    assert classify(entry, "alpha") == expected
