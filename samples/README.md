# Sample worksites

These are fictional inputs for Gantry's tests and demo. Every name in them
is made up: the sites, the owner `sample_owner`, the issues and their
titles. The words devagent and gantry appear only as the names of the two
public tools (in prose, in the `samples/devagent/` folder name and in
`gantry.sample.toml`, which #7 adds).

## Layout

- `devagent/config.toml`: a fictional devagent config. It has a
  `[defaults]` table and one `[project.<site>]` table per site, each with
  an `issue_source` subtable. Every path in it is relative to the file's
  own folder, so `devdoc_dir = "../harbor"` means `samples/harbor/`, and
  no path leads outside `samples/`.
- `devagent/out/`: devagent's portfolio JSON over these samples
  (`portfolio/portfolio.json`) and the devagent home it is written from.
  `tools/sample_portfolio.py` writes it with a devagent checkout; it is
  git-ignored and never committed. `gantry build` reads `WBS.md` directly
  until #12 switches it to this document.
- `gantry.sample.toml`: the sample installation config (#7); its paths are
  relative to `samples/` and its pages go to the ignored `samples/out/`.
- `<site>/`: one planning folder per site, holding its `WBS.md`.
- `harbor/estimates-2026-01-15-effort-and-model-tiers.md`: harbor's
  estimates file, giving each open harbor leaf with an `issue:` an effort
  bucket, an hour range and model tiers. Its `#N` cells name `Issue-N` on harbor; GitHub
  auto-links them to this repo's issues, which is harmless for a fixture.
- `harbor/Issue-N/`: devagent issue folders for eight harbor leaves (#31,
  #173), read by `gantry/devagent_records.py`. Each `checklist.md` and `STUCK` is
  a byte copy of the same-numbered fixture that devagent's own scripts
  wrote (`tests/fixtures/devagent_records/written/`, made with
  `tools/devagent_record_fixtures.py --samples samples`), with fixed
  `Created:` values dated 2026-01-12, before `as_of`; the three
  `imPlan.md` files are hand-written. `Issue-6` is at an in-progress step
  with a check-box plan, one task ticked; `Issue-7` has a plan of `Task N`
  headings and `Issue-8` a numbered one; `Issue-12` is parked; `Issue-13`
  has a STUCK marker in `stuck.sh`'s form and `Issue-14` one in
  `checklist-stuck.sh`'s, both on open leaves; `Issue-15` has a checklist
  and no plan yet; `Issue-16` (#173) is stuck at step 15 `review`, with the
  STUCK marker `stuck.sh` writes there, so the build counts one more stuck
  fire. The leaves of `Issue-7`, `8`, `12`, `13` and `14` stay
  `[ ]` in `WBS.md` although their checklists show progress, deliberately:
  devagent sets `~` only when its WBS update runs, and changing them would
  change harbor's history role and its one in-progress leaf.
- `quarry/Issue-N/`: two hand-written devagent issue folders (#32), each a
  `STUCK` marker and a `checklist.md` in the forms devagent's writers use,
  dated before `as_of`. `Issue-20` has a marker in `stuck.sh`'s form and an
  unparked checklist, and no quarry leaf names it; `Issue-9` (the open leaf
  "Calibrate the weighbridge") has one in `checklist-stuck.sh`'s form and a
  parked checklist. The build counts the first under `stuck_no_open_leaf`
  and the second under `stuck_parked`, so neither is a fire.
- Phase row samples (#173), each on a leaf the worksite network draws, so
  a `--site` build draws its row too: `quarry/Issue-5` (implementing, `~`
  at step 9) and `quarry/Issue-3` (checking, next step 13), each a
  devagent-written checklist like harbor's, and `lighthouse/Issue-9`
  (waiting at `mergemr`, `?` at step 20), devagent-written too.
  `lighthouse/Issue-12/checklist.md` is hand-written with a glyph devagent
  never writes (`- [o]  2. draft`), so its row shows unknown. With harbor's
  folders they show a row mid-plan, implementing, checking, waiting,
  stuck, parked and unknown; quarry `Issue-8`, a drawn leaf with no folder,
  shows none. No `WBS.md` glyph changes.
- `portfolio.md`: the sample installation's portfolio file (#22), named by
  the sample config's `portfolio_file`. Its business requirements are
  `life:BR-1` to `BR-3`; its settings give the four readable sites four
  roles: harbor serves two BRs, orchard serves one, quarry has a share and
  serves none, and lighthouse has neither but holds the milestone leaf
  (`Issue-5`). It has no `[thresholds]` table, so the defaults apply.
- `fires.md`: the sample installation's fires list (#30), named by the
  sample config's `fires_list` and dated against its `as_of`. It holds an
  open fire at a site, one at no site, one old enough to be flagged "still
  a fire?", one with a time (the day before `as_of`), a resolved and a
  promoted entry, and a note under an entry; no unreadable line (those
  are test fixtures). `gantry fires --config samples/gantry.sample.toml`
  checks it; the full build reads it once and collects its fires (#32,
  `docs/daily-screen.md`).
- `lighthouse/requirements.md`: lighthouse's requirements file (#15). The
  sample config's `[site.lighthouse]` table names it, so lighthouse is the
  one sample site in the trace; the other sites are not. `UC-3` (#20)
  needs `SR-1`, which `FR-1` refines, so a leaf naming `FR-1` serves
  `UC-3` indirectly. A lighthouse leaf naming `life:BR-1` in its
  `satisfies:` does not change the portfolio's roles: `serves` is a
  site-level allocation, and lighthouse still has neither.
- `history/`: the sample `WBS.md` history (#28). `plan.toml` lists, per
  site, the dates of earlier versions, each kept as `history/<site>/<date>.md`;
  `tools/sample_history.py` commits them, then each site's current
  `WBS.md` at the plan's `last_date`, into the ignored `history/out/`
  repository. The sample config's `history` keys point harbor, orchard and
  lighthouse into it ([the rules](../docs/wbs-history.md)).

## The hostile title

One open leaf on `lighthouse` has a title holding `<`, `>`, `&` and `"`, so
the demo shows record text rendered as text:

`Render <b>this</b> & "that" as text`

## Sites and roles

| Site | Constant(s) | Role | Shapes it carries |
|---|---|---|---|
| harbor | `BUSY` | well estimated, with model tiers; the busy site | two epics, each with an `issue:` id; a `depends_on` naming a leaf under the other epic; P1 to P4 each present, and leaves with no priority; at least 8 open, 5 finished (`x`), one in-progress (`~`) and one skipped (`-`) `issue:` leaf; one open leaf with no `issue:` and a time `est:`, for the demo week plan; a cross-site entry naming quarry's open `Issue-3` (#62); `est: ?w` on open leaves that have estimates rows; the estimates file, with a row for every open `issue:` leaf, one epic row and one finished row; seven devagent issue folders (`Issue-N/`): an in-progress checklist, check-box, heading and numbered plans, a parked checklist, a STUCK marker in each `Created:` form on an open leaf, and an issue with no plan (#31) |
| orchard | `OWN_UNIT` | `est:` values and a unit of its own | `Nd`, `Nw`, `?w` and the non-time `Npt`; no priority at all; at least three issue-less leaves; a `depends_on` entry that is an outline number (`2.1`) and names no issue; cross-site entries: a row naming an open lighthouse leaf and a site not in the installation, a leaf naming a harbor row, and leaves naming bakery, marsh and an issue lighthouse lacks (#68) |
| quarry | `UNESTIMATED` | wholly unestimated; the P0 demo | no `est:` and no estimates file; priorities on some leaves only; an open P0 leaf and the open leaf it depends on (#10); a `depends_on` naming a leaf under another epic; a `depends_on` naming an issue that exists on no site; `h: 0`, `h: 4` and a quoted `h: "2"` on open leaves, and `h: high`, which its panel lists as `bad_h` (#36); cross-site entries: an open harbor leaf whose id quarry also holds, a finished harbor leaf, a harbor outline position and its own `quarry:` prefix (#68); a `satisfies:` naming lighthouse's `FR-2`, the sample foreign leaf (#21); two hand-written issue folders with STUCK markers, one that no leaf names and one on an open leaf whose checklist is parked (#32) |
| lighthouse | `SMALL`, `HOSTILE_SITE` | small; the trace demo (#20) | two epics and one top-level leaf with no epic; the hostile title on an open leaf; a requirements file, which puts it in the trace, with `UC`, `SR`, `FR`, `QA` and `CON` items, a use case with `needs:` and an `FR` that `refines:` an `SR`; a second epic, "Keep the lamp log", whose leaves carry `satisfies:` keys: a full box naming four IDs, one of another namespace (`life:BR-1`); a circle naming an `FR` that refines the `SR` a use case needs; a schedule-only leaf naming the `SR` directly; finished leaves serving directly and through refinement; an undefined ID (`lighthouse:FR-90`); untraced open leaves; and an open leaf serving only `QA-1` that a leaf serving `FR-1` waits on, so `FR-1`, `SR-1` and `UC-3` each have an upstream leaf (#77). `UC-2` stays unserved installation-wide; `FR-2` is served only by quarry's `Issue-3` (#21); `FR-3` states a `Threshold:` and an `Objective:`, gated by `Issue-8` and served at objective only by the schedule-only `Issue-11` (`objective: lighthouse:FR-3`), so selecting it splits the path box (#79) |
| bakery | `NO_WBS` | no `WBS.md` | a folder holding only a short `README.md` |
| marsh | `UNREADABLE` | cannot be read | ordinary node lines, one of whose titles holds a Latin-1 byte that is not valid UTF-8 |

The sample history gives four sites a role each (`as_of` 2026-01-20, a
four-week window):

| Site | Constant | History role |
|---|---|---|
| harbor | `HISTORY_RECENT` | a glyph change inside the window (2026-01-08) |
| orchard | `HISTORY_STALE` | its last glyph change (2025-11-03) is older than the window |
| lighthouse | `HISTORY_YOUNG` | history that begins inside the window (2026-01-05), with no glyph change since |
| quarry | `HISTORY_LESS` | a `WBS.md` and no `history` key: no data, in no repository |

marsh and bakery have no history either.

Every readable site (harbor, orchard, quarry, lighthouse) has at least one
open and at least three finished `issue:` leaves. Ids are unique within a
site and may repeat across sites: `Issue-7` is open on both harbor and
quarry.

### Cross-site entries

quarry, orchard and harbor carry the eleven cross-site `depends_on` cases
(#68, #62). On
quarry: `harbor:Issue-7` (open on harbor, and quarry has its own open
`Issue-7`), `harbor:Issue-3` (finished there: satisfied), `harbor:1.2` (an
outline position, never an issue id: unresolved) and `quarry:Issue-9` (its own
prefix: a local link). On orchard: the Irrigation row's `lighthouse:Issue-4`
(open) and `ferry:Issue-2`, `harbor:Issue-2` (a row: the box links to its
first open leaf), `bakery:Issue-1` (no `WBS.md`), `marsh:Issue-2` (not valid
UTF-8) and `lighthouse:Issue-40` (lighthouse has no such issue). `ferry` is
not a sample site, so that entry names a site not in this installation.
On harbor: `quarry:Issue-3` on `Issue-6` (open on quarry; the demo week plan
holds `Issue-6` until it finishes, #62).

`marsh/WBS.md` is deliberately invalid UTF-8. Exempt it by its exact path
from UTF-8 text tools, and never re-save it in an editor, which would
re-encode the byte.

## Using the samples

Import a site's role from `tests/sample_roles.py` and never hard-code a
site name. pytest puts `tests/` on the import path by itself; a tool run
outside pytest adds `<repo>/tests` to `sys.path` first.

## Rules for later issues

- Later issues add their own sample files under `samples/`. Generated
  output goes only in `out/` folders, which git ignores and the sample
  checks skip.
- The busy site's rule: a change that adds an open `issue:` leaf to harbor
  adds its estimates row in the same change, or puts the leaf on another
  site.
- The line-ending rule: `.gitattributes` pins sample `.md`, `.toml` and
  `.json` files to `eol=lf`. A sample file of a type those lines do not
  cover adds its own `eol=lf` line, or `-text` if it is binary, above the
  marsh override (`samples/marsh/WBS.md -text`). No line below the
  override may match `samples/marsh/WBS.md`. The sample check fails
  otherwise, on every platform.
- Reserved folder names: no site may be named `devagent`, `out`,
  `history`, `actuals` or `forecast`.
- The history rule: the newest version of each site's sample history is
  its current `WBS.md`, so a change to a sample `WBS.md` reviews
  `samples/history/` in the same change. A glyph edit is a state change at
  the plan's `last_date`, which can change a history role.
