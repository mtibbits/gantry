"""The workflow phase rule (#173): checklist steps, by name, to five looks.

Every expected row is a hand-written literal, never computed through
gantry.phases. Checklists go in as text, through read_checklist.
"""

import pytest

from gantry import devagent_records as dr
from gantry import phases
from page_safety import HOSTILE

C, A, N, U = "current", "ahead", "none", "unknown"
D, S, P, B = "done", "stuck", "parked", "blocked"

# The step rows of each checklist template at the pinned devagent release
# (tests/devagent.pin), copied as written; test_devagent_contract ties each
# to its file.
TEMPLATE_STEPS = {
    "checklist-standard.md": """\
- [ ]  0. pull
- [-]  1. research
- [ ]  2. draft
- [-]  3. spike
- [ ]  4. scope
- [ ]  5. improve
- [ ]  6. prune
- [ ]  7. tighten
- [ ]  8. branch
- [ ]  9. implement
- [ ] 10. quality
- [ ] 11. document
- [ ] 12. commit
- [ ] 13. analyze
- [ ] 14. draftmr
- [ ] 15. review
- [ ] 16. redmr
- [ ] 17. preship
- [ ] 18. ship
- [-] 19. mergetoall
- [ ] 20. mergemr
- [ ] 21. updatewbs
- [ ] 22. impact
- [ ] 23. lessonslearned
- [ ] 24. cleanup
""",
    "checklist-perf.md": """\
- [ ]  0. pull
- [-]  1. research
- [ ]  2. draft
- [-]  3. spike
- [ ]  4. scope
- [ ]  5. improve
- [ ]  6. prune
- [ ]  7. tighten
- [ ]  8. branch
- [ ]  9. implement
- [ ] 10. quality
- [ ] 11. document
- [ ] 12. commit
- [ ] 13. analyze
- [ ] 14. draftmr
- [ ] 15. review
- [ ] 16. redmr
- [ ] 17. preship
- [ ] 18. ship
- [-] 19. mergetoall
- [ ] 20. mergemr
- [ ] 22. impact
- [ ] 23. lessonslearned
- [ ] 24. cleanup
""",
    "checklist-oneshot.md": """\
- [ ]  0. pull
- [ ]  9. implement
- [ ] 11. document
- [ ] 23. lessonslearned
- [ ] 24. cleanup
""",
    "checklist-research.md": """\
- [ ]  0. pull
- [ ]  2. draft
- [ ]  4. scope
- [ ]  5. improve
- [ ] 11. document
- [ ] 23. lessonslearned
- [ ] 24. cleanup
""",
    "checklist-docs-only.md": """\
- [ ]  0. pull
- [ ]  2. draft
- [ ]  8. branch
- [ ] 11. document
- [ ] 12. commit
- [ ] 14. draftmr
- [ ] 15. review
- [ ] 17. preship
- [ ] 18. ship
- [-] 19. mergetoall
- [ ] 20. mergemr
- [ ] 24. cleanup
""",
    "revision_block.md": """\
- [ ]  2. draft
- [ ]  4. scope
- [ ]  5. improve
- [ ]  6. prune
- [ ]  7. tighten
- [ ]  8. branch
- [ ]  9. implement
- [ ] 10. quality
- [ ] 11. document
- [ ] 12. commit
- [ ] 13. analyze
- [ ] 14. draftmr
- [ ] 15. review
- [ ] 16. redmr
- [ ] 17. preship
- [ ] 18. ship
- [-] 19. mergetoall
- [ ] 20. mergemr
- [ ] 21. updatewbs
- [ ] 22. impact
- [ ] 23. lessonslearned
- [ ] 24. cleanup
""",
}

