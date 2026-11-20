"""gantry/devagent_records.py: devagent's per-issue records, read (#31).

STUCK and checklist inputs are the script-written fixtures under
tests/fixtures/devagent_records/written/ (tools/devagent_record_fixtures.py);
each edge case devagent's writers cannot produce is derived from a fixture's
text inside the test, never hand-written. Plan inputs are hand-written to the
plan template at the pin (tests/fixtures/devagent_records/plans/).
"""

import os
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
    return "\ufeff" + text.replace("\n", "\r\n")


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


@pytest.mark.parametrize("text", ["the crane fell over, call someone\nplease\n", "", "\ufeff"])
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
    text = written("Issue-13", "STUCK").replace("spare part.", "spare\u2028part\u2029.")
    got = dr.read_stuck(text)
    assert got.reason == "The harbour crane is waiting for a spare\u2028part\u2029."


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


def test_checklist_steps_are_the_active_block():
    text = (
        "# x\n\n## Revision 1\n\n- [x]  0. pull\n- [ ]  2. draft\n\n"
        "## Revision 2\n\n- [x]  2. draft\n- [-]  3. spike\n- [~]  4. scope\n- [ ]  5. improve\n"
    )
    assert dr.read_checklist(text).steps == (
        dr.Step(2, "draft", "x"),
        dr.Step(3, "spike", "-"),
        dr.Step(4, "scope", "~"),
        dr.Step(5, "improve", " "),
    )


def test_checklist_steps_empty_cases():
    assert dr.read_checklist("# a checklist\n\nno steps here\n").steps == ()
    assert dr.read_checklist("- [x]  0. pull\n- [ ]  2. draft\n\n## Revision 2\n").steps == ()


def test_checklist_steps_keep_bad_glyph():
    got = dr.read_checklist("## Revision 1\n- [x]  0. pull\n- [o]  2. draft\n- [ ]  4. scope\n")
    assert got.steps == (dr.Step(0, "pull", "x"), dr.Step(2, "draft", "o"), dr.Step(4, "scope", " "))
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


# --- read_plan (Task 5) --------------------------------------------------------------------


def plan(name):
    return (PLANS / name).read_bytes().decode("utf-8")


def test_checkbox_plan_first_unticked():
    one = dr.read_plan(plan("checkbox_one_ticked.md"))
    assert one == dr.PlanState("checkbox", "Task 2: Order the crane part", True, ())
    none = dr.read_plan(plan("checkbox_none_ticked.md"))
    assert none == dr.PlanState("checkbox", "Task 1: Survey the north quay", False, ())


def test_checkbox_all_ticked():
    got = dr.read_plan(plan("checkbox_all_ticked.md"))
    assert got.shape == "checkbox" and got.next_task is None and got.marks_done


def test_headings_plan_with_and_without_section():
    inside = dr.read_plan(plan("headings_in_section.md"))
    assert inside == dr.PlanState("headings", "Task 1: Survey the north quay", False, ())
    outside = dr.read_plan(plan("headings_no_section.md"))
    assert outside == dr.PlanState("headings", "Task 1 — Survey the north quay", False, ())


def test_numbered_plan():
    text = plan("numbered.md")
    assert dr.read_plan(text) == dr.PlanState("numbered", "Survey the north quay", False, ())
    paren = text.replace("1. Survey", "1) Survey")
    assert paren != text
    assert dr.read_plan(paren).next_task == "Survey the north quay"


@pytest.mark.parametrize("name, shape, first, marks_done", [
    ("mixed_heading_first.md", "headings", "Task 1: Survey the north quay", False),
    ("mixed_checkbox_first.md", "checkbox", "Task 1: Survey the north quay", False),
    ("mixed_heading_numbered.md", "headings", "Task 1: Survey the north quay", False),
    ("mixed_numbered_first.md", "numbered", "Survey the north quay", False),
])
def test_mixed_section_takes_first_task_shaped_line(name, shape, first, marks_done):
    got = dr.read_plan(plan(name))
    assert (got.shape, got.next_task, got.marks_done) == (shape, first, marks_done)


def test_mixed_heading_first_ignores_ticked_steps():
    text = plan("mixed_heading_first.md")
    assert "- [x] " in text
    assert not dr.read_plan(text).marks_done


def test_prose_section_is_unrecognised():
    got = dr.read_plan(plan("prose_section.md"))
    assert got == dr.PlanState("unrecognised", None, False, ("plan_tasks_unrecognised",))


