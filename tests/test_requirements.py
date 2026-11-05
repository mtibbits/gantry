"""The requirements file format and ID scheme (#15).

docs/requirements-format.md states the rules these tests pin.
"""

import pytest

import gantry.config as cfg
from gantry import requirements as req
from gantry.requirements import LAYERS, PROJECT_LAYERS, ReqId, parse_id

# A fictional mixed-case namespace (operator decision Q3); the format doc's
# case rule uses the same name.
MIXED_NS = "harborWest"


# --- the ID grammar ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "default", "full", "fields"),
    [
        ("gantry:FR-12", None, "gantry:FR-12", ("gantry", "FR", 12)),
        ("life:BR-3", None, "life:BR-3", ("life", "BR", 3)),
        (f"{MIXED_NS}:UC-2", None, f"{MIXED_NS}:UC-2", (MIXED_NS, "UC", 2)),
        ("FR-12", "gantry", "gantry:FR-12", ("gantry", "FR", 12)),
        ("BRule-1", "alpha", "alpha:BRule-1", ("alpha", "BRule", 1)),
        ("alpha:CON-999999999", None, "alpha:CON-999999999", ("alpha", "CON", 999999999)),
    ],
)
def test_parse_id_accepts(text, default, full, fields):
    rid = parse_id(text, default)
    assert isinstance(rid, ReqId)
    assert str(rid) == full
    assert tuple(rid) == fields
    assert type(rid.number) is int


@pytest.mark.parametrize(
    ("text", "default"),
    [
        ("FR-012", "gantry"),
        ("FR-0", "gantry"),
        ("fr-12", "gantry"),
        ("NFR-4", "gantry"),
        ("gantry:FR", None),
        ("gantry:FR-1x", None),
        ("gantry: FR-1", None),
        ("a:b:FR-1", None),
        (":FR-1", "gantry"),
        ("FR-12", None),
        ("FR-1", "a b"),
        ("FR-1234567890", "gantry"),
        ("FR-" + "9" * 5000, "gantry"),
        ("", "gantry"),
    ],
)
def test_parse_id_rejects(text, default):
    assert parse_id(text, default) is None


def test_layers_and_default():
    assert LAYERS == ("BR", "BRule", "UC", "SR", "FR", "QA", "EI", "CON")
    assert PROJECT_LAYERS == set(LAYERS) - {"BR"}
    assert req.DEFAULT_BR_NAMESPACE == "life"
    assert req.DEFAULT_BR_NAMESPACE is cfg.DEFAULT_BR_NAMESPACE