# Real active blocks from checklists on the build machine, rows only (names,
# numbers and glyphs; no header, log, project or issue name).
# devagent between #558's first renumbering and mergemr: 20 is updatewbs.
MID_558_BLOCK = """\
- [x]  0. pull
- [-]  1. research
- [P]  2. draft
- [-]  3. spike
- [ ]  4. scope
- [ ]  5. improve
- [ ]  6. prune
- [ ]  7. tighten
- [ ]  8. branch
- [ ]  9. implement
- [ ] 10. quality
- [ ] 11. document
- [ ] 12. commit
- [ ] 13. analyze
- [ ] 14. draftmr
- [ ] 15. review
- [ ] 16. redmr
- [ ] 17. preship
- [ ] 18. ship
- [-] 19. mergetoall
- [ ] 20. updatewbs
- [ ] 21. impact
- [ ] 22. lessonslearned
- [ ] 23. cleanup
"""

# devagent before #558: draft is 1, preship (21) sits between redmr and ship.
PRE_558_BLOCK = """\
- [x]  0. pull
- [x]  1. draft
- [x]  2. scope
- [x]  3. improve
- [x]  4. prune
- [x]  5. tighten
- [x]  6. branch
- [x]  7. implement
- [x]  8. quality
- [x]  9. document
- [x] 10. commit
- [x] 11. analyze
- [x] 12. draftmr
- [x] 13. review
- [x] 14. redmr
- [x] 21. preship
- [x] 15. ship
- [-] 16. mergetoall
- [x] 17. updatewbs
- [x] 18. impact
- [x] 19. lessonslearned
- [x] 20. cleanup
"""

HEADER = "# Issue-1 — Workflow checklist\n\nTemplate: standard\n\n## Revision 1\n\n"


def row(text):
    return phases.checklist_row(dr.read_checklist(text))


def at(rows, number, glyph=" "):
    """`rows` with every step before step `number` done (pre-skips kept),
    that step marked `glyph`, and the later rows as written."""
    out, seen = [], False
    for line in rows.splitlines(keepends=True):
        steps = dr.read_checklist(line).steps
        if not steps:
            out.append(line)
            continue
        step = steps[0]
        if step.number == number and not seen:
            seen = True
            line = line.replace(f"[{step.glyph}]", f"[{glyph}]", 1)
        elif not seen and step.glyph != "-":
            line = line.replace(f"[{step.glyph}]", "[x]", 1)
        out.append(line)
    assert seen, number
    return "".join(out)


def done(rows):
    return "".join(line if line.startswith("- [-]") else "- [x]" + line[5:] for line in rows.splitlines(True))


def test_step_phases_table():
    assert phases.STEP_PHASES == {
        "pull": 0, "research": 0, "draft": 0, "spike": 0, "scope": 0, "improve": 0, "prune": 0,
        "tighten": 0, "branch": 0,
        "implement": 1,
        "quality": 2, "document": 2, "commit": 2, "analyze": 2, "draftmr": 2, "review": 2, "redmr": 2,
        "preship": 2, "ship": 2, "mergetoall": 2,
        "mergemr": 3,
        "updatewbs": 4, "impact": 4, "lessonslearned": 4, "cleanup": 4,
    }
    assert phases.PHASE_NAMES == ("plan", "implement", "check", "merge", "wrap up")


STANDARD = TEMPLATE_STEPS["checklist-standard.md"]
RESEARCH = TEMPLATE_STEPS["checklist-research.md"]


@pytest.mark.parametrize("text, looks", [
    (HEADER + STANDARD, (C, A, A, A, A)),
    (HEADER + at(STANDARD, 9, "~"), (D, C, A, A, A)),
    (HEADER + at(STANDARD, 15, "!"), (D, D, S, A, A)),
    (HEADER + at(STANDARD, 2, "P"), (P, A, A, A, A)),
    (HEADER + at(STANDARD, 20, "?"), (D, D, D, B, A)),
    (HEADER + done(STANDARD), (D, D, D, D, D)),
    (HEADER + at(RESEARCH, 11), (D, N, C, N, A)),
], ids=["fresh-current-space", "tilde", "stuck", "parked", "blocked", "done", "none"])
def test_each_look(text, looks):
    assert row(text) == phases.PhaseRow(looks, None)


def test_each_look_unknown():
    bad = HEADER + at(STANDARD, 9).replace("- [x]  4. scope", "- [o]  4. scope")
    assert row(bad) == phases.PhaseRow((U,) * 5, "checklist.md has a step glyph devagent does not write")