def test_heading_needs_a_digit():
    assert dr.read_plan(plan("task_word_heading.md")) == dr.PlanState("none", None, False, ())


def test_tasks_section_ended_by_task_heading():
    got = dr.read_plan(plan("tasks_then_level2_headings.md"))
    assert got == dr.PlanState("headings", "Task 1: Survey the north quay", False, ())


def test_no_task_list_is_none():
    assert dr.read_plan(plan("no_tasks.md")) == dr.PlanState("none", None, False, ())
    assert dr.read_plan("") == dr.PlanState("none", None, False, ())


def test_provenance_lines_change_nothing():
    text = plan("dispatched.md")
    assert text.startswith("context: subagent\nmodel: fable\n# ")
    assert dr.read_plan(text) == dr.read_plan(plan("checkbox_one_ticked.md"))


def test_plan_bom_and_crlf_read_like_plain():
    for path in sorted(PLANS.iterdir()):
        text = path.read_bytes().decode("utf-8")
        assert dr.read_plan(bom_crlf(text)) == dr.read_plan(text), path.name
    assert len(list(PLANS.iterdir())) == 15


# --- read_issue_records and read_site_records (Task 6) -------------------------------------

import commit_check  # noqa: E402
import helpers_links  # noqa: E402
from gantry.roots import directory_roots  # noqa: E402

RECORD_NAMES = ("STUCK", "checklist.md", "imPlan.md")
FIELDS = {"STUCK": "stuck", "checklist.md": "checklist", "imPlan.md": "plan"}


def issue_folder(parent, name="Issue-13", files=None):
    """An issue folder holding Issue-13's STUCK and checklist and a check-box plan."""
    folder = parent / name
    folder.mkdir(parents=True)
    files = files if files is not None else {
        "STUCK": (WRITTEN / "Issue-13" / "STUCK").read_bytes(),
        "checklist.md": (WRITTEN / "Issue-13" / "checklist.md").read_bytes(),
        "imPlan.md": (PLANS / "checkbox_one_ticked.md").read_bytes(),
    }
    for file_name, data in files.items():
        (folder / file_name).write_bytes(data)
    return folder


def records_of(got):
    return (got.stuck, got.checklist, got.plan)


def all_reasons(result):
    """Every reason string in an IssueRecords or SiteRecords."""
    if isinstance(result, dr.SiteRecords):
        found = [result.reason, *result.skipped.values()]
        for records in result.issues.values():
            found += all_reasons(records)
        return [r for r in found if r is not None]
    return [r.reason for r in records_of(result) if r.reason is not None]


def assert_no_path(reasons, tmp_path):
    for reason in reasons:
        assert str(tmp_path) not in reason and commit_check.path_form_hits(reason) == [], reason
        assert not reason.endswith(": "), reason


def test_issue_records_present_absent_unreadable(tmp_path):
    plan_dir = tmp_path / "plan"
    folder = issue_folder(plan_dir)
    roots = directory_roots([plan_dir])
    got = dr.read_issue_records(roots, folder)
    assert got.issue == "Issue-13"
    assert got.stuck == dr.Record("present", dr.read_stuck(written("Issue-13", "STUCK")), None)
    assert got.checklist == dr.Record("present", dr.read_checklist(written("Issue-13", "checklist.md")), None)
    assert got.plan == dr.Record("present", dr.read_plan(plan("checkbox_one_ticked.md")), None)
    reasons = []
    for name in RECORD_NAMES:
        data = (folder / name).read_bytes()
        (folder / name).unlink()
        record = getattr(dr.read_issue_records(roots, folder), FIELDS[name])
        assert record == dr.Record("absent", None, None)
        (folder / name).write_bytes(b"ok\n\xff\xfe")
        record = getattr(dr.read_issue_records(roots, folder), FIELDS[name])
        assert record == dr.Record("unreadable", None, f"{name} is not valid UTF-8 at byte 3")
        reasons.append(record.reason)
        (folder / name).unlink()
        (folder / name).mkdir()
        record = getattr(dr.read_issue_records(roots, folder), FIELDS[name])
        assert record == dr.Record("unreadable", None, f"{name} is not a file")
        reasons.append(record.reason)
        (folder / name).rmdir()
        (folder / name).write_bytes(data)
    assert len(reasons) == 6
    assert_no_path(reasons, tmp_path)


