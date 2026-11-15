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


# --- read_checklist (Task 4) ---------------------------------------------------------------


def test_next_step_is_first_not_done_or_skipped():
    got = dr.read_checklist(written("Issue-6", "checklist.md"))
    assert got.next_step == dr.Step(2, "draft", "~") and got.revision == 1
    assert not got.all_done and got.problems == ()
    assert dr.read_checklist(written("Issue-15", "checklist.md")).next_step == dr.Step(0, "pull", " ")


def test_two_revision_blocks_read_the_last():
    text = written("Issue-24", "checklist.md")
    assert text.count("## Revision ") == 2
    got = dr.read_checklist(text)
    assert got.next_step == dr.Step(2, "draft", " ") and got.revision == 2


def test_done_checklist_gives_all_done():
    text = written("Issue-15", "checklist.md")
    assert text.count("- [ ]") > 0
    done = text.replace("- [ ]", "- [x]")
    got = dr.read_checklist(done)
    assert got.next_step is None and got.all_done and got.problems == ()


def test_parked_and_resumed():
    parked = dr.read_checklist(written("Issue-12", "checklist.md"))
    assert parked.parked and parked.parked_step == dr.Step(2, "draft", "P")
    resumed = dr.read_checklist(written("Issue-23", "checklist.md"))
    assert not resumed.parked and resumed.parked_step is None
    assert resumed.next_step.glyph == "~"


def test_parked_only_in_earlier_block():
    got = dr.read_checklist(written("Issue-24", "checklist.md"))
    assert got.parked and got.parked_step == dr.Step(2, "draft", "P")
    assert got.next_step == dr.Step(2, "draft", " ")


def test_stuck_step_follows_the_same_rule():
    assert dr.read_checklist(written("Issue-14", "checklist.md")).stuck_step == dr.Step(2, "draft", "!")
    text = written("Issue-24", "checklist.md")
    assert text.count("- [P]") == 1
    got = dr.read_checklist(text.replace("- [P]", "- [!]"))
    assert got.stuck_step == dr.Step(2, "draft", "!") and not got.parked
    assert dr.read_checklist(written("Issue-15", "checklist.md")).stuck_step is None


def test_shows_progress():
    text = written("Issue-24", "checklist.md")
    later = text[text.rindex("## Revision "):]
    assert "- [x]" in text and "- [x]" not in later and "- [~]" not in later
    assert dr.read_checklist(text).shows_progress
    tilde = text.replace("- [x]", "- [~]")
    assert tilde != text and "- [x]" not in tilde
    assert dr.read_checklist(tilde).shows_progress
    assert not dr.read_checklist(written("Issue-15", "checklist.md")).shows_progress


def test_no_step_lines():
    header = written("Issue-15", "checklist.md").split("## Revision 1")[0]
    got = dr.read_checklist(header)
    assert got.problems == ("checklist_no_steps",) and not got.all_done and got.next_step is None
    empty_block = dr.read_checklist(written("Issue-15", "checklist.md") + "\n## Revision 2\n")
    assert empty_block.problems == ("checklist_active_block_empty",)
    assert empty_block.next_step is None and not empty_block.all_done and empty_block.revision == 2


def test_bad_glyph_reported():
    text = written("Issue-15", "checklist.md")
    changed = text.replace("- [ ]  4. scope", "- [z]  4. scope").replace("- [ ]  5. improve", "- [y]  5. improve")
    assert changed != text
    got = dr.read_checklist(changed)
    assert got.problems == ("checklist_bad_glyph",)


def test_checklist_overlong_numbers():
    text = written("Issue-15", "checklist.md")
    long_step = text.replace("- [ ]  0. pull", "- [ ]  " + "1" * 5000 + ". pull")
    assert dr.read_checklist(long_step).next_step == dr.Step(2, "draft", " ")
    long_rev = text.replace("## Revision 1", "## Revision " + "7" * 5000)
    got = dr.read_checklist(long_rev)
    assert got.revision is None and got.next_step == dr.Step(0, "pull", " ")


def test_checklist_bom_and_crlf_read_like_plain():
    for issue in ("Issue-6", "Issue-12", "Issue-24"):
        text = written(issue, "checklist.md")
        assert dr.read_checklist(bom_crlf(text)) == dr.read_checklist(text)
