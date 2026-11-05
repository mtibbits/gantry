"""Optional inputs joined onto a site: gantry/inputs.py (#10)."""

from dataclasses import fields

from gantry.model import Estimate, Models, Totals


def test_value_type_fields():
    assert [f.name for f in fields(Estimate)] == ["lo_h", "hi_h", "source", "est_kind", "effort"]
    assert [f.name for f in fields(Models)] == ["thinking", "checking", "source"]
    assert [f.name for f in fields(Totals)] == ["hours", "hours_lo", "hours_hi", "unestimated_count", "non_time"]