def test_rule_order():
    # check is all `-` and comes before the next step (merge): none, not done.
    text = HEADER + "- [x]  8. branch\n- [x]  9. implement\n- [-] 10. quality\n- [-] 19. mergetoall\n" \
        "- [ ] 20. mergemr\n- [ ] 24. cleanup\n"
    assert row(text) == phases.PhaseRow((D, D, N, C, A), None)


def test_revision_block_phase_1():
    block = "## Revision 2\n\n" + TEMPLATE_STEPS["revision_block.md"]
    assert row(HEADER + "- [x]  0. pull\n\n" + at(block, 4)) == phases.PhaseRow((C, A, A, A, A), None)
    assert row(HEADER + at(block, 9)) == phases.PhaseRow((D, C, A, A, A), None)


def test_pre_skipped_steps():
    assert row(HEADER + at(STANDARD, 18)) == phases.PhaseRow((D, D, C, A, A), None)
    assert row(HEADER + at(STANDARD, 20)) == phases.PhaseRow((D, D, D, C, A), None)


def test_phase_with_no_step_passed_is_dash():
    assert row(HEADER + at(RESEARCH, 11)).looks[1] == N


def test_all_skipped_phase_before_and_after():
    merge_skipped = STANDARD.replace("- [ ] 20. mergemr", "- [-] 20. mergemr")
    assert merge_skipped != STANDARD
    assert row(HEADER + at(merge_skipped, 15)) == phases.PhaseRow((D, D, C, N, A), None)
    assert row(HEADER + at(merge_skipped, 21)) == phases.PhaseRow((D, D, D, N, C), None)


def test_unknown_names():
    why = "step 25 has a name this Gantry does not know"
    # The next step's name is unknown.
    assert row(HEADER + done(STANDARD) + "- [ ] 25. frobnicate\n") == phases.PhaseRow((U,) * 5, why)
    assert row(HEADER + "- [x]  0. pull\n- [x] 25. frobnicate\n- [ ]  2. draft\n") == phases.PhaseRow((U,) * 5, why)
    # A step numbered 25 with a known name is read by its name.
    assert row(HEADER + done(STANDARD).replace("[x] 24. cleanup", "[ ] 25. cleanup")) == \
        phases.PhaseRow((D, D, D, D, C), None)


def test_unknown_name_ahead_of_next_step():
    # A step unknown by name anywhere in the active block makes the row unknown.
    text = HEADER + at(STANDARD, 9) + "- [ ] 25. frobnicate\n"
    assert row(text) == phases.PhaseRow((U,) * 5, "step 25 has a name this Gantry does not know")


def test_real_old_scheme_blocks():
    assert row(HEADER + MID_558_BLOCK) == phases.PhaseRow((P, A, A, N, A), None)
    assert row(HEADER + at(MID_558_BLOCK, 20)) == phases.PhaseRow((D, D, D, N, C), None)
    assert row(HEADER + PRE_558_BLOCK) == phases.PhaseRow((D, D, D, N, D), None)
    reopened = PRE_558_BLOCK.replace("- [x] 21. preship", "- [ ] 21. preship")
    assert reopened != PRE_558_BLOCK
    assert row(HEADER + reopened) == phases.PhaseRow((D, D, C, N, A), None)


TEMPLATE_ROWS = {
    "checklist-standard.md": ((C, A, A, A, A), (D, D, D, D, D)),
    "checklist-perf.md": ((C, A, A, A, A), (D, D, D, D, D)),
    "revision_block.md": ((C, A, A, A, A), (D, D, D, D, D)),
    "checklist-oneshot.md": ((C, A, A, N, A), (D, D, D, N, D)),
    "checklist-research.md": ((C, N, A, N, A), (D, N, D, N, D)),
    "checklist-docs-only.md": ((C, N, A, A, A), (D, N, D, D, D)),
}


def test_templates_fresh_and_done():
    assert sorted(TEMPLATE_ROWS) == sorted(TEMPLATE_STEPS) and len(TEMPLATE_STEPS) == 6
    for name, (fresh, all_done) in TEMPLATE_ROWS.items():
        assert row(HEADER + TEMPLATE_STEPS[name]) == phases.PhaseRow(fresh, None), name
        assert row(HEADER + done(TEMPLATE_STEPS[name])) == phases.PhaseRow(all_done, None), name