def test_issue_records_bom_and_crlf(tmp_path):
    plan_dir = tmp_path / "plan"
    plain = issue_folder(plan_dir, "Issue-13")
    files = {name: bom_crlf((plain / name).read_bytes().decode("utf-8")).encode("utf-8") for name in RECORD_NAMES}
    odd = issue_folder(plan_dir, "Issue-14", files)
    roots = directory_roots([plan_dir])
    a, b = dr.read_issue_records(roots, plain), dr.read_issue_records(roots, odd)
    assert records_of(a) == records_of(b)
    assert b.stuck.status == "present"


def test_text_and_disk_read_alike(tmp_path):
    plan_dir = tmp_path / "plan"
    roots = directory_roots([plan_dir])
    for issue in ("Issue-6", "Issue-12", "Issue-24"):
        data = (WRITTEN / issue / "checklist.md").read_bytes()
        for variant, raw in (("plain", data), ("odd", bom_crlf(data.decode("utf-8")).encode("utf-8"))):
            folder = issue_folder(plan_dir, f"{issue}-{variant}", {"checklist.md": raw})
            got = dr.read_issue_records(roots, folder).checklist
            assert got.status == "present"
            assert got.value == dr.read_checklist(data.decode("utf-8"))


def test_site_records_direct_subfolders_only(tmp_path):
    plan_dir = tmp_path / "plan"
    issue_folder(plan_dir, "Issue-13")
    issue_folder(plan_dir, "ISSUE-x", {"imPlan.md": b"## Tasks\n\n1. One\n"})
    issue_folder(plan_dir, "Captures", {"notes.md": b"none of the three\n"})
    issue_folder(plan_dir / "Captures", "deep", {"STUCK": (WRITTEN / "Issue-13" / "STUCK").read_bytes()})
    (plan_dir / "STUCK").write_bytes(b"a top-level file\n")
    (plan_dir / "WBS.md").write_bytes(b"# WBS\n")
    got = dr.read_site_records(directory_roots([plan_dir]), plan_dir)
    assert got.status == "read" and got.reason is None and got.skipped == {}
    assert list(got.issues) == ["ISSUE-x", "Issue-13"]
    assert len(got.issues) == 2
    assert got.issues["ISSUE-x"].issue == "ISSUE-x"
    assert got.issues["ISSUE-x"].stuck.status == "absent" and got.issues["ISSUE-x"].plan.status == "present"


def test_site_not_read(tmp_path):
    roots = directory_roots([tmp_path])
    missing = dr.read_site_records(roots, tmp_path / "nowhere")
    assert missing == dr.SiteRecords("not_read", "planning directory does not exist", {}, {})
    (tmp_path / "file").write_bytes(b"x")
    a_file = dr.read_site_records(roots, tmp_path / "file")
    assert a_file == dr.SiteRecords("not_read", "planning directory is not a directory", {}, {})


def test_link_subfolder_is_skipped(tmp_path):
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    outside = issue_folder(tmp_path / "elsewhere", "Issue-99")
    helpers_links.link_dir(outside, plan_dir / "Issue-99")
    issue_folder(plan_dir, "Issue-13")
    got = dr.read_site_records(directory_roots([plan_dir]), plan_dir)
    assert got.skipped == {"Issue-99": "is a link"}
    assert list(got.issues) == ["Issue-13"]


@pytest.mark.parametrize("name", RECORD_NAMES)
def test_link_record_file_is_unreadable(tmp_path, name):
    plan_dir = tmp_path / "plan"
    folder = issue_folder(plan_dir, files={})
    target = tmp_path / "target"
    target.write_bytes(b"Step:        2 SENTINEL-NEVER-READ\nReason:      SENTINEL-NEVER-READ\n")
    helpers_links.symlink(target, folder / name)
    got = dr.read_issue_records(directory_roots([plan_dir]), folder)
    record = getattr(got, FIELDS[name])
    assert record == dr.Record("unreadable", None, f"{name} is a link")
    assert "SENTINEL" not in repr(got)


def test_path_outside_roots_is_reported_not_raised(tmp_path):
    inside = tmp_path / "inside"
    inside.mkdir()
    folder = issue_folder(tmp_path / "elsewhere")
    roots = directory_roots([inside])
    got = dr.read_issue_records(roots, folder)
    for record in records_of(got):
        assert record.status == "unreadable" and record.value is None
        assert record.reason.startswith("issue folder cannot be read: ")
    site = dr.read_site_records(roots, tmp_path / "elsewhere")
    assert site.status == "not_read" and site.reason.startswith("planning directory cannot be listed: ")
    assert_no_path(all_reasons(got) + all_reasons(site), tmp_path)


