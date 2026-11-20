"""The workflow phase rule (#173): a leaf's checklist, by step name, to five looks.

A devagent checklist's active block (gantry.devagent_records) is cut into five
phases: plan, implement, check, merge and wrap up. Steps join phases by
**name**, through a fixed table of the 25 names every devagent numbering
scheme uses; step numbers are never read, because devagent #558 renumbered
them. A name not in the table makes the whole row unknown (fail closed).

Each phase takes the first look that applies: `none` (no step of the phase
in the active block, or every one skipped), `done` (before the next step's
phase, or no next step), the next step's glyph look (its own phase), then
`ahead`. A row is either five known looks with no reason, or five `unknown`
with a reason; the two never mix. No checklist text reaches a row: the
reason holds only fixed texts, a step number, and a record's reason.
"""

from typing import NamedTuple

from gantry import devagent_records as dr

PHASE_NAMES = ("plan", "implement", "check", "merge", "wrap up")
STEP_PHASES = {
    **dict.fromkeys(("pull", "research", "draft", "spike", "scope", "improve", "prune", "tighten", "branch"), 0),
    "implement": 1,
    **dict.fromkeys(("quality", "document", "commit", "analyze", "draftmr", "review", "redmr", "preship", "ship",
                     "mergetoall"), 2),
    "mergemr": 3,
    **dict.fromkeys(("updatewbs", "impact", "lessonslearned", "cleanup"), 4),
}
LOOKS = ("done", "current", "stuck", "parked", "blocked", "ahead", "none", "unknown")
LOOK_WORDS = {"done": "done", "current": "current", "stuck": "stuck", "parked": "parked",
              "blocked": "blocked outside", "ahead": "ahead", "none": "not in this workflow", "unknown": "unknown"}
GLYPH_LOOKS = {" ": "current", "~": "current", "!": "stuck", "P": "parked", "?": "blocked"}

WHY_NOT_READ = "devagent records not read: {reason}"  # SiteRecords.reason
WHY_SKIPPED = "issue folder {reason}"  # a SiteRecords.skipped value, e.g. "is a link"
WHY_NAME = "step {number} has a name this Gantry does not know"
WHY_PROBLEM = {
    dr.CHECKLIST_NO_STEPS: "checklist.md has no step line",
    dr.CHECKLIST_ACTIVE_BLOCK_EMPTY: "checklist.md has no step after its last revision heading",
    dr.CHECKLIST_BAD_GLYPH: "checklist.md has a step glyph devagent does not write",
}


class PhaseRow(NamedTuple):
    looks: tuple[str, ...]  # five members of LOOKS, one per phase
    why: str | None  # set if and only if every look is unknown


def _unknown(why: str) -> PhaseRow:
    return PhaseRow(("unknown",) * len(PHASE_NAMES), why)


def checklist_row(state: dr.ChecklistState) -> PhaseRow:
    """The row of a read checklist."""
    if state.problems:
        return _unknown("; ".join(WHY_PROBLEM[problem] for problem in state.problems))
    unnamed = next((step for step in state.steps if step.name not in STEP_PHASES), None)
    if unnamed is not None:
        return _unknown(WHY_NAME.format(number=unnamed.number))
    nxt = next((step for step in state.steps if step.glyph not in dr.DONE_GLYPHS), None)
    at = STEP_PHASES[nxt.name] if nxt is not None else None
    looks = []
    for phase in range(len(PHASE_NAMES)):
        glyphs = [step.glyph for step in state.steps if STEP_PHASES[step.name] == phase]
        if all(glyph == "-" for glyph in glyphs):  # also true with no step in the phase
            looks.append("none")
        elif at is None or phase < at:
            looks.append("done")
        elif phase == at:
            looks.append(GLYPH_LOOKS[nxt.glyph])
        else:
            looks.append("ahead")
    return PhaseRow(tuple(looks), None)


def leaf_row(records: dr.SiteRecords, issue: str | None) -> PhaseRow | None:
    """The row of the leaf whose `issue:` value is `issue`, from its site's
    records; None when the leaf has no issue value, no folder, or no
    checklist. The folder name must equal `issue` exactly."""
    if not isinstance(records, dr.SiteRecords):
        raise TypeError(f"leaf_row needs SiteRecords, not {type(records).__name__}")
    if issue is None:
        return None
    if records.status != dr.READ:
        return _unknown(WHY_NOT_READ.format(reason=records.reason))
    if issue in records.skipped:
        return _unknown(WHY_SKIPPED.format(reason=records.skipped[issue]))
    folder = records.issues.get(issue)
    if folder is None or folder.checklist.status == dr.ABSENT:
        return None
    if folder.checklist.status != dr.PRESENT:
        return _unknown(folder.checklist.reason)
    return checklist_row(folder.checklist.value)