def test_bad_glyph_outside_active_block_is_unknown():
    text = "## Revision 1\n\n- [o]  0. pull\n\n## Revision 2\n\n" + TEMPLATE_STEPS["revision_block.md"]
    assert row(text) == phases.PhaseRow((U,) * 5, "checklist.md has a step glyph devagent does not write")


def test_problem_texts():
    assert row("# no steps\n") == phases.PhaseRow((U,) * 5, "checklist.md has no step line")
    assert row(HEADER + STANDARD + "\n## Revision 2\n") == \
        phases.PhaseRow((U,) * 5, "checklist.md has no step after its last revision heading")


def site(status=dr.READ, reason=None, issues=None, skipped=None):
    return dr.SiteRecords(status, reason, issues or {}, skipped or {})


def folder(issue, checklist):
    absent = dr.Record(dr.ABSENT, None, None)
    return dr.IssueRecords(issue, absent, checklist, absent)


def test_leaf_row_causes():
    unread = site(dr.NOT_READ, dr.PLANNING_CANNOT_LIST.format(error="Permission denied"))
    assert phases.leaf_row(unread, "Issue-1") == phases.PhaseRow(
        (U,) * 5, "devagent records not read: planning directory cannot be listed: Permission denied")
    assert phases.leaf_row(unread, None) is None
    linked = site(skipped={"Issue-1": dr.SKIPPED_LINK})
    assert phases.leaf_row(linked, "Issue-1") == phases.PhaseRow((U,) * 5, "issue folder is a link")
    utf8 = dr.Record(dr.UNREADABLE, None, dr.FILE_NOT_UTF8.format(name="checklist.md", offset=3))
    assert phases.leaf_row(site(issues={"Issue-1": folder("Issue-1", utf8)}), "Issue-1") == \
        phases.PhaseRow((U,) * 5, "checklist.md is not valid UTF-8 at byte 3")
    cannot = dr.Record(dr.UNREADABLE, None, dr.FOLDER_CANNOT_READ.format(error="Permission denied"))
    assert phases.leaf_row(site(issues={"Issue-1": folder("Issue-1", cannot)}), "Issue-1") == \
        phases.PhaseRow((U,) * 5, "issue folder cannot be read: Permission denied")
    absent = dr.Record(dr.ABSENT, None, None)
    assert phases.leaf_row(site(issues={"Issue-1": folder("Issue-1", absent)}), "Issue-1") is None
    fresh = dr.Record(dr.PRESENT, dr.read_checklist(HEADER + STANDARD), None)
    readable = site(issues={"Issue-1": folder("Issue-1", fresh)})
    assert phases.leaf_row(readable, "Issue-1") == phases.PhaseRow((C, A, A, A, A), None)
    assert phases.leaf_row(readable, "Issue-2") is None
    assert phases.leaf_row(readable, "issue-1") is None  # the folder name must match exactly
    assert phases.leaf_row(readable, None) is None
    with pytest.raises(TypeError):
        phases.leaf_row(None, "Issue-1")
    with pytest.raises(TypeError):
        phases.leaf_row({"Issue-1": folder("Issue-1", fresh)}, "Issue-1")


def test_no_checklist_text_in_row():
    for hostile in HOSTILE:
        text = f"# {hostile}\n\n## Revision 1\n\n" + at(STANDARD, 9) + f"\n## Log\n- {hostile}\n"
        got = row(text)
        assert got == phases.PhaseRow((D, C, A, A, A), None)
        assert all(look in phases.LOOKS for look in got.looks)


def test_look_words():
    assert phases.LOOKS == ("done", "current", "stuck", "parked", "blocked", "ahead", "none", "unknown")
    assert phases.LOOK_WORDS == {
        "done": "done", "current": "current", "stuck": "stuck", "parked": "parked",
        "blocked": "blocked outside", "ahead": "ahead", "none": "not in this workflow", "unknown": "unknown",
    }