def test_issue_folder_states(tmp_path):
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    roots = directory_roots([plan_dir])
    (plan_dir / "Issue-2").write_bytes(b"a file\n")
    cases = [(plan_dir / "Issue-1", "issue folder does not exist"),
             (plan_dir / "Issue-2", "issue folder is not a directory")]
    for path, reason in cases:
        got = dr.read_issue_records(roots, path)
        assert {(r.status, r.value, r.reason) for r in records_of(got)} == {("unreadable", None, reason)}
    target = issue_folder(plan_dir, "Issue-real")
    helpers_links.link_dir(target, plan_dir / "Issue-3")
    got = dr.read_issue_records(roots, plan_dir / "Issue-3")
    assert {(r.status, r.reason) for r in records_of(got)} == {("unreadable", "issue folder is a link")}


def test_issue_folder_that_is_a_root_reads(tmp_path):
    folder = issue_folder(tmp_path / "plan")
    got = dr.read_issue_records(directory_roots([folder]), folder)
    assert [r.status for r in records_of(got)] == ["present"] * 3


def test_empty_error_reason_has_fallback(tmp_path, monkeypatch):
    plan_dir = tmp_path / "plan"
    folder = issue_folder(plan_dir)

    def refuse(*args, **kwargs):
        raise OSError()

    monkeypatch.setattr(dr, "read_bytes_confined", refuse)
    got = dr.read_issue_records(directory_roots([plan_dir]), folder)
    assert got.checklist == dr.Record("unreadable", None, "checklist.md cannot be read: unknown error")
    monkeypatch.setattr(dr, "list_confined", refuse)
    site = dr.read_site_records(directory_roots([plan_dir]), plan_dir)
    assert site.reason == "planning directory cannot be listed: unknown error"
    lone = dr.read_issue_records(directory_roots([plan_dir]), folder)
    assert {r.reason for r in records_of(lone)} == {"issue folder cannot be read: unknown error"}
    assert_no_path(all_reasons(got) + all_reasons(site) + all_reasons(lone), tmp_path)


def test_roots_marks_settled():
    text = (ROOT / "gantry" / "roots.py").read_bytes().decode("utf-8")
    assert "provisional for #31" not in text and "for #31" not in text
    assert text.count("gantry/devagent_records.py") == 2


def test_readers_never_raise_on_bytes(tmp_path):
    plan_dir = tmp_path / "plan"
    long_text = ("- [ ]  " + "4" * 5000 + ". pull\n## Revision " + "9" * 5000 + "\nStep:        "
                 + "8" * 5000 + " x\n")
    files = {"STUCK": b"\x00\x00Step: \x00\n", "checklist.md": b"", "imPlan.md": long_text.encode("utf-8")}
    folder = issue_folder(plan_dir, files=files)
    got = dr.read_issue_records(directory_roots([plan_dir]), folder)
    assert [r.status for r in records_of(got)] == ["present"] * 3
    for name in RECORD_NAMES:
        (folder / name).write_bytes(long_text.encode("utf-8"))
    again = dr.read_issue_records(directory_roots([plan_dir]), folder)
    assert [r.status for r in records_of(again)] == ["present"] * 3
    assert again.checklist.value.revision is None and again.stuck.value.step is None


# --- verbatim text and the single-reader rule (Task 7) -------------------------------------

import ast  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402

from page_safety import HOSTILE  # noqa: E402
from test_source_scan import package_modules  # noqa: E402

RECORD_NAME_RE = re.compile(r"(?:^|[\\/])(?:STUCK|checklist\.md|imPlan\.md)$")


def test_hostile_text_round_trips(tmp_path):
    stuck = written("Issue-13", "STUCK")
    checklist = written("Issue-6", "checklist.md")
    assert len(HOSTILE) >= 10
    for value in HOSTILE:
        reason = stuck.replace("The harbour crane is waiting for a spare part.", value)
        assert reason != stuck and dr.read_stuck(reason).reason == value
        step = stuck.replace("Step:        2 draft", "Step:        2 " + value)
        assert step != stuck and dr.read_stuck(step).step_name == value
        box = dr.read_plan("## Tasks\n\n- [ ] " + value + "\n- [ ] Task 2\n")
        assert (box.shape, box.next_task) == ("checkbox", value)
        heading = dr.read_plan("## Tasks\n\n### Task 1: " + value + "\n\nText.\n")
        assert (heading.shape, heading.next_task) == ("headings", "Task 1: " + value)
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    planted = []
    for value in HOSTILE:
        try:
            (plan_dir / value).mkdir()
        except (OSError, ValueError):
            continue
        if (plan_dir / value).name != value:
            continue
        (plan_dir / value / "checklist.md").write_bytes(checklist.encode("utf-8"))
        planted.append(value)
    assert len(planted) >= 4
    got = dr.read_site_records(directory_roots([plan_dir]), plan_dir)
    assert sorted(got.issues) == sorted(planted)
    for value in planted:
        assert got.issues[value].issue == value
        assert got.issues[value].checklist.value.next_step == dr.Step(2, "draft", "~")
    # devagent's grammar takes the name as one token, so markup after it is not part of it.
    row = checklist.replace("- [~]  2. draft", "- [~]  2. draft<script>")
    assert row != checklist
    assert dr.read_checklist(row).next_step == dr.Step(2, "draft", "~")


