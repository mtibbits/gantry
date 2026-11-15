"""gantry/devagent_records.py: devagent's per-issue records, read (#31).

STUCK and checklist inputs are the script-written fixtures under
tests/fixtures/devagent_records/written/ (tools/devagent_record_fixtures.py);
each edge case devagent's writers cannot produce is derived from a fixture's
text inside the test, never hand-written. Plan inputs are hand-written to the
plan template at the pin (tests/fixtures/devagent_records/plans/).
"""

from datetime import datetime, timedelta, timezone

import pytest

from gantry import devagent_records as dr
from test_samples import ROOT

FIXTURES = ROOT / "tests" / "fixtures" / "devagent_records"
WRITTEN = FIXTURES / "written"
PLANS = FIXTURES / "plans"


def written(issue, name):
    return (WRITTEN / issue / name).read_bytes().decode("utf-8")


def bom_crlf(text):
    return "﻿" + text.replace("\n", "\r\n")


# --- read_stuck (Task 3) -------------------------------------------------------------------


def test_stuck_sh_fixture():
    got = dr.read_stuck(written("Issue-13", "STUCK"))
    assert got.step == 2 and got.step_name == "draft"
    assert got.reason == "The harbour crane is waiting for a spare part."
    assert got.last_good == "step 0 pull"
    assert got.created == datetime(2026, 1, 12, 9, 30) and got.created.tzinfo is None
    assert got.created_format == "local_minutes"
    assert got.problems == ()


def test_checklist_stuck_sh_fixture():
    text = written("Issue-14", "STUCK")
    got = dr.read_stuck(text)
    assert got.step == 2 and got.step_name == "draft"
    assert got.reason == "The tide table for the north quay is missing."
    assert got.last_good == "0 pull"
    assert got.created == datetime(2026, 1, 12, 9, 30, tzinfo=timezone.utc)
    assert got.created.utcoffset() == timedelta(0)
    assert got.created_format == "iso_offset" and got.problems == ()
    shifted = text.replace("+00:00\n", "+05:30\n")
    assert shifted != text
    other = dr.read_stuck(shifted)
    assert other.created.utcoffset() == timedelta(hours=5, minutes=30)
    assert other.created_format == "iso_offset"


def test_multi_line_reason_keeps_every_line():
    got = dr.read_stuck(written("Issue-25", "STUCK"))
    assert got.reason == "The ferry timetable changed.\nThe second line names the new pier."
    assert got.last_good == "step 0 pull"
    assert got.created == datetime(2026, 1, 12, 9, 30) and got.created.tzinfo is None
    assert got.problems == ()


def test_reason_holding_created_line_takes_last_created():
    got = dr.read_stuck(written("Issue-26", "STUCK"))
    assert got.reason.endswith("\nCreated:     not the key")
    assert got.reason == "The pilot boat is late.\nCreated:     not the key"
    assert got.last_good == "0 pull"
    assert got.created == datetime(2026, 1, 12, 9, 30, tzinfo=timezone.utc)
    assert got.created_format == "iso_offset" and got.problems == ()


def test_no_last_good_line():
    text = written("Issue-13", "STUCK")
    trimmed = "".join(line for line in text.splitlines(keepends=True) if not line.startswith("Last good:"))
    assert trimmed != text
    got = dr.read_stuck(trimmed)
    assert got.last_good is None and got.problems == ()
    assert got.reason == "The harbour crane is waiting for a spare part."
    assert got.created_format == "local_minutes"


def test_last_good_none():
    got = dr.read_stuck(written("Issue-22", "STUCK"))
    assert got.step == 0 and got.step_name == "pull"
    assert got.last_good is None and got.problems == ()


@pytest.mark.parametrize("value", ["soon", "2026-13-40 09:30", "2026-02-30T09:30:00+00:00"])
def test_unparseable_created(value):
    text = written("Issue-13", "STUCK")
    changed = text.replace("Created:     2026-01-12 09:30", "Created:     " + value)
    assert changed != text
    got = dr.read_stuck(changed)
    assert got.created is None and got.created_format == "unparseable"
    assert got.problems == ("stuck_created_unparseable",)
    assert got.reason == "The harbour crane is waiting for a spare part."


@pytest.mark.parametrize("text", ["the crane fell over, call someone\nplease\n", "", "﻿"])
def test_arbitrary_text_reads_with_unknown_step_and_reason(text):
    got = dr.read_stuck(text)
    assert isinstance(got, dr.StuckMarker)
    assert got.step is None and got.step_name is None and got.reason is None
    assert got.created is None and got.created_format == "unparseable"
    assert got.problems == ("stuck_no_step", "stuck_no_reason", "stuck_no_created")


@pytest.mark.parametrize("issue", ["Issue-13", "Issue-14", "Issue-25", "Issue-26"])
def test_stuck_bom_and_crlf_read_like_plain(issue):
    text = written(issue, "STUCK")
    assert dr.read_stuck(bom_crlf(text)) == dr.read_stuck(text)


def test_stuck_overlong_step_number():
    text = written("Issue-13", "STUCK").replace("Step:        2 draft", "Step:        " + "9" * 5000 + " draft")
    got = dr.read_stuck(text)
    assert got.step is None and "stuck_no_step" in got.problems


def test_stuck_line_separators_are_not_line_ends():
    text = written("Issue-13", "STUCK").replace("spare part.", "spare part .")
    got = dr.read_stuck(text)
    assert got.reason == "The harbour crane is waiting for a spare part ."