def record_name_hits(pkg_dir):
    """({module relpath with a record-file-name string constant}, modules scanned)."""
    modules = package_modules(pkg_dir)
    hits = set()
    for rel, tree in modules:
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and RECORD_NAME_RE.search(node.value):
                hits.add(rel)
    return hits, len(modules)


def test_only_the_adapter_names_record_files():
    hits, scanned = record_name_hits(ROOT / "gantry")
    assert scanned >= 20
    assert hits == {"devagent_records.py"}


def test_single_reader_scan_planted(tmp_path):
    copy = tmp_path / "gantry"
    shutil.copytree(ROOT / "gantry", copy, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / "named.py").write_bytes(b'WHERE = "plans/" + "x"\nNAME = "checklist.md"\n')
    (copy / "identifier.py").write_bytes(b"STUCK_LINE = 1\nTEXT = 'a STUCK marker'\n")
    hits, _ = record_name_hits(copy)
    assert hits == {"devagent_records.py", "named.py"}


# --- sample issue folders on harbor (Task 9) -----------------------------------------------

SAMPLE_CONFIG = ROOT / "samples" / "gantry.sample.toml"
HARBOR = ROOT / "samples" / "harbor"
SAMPLE_ISSUES = ("Issue-12", "Issue-13", "Issue-14", "Issue-15", "Issue-16", "Issue-6", "Issue-7", "Issue-8")
# Sample issue folders written by hand, not by a recipe: (site, folder).
HAND_WRITTEN = {("quarry", "Issue-9"), ("quarry", "Issue-20"), ("lighthouse", "Issue-12")}


def sample_site(name):
    from gantry.config import load_config
    from gantry.sites import config_roots, load_sites

    config = load_config(SAMPLE_CONFIG)
    roots = config_roots(config)
    sites, _ = load_sites(config, roots)
    return config, roots, next(s for s in sites if s.name == name)


def test_sample_records_are_fixture_copies():
    from devagent_record_fixtures import RECIPES

    copies = sorted(p for p in (ROOT / "samples").glob("*/Issue-*/*")
                    if p.is_file() and p.name in ("STUCK", "checklist.md"))
    recipes = {(r.site, r.issue) for r in RECIPES if r.sample}
    assert {(p.parent.parent.name, p.parent.name) for p in copies} == recipes | HAND_WRITTEN
    compared = 0
    for path in copies:
        site, issue = path.parent.parent.name, path.parent.name
        if (site, issue) in HAND_WRITTEN:
            continue
        assert path.read_bytes() == (WRITTEN / issue / path.name).read_bytes(), path
        compared += 1
    assert compared == 14  # harbor's eight folders (11 files), quarry's two and lighthouse's one
    assert sorted(r.issue for r in RECIPES if r.sample and r.site == "harbor") == sorted(SAMPLE_ISSUES)


def test_sample_site_records_shapes():
    config, roots, site = sample_site("harbor")
    got = dr.read_site_records(roots, site.planning_dir, site="harbor")
    assert got.status == "read" and got.skipped == {}
    assert sorted(got.issues) == sorted(SAMPLE_ISSUES)
    issues = got.issues
    assert issues["Issue-6"].checklist.value.next_step.glyph == "~"
    assert (issues["Issue-6"].plan.value.shape, issues["Issue-6"].plan.value.marks_done) == ("checkbox", True)
    assert issues["Issue-7"].plan.value.shape == "headings"
    assert issues["Issue-8"].plan.value.shape == "numbered"
    assert issues["Issue-12"].checklist.value.parked
    assert issues["Issue-13"].stuck.value.created_format == "local_minutes"
    assert issues["Issue-14"].stuck.value.created_format == "iso_offset"
    assert issues["Issue-15"].plan.status == "absent" and issues["Issue-12"].plan.status == "absent"
    stucks = [r.stuck.value for r in issues.values() if r.stuck.status == "present"]
    assert len(stucks) == 3
    for marker in stucks:
        assert marker.created.date() < config.as_of
    for records in issues.values():
        for record in (records.stuck, records.checklist, records.plan):
            assert record.status in ("present", "absent") and record.reason is None
            assert record.status == "absent" or record.value.problems == ()


def test_sample_stuck_folders_name_open_leaves():
    import helpers_portfolio as hp

    data = hp.sample_site(HARBOR.name)
    open_issues = {leaf.issue for leaf in data.leaves if leaf.open and leaf.issue}
    stuck = {p.parent.name for p in HARBOR.glob("*/STUCK")}
    assert stuck == {"Issue-13", "Issue-14", "Issue-16"}
    assert stuck <= open_issues


# --- the doc names every problem and reason (Task 10) --------------------------------------

DOC = ROOT / "docs" / "devagent-formats.md"


def test_doc_names_every_problem_and_reason():
    text = DOC.read_bytes().decode("utf-8")
    assert len(dr.PROBLEM_KINDS) == 8
    assert [kind for kind in dr.PROBLEM_KINDS if f"`{kind}`" not in text] == []
    shown = [r.format(name="<name>", error="<error>", offset="<offset>") for r in dr.REASONS]
    assert len(shown) == 12
    assert [reason for reason in shown if f"`{reason}`" not in text] == []
    for word in (dr.PRESENT, dr.ABSENT, dr.UNREADABLE, dr.READ, dr.NOT_READ, dr.UNKNOWN_ERROR,
                 dr.LOCAL_MINUTES, dr.ISO_OFFSET, dr.UNPARSEABLE, dr.CHECKBOX, dr.HEADINGS, dr.NUMBERED,
                 dr.UNRECOGNISED, dr.NONE):
        assert f"`{word}`" in text, word


def test_module_docstring_names_its_callers():
    for issue in ("#32", "#33", "#29"):
        assert issue in dr.__doc__


# --- final review fixes ----------------------------------------------------------------------


def test_listing_race_is_retried_once(tmp_path, monkeypatch):
    """devagent renames temp files inside an issue folder on every mark; an
    entry that vanishes between scandir and lstat must not read as a missing
    folder."""
    plan_dir = tmp_path / "plan"
    folder = issue_folder(plan_dir)
    real = dr.list_confined
    failed = set()

    def flaky(roots, path, **kwargs):
        if str(path) not in failed:
            failed.add(str(path))
            raise FileNotFoundError(2, "No such file or directory")
        return real(roots, path, **kwargs)

    monkeypatch.setattr(dr, "list_confined", flaky)
    roots = directory_roots([plan_dir])
    site = dr.read_site_records(roots, plan_dir)
    assert site.status == "read" and list(site.issues) == ["Issue-13"]
    assert site.issues["Issue-13"].stuck.status == "present"
    failed.clear()
    lone = dr.read_issue_records(roots, folder)
    assert [r.status for r in records_of(lone)] == ["present"] * 3
    # A folder that is really gone still reads as missing.
    assert dr.read_site_records(roots, plan_dir / "nowhere").reason == "planning directory does not exist"


def test_record_deleted_after_listing_reads_absent(tmp_path, monkeypatch):
    """devagent's unstuck deletes STUCK; one deleted between the listing and
    the read is absent, not unreadable, so no fire shows for it."""
    plan_dir = tmp_path / "plan"
    folder = issue_folder(plan_dir)
    real = dr.read_bytes_confined

    def unstuck_first(roots, path, **kwargs):
        if os.path.basename(path) == "STUCK" and os.path.exists(path):
            os.remove(path)
        return real(roots, path, **kwargs)

    monkeypatch.setattr(dr, "read_bytes_confined", unstuck_first)
    got = dr.read_issue_records(directory_roots([plan_dir]), folder)
    assert got.stuck == dr.Record("absent", None, None)
    assert got.checklist.status == "present" and got.plan.status == "present"
    # Another read error stays unreadable.
    def denied(roots, path, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(dr, "read_bytes_confined", denied)
    got = dr.read_issue_records(directory_roots([plan_dir]), folder)
    assert got.checklist == dr.Record("unreadable", None, "checklist.md cannot be read: Permission denied")
