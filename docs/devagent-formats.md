# devagent's per-issue records

devagent keeps a folder per issue in each site's planning directory, holding
that issue's STUCK marker, checklist and plan. Gantry reads them to find
fires, parked issues and a line of context per planned task. These are
devagent's internal formats, which devagent owns and may change, so one
module, `gantry/devagent_records.py`, is the only Gantry code that reads
them. This page lists every format it depends on, the devagent source lines
each mirrors, what it does with each malformed case, and how a contract test
notices when devagent's writers change shape.

One format on this page is not a per-issue record: the capacity declaration
in devagent's config, which a module of its own reads for the week plan
([The capacity declaration](#the-capacity-declaration)).

**A planning directory is private data.** The records in this repository
(`samples/<site>/Issue-N/`, `tests/fixtures/devagent_records/` and the week
view fixture's issue folders) are fictional, or template step rows (names
and glyphs only) taken from real checklists, as the phase rule's old-scheme
test blocks are.

The full build reads each site's records once, before its pages: the
pages draw each leaf's workflow phase row from its checklist
([worksite-page.md](worksite-page.md#the-phase-row), #173), the fire
collector (#32) reads the STUCK markers and checklists from the same read
([daily-screen.md](daily-screen.md#stuck-markers)), and the warm-up list
(#33) will use the same result; the time thieves (#29) read each WIP
leaf's checklist from it. A `--site` build
reads its one site's records for its rows.

## The pinned release

Gantry depends on devagent's formats as of one release, named in
`tests/devagent.pin`: today `devagent--v3.4.0`, whose
`.claude-plugin/plugin.json` version is `3.4.0`, the oldest devagent the
README supports. Every source line cited on this page is a line of that
release. Moving the pin is a change of its own, made with every file that
records the pin regenerated
([Fixtures and the contract test](#fixtures-and-the-contract-test)). A test
that needs a newer devagent contract than this release adds a second pin
for its own checkout, as gantry 75f33bc (#39) did.

## Where the records are

devagent keeps an issue's records in `<planning directory>/<issue>/`
(`scripts/lib/config.sh:495-500`), where the planning directory is the
site's `devdoc_dir` and `<issue>` is a leaf's `issue:` value. An **issue
folder** may hold three records: `STUCK`, `checklist.md` and `imPlan.md`.

`read_site_records(roots, planning_dir, *, site=None)` lists the planning
directory's direct subfolders and reads each one that holds at least one of
the three, keyed by its name exactly as on disk. It does not descend further,
so captures and other folders holding none of the three are left out. A
subfolder that cannot be listed is kept, with all three records
`unreadable`: it is not known to hold none of them, so the reader fails
closed.
`read_issue_records(roots, issue_dir, *, site=None)` reads one folder. Both
read through the run's roots under the `devdoc_dir` key
([config reference](config.md#where-a-build-may-read-and-write)).

### Links

**The record reader follows no link at all.** A subfolder of the planning
directory that is a link (a symlink or a Windows junction) is not read and
is listed in `skipped` with the reason `is a link`, as is any other link in
it that is not a link to a file inside the roots (a dangling link, or a
link out of the roots). A record file that is a
link is `unreadable` with `<name> is a link`, and its target is never
opened. An issue folder given to `read_issue_records` that is itself a link
gives `issue folder is a link`. This is stricter than the rest of Gantry,
which follows a link that stays inside the roots.

### What only this build machine sees

Gantry reads whatever this build machine's copy of the planning directory
holds. devagent's STUCK writers commit nothing, and `scripts/unstuck.sh:66`
and `scripts/checklist-unstuck.sh:42` delete the file. devagent's cleanup
stages and commits only the paths it owns: the folder of the issue it
cleans up, and `WBS.md` when that has changed (`scripts/cleanup.sh:297`,
`scripts/lib/devdoc-repo.sh:59-72`). So a STUCK marker or checklist change
reaches the planning repository only at the cleanup of its own issue, on
the machine that wrote it; the cleanup of another issue leaves it out. A
STUCK marker written on another machine is invisible here until then, and
devagent never commits one that is deleted before that cleanup. How such
records reach the build machine is outside Gantry. `/devagent:status`
reads a STUCK marker only for each project's active issue, from its own
machine's state (`scripts/status.sh:28-51`); Gantry reads every issue
folder.

## STUCK markers

devagent writes `STUCK` when a step cannot go on. Two writers write two
shapes; both mark the step `!` in the checklist first and put `Created:`
last.

`scripts/stuck.sh` marks the step (`scripts/stuck.sh:59`) and writes
(`scripts/stuck.sh:61-69`):

```
Step:        2 draft
Reason:      The harbour crane is waiting for a spare part.
Last good:   step 0 pull
Created:     2026-01-12 09:30
```

`Created:` is local time to the minute with no zone. `Last good:` names the
last done step before the current one, prefixed `step`. At the pin this
writer cannot write a marker without a `Last good:` line: with no done step
before the current one its `last_good` variable is never set
(`scripts/stuck.sh:41`), and the test of it (`scripts/stuck.sh:55`) stops
the script under `set -u` before anything is written.

`scripts/checklist-stuck.sh` marks the step
(`scripts/checklist-stuck.sh:66-70`) and writes
(`scripts/checklist-stuck.sh:72-77`):

```
Step:        2 draft
Reason:      The tide table for the north quay is missing.
Last good:   0 pull
Created:     2026-01-12T09:30:00+00:00
```

`Created:` comes from `date -Iseconds`, with a UTC offset. `Last good:` is
`N name`, or `(none)` with no done step before the current one.

**A reason can run over several lines.** Both writers write the reason as
given. Given a reason of more than one line, each writes the whole file and
marks the step, then exits 1 when its log refuses the message
(`scripts/lib/log.sh:17-19`); the marker stays.

### How `read_stuck` reads a marker

`read_stuck(text)` never raises. It drops trailing blank lines and reads the
**trailer** from the end: the last line, when it is a `Created:` line, is the
key; then the line now last, when it is a `Last good:` line, is the key.
`Step:` is the first line, when it is one. The reason is the first `Reason:`
line left and every line after it, joined with a newline and kept as
written, so a line starting `Created:` or `Last good:` inside a reason is
reason text. Keys are matched with any spaces or tabs after the colon.

It returns `step` (the number), `step_name` (the rest of the `Step:` line, as
written), `reason`, `last_good` (as written, without its key; `None` for no
line and for `(none)`), `created`, `created_format` and `problems`:

| `Created:` value | `created` | `created_format` |
|---|---|---|
| `YYYY-MM-DD HH:MM` | a naive datetime; Gantry never applies a zone, the collector reads it in the build machine's zone | `local_minutes` |
| `YYYY-MM-DDTHH:MM:SS±HH:MM` | an offset-aware datetime | `iso_offset` |
| anything else, a date that does not exist, or no `Created:` line | `None` | `unparseable` |

Problem kinds, each at most once: `stuck_no_step` (no first `Step:` line with
a number of at most nine digits and a name), `stuck_no_reason`,
`stuck_no_created` and `stuck_created_unparseable`. A marker whose text
cannot be parsed at all still reads as present, with its step and reason
`None`: the file's existence is the signal, and the collector fails closed.

**The trailer limit.** A reason whose last line starts `Last good:`, in a
marker with no `Last good:` line of its own, gives that line as the last good
step. Neither writer can produce such a marker, since both always write
`Last good:` before `Created:`.

## Checklists

A step line matches devagent's pattern
`^- \[(.)\][[:space:]]+([0-9]+)\.[[:space:]]+([A-Za-z][A-Za-z0-9_-]*)`
(`scripts/lib/checklist.sh:68`); the name is that token only, so text after
it is not part of the name. The seven glyphs are space (pending), `x` (done),
`-` (skipped), `!` (stuck), `~` (in progress), `?` (blocked outside) and `P`
(parked) (`scripts/lib/checklist.sh:326-331`).

A checklist holds one block per **revision**. The **active block** is every
line after the last line starting `## Revision `, or the whole file with
none (`scripts/lib/checklist.sh:77-79`). Revision blocks reuse step numbers.
The `Active revision:` header is not read.

`read_checklist(text)` never raises and returns:

- `next_step`: the first step in the active block whose glyph is not `x` or
  `-`, as `Step(number, name, glyph)` (`scripts/lib/checklist.sh:133-155`
  calls it the current step); `None` with `all_done` true when every step
  there is done or skipped and there is at least one.
- `parked_step`: the first `P` step in the active block, else the first in
  the file, else `None` (`scripts/lib/checklist.sh:382-399`, the rule
  devagent's resume uses at `scripts/resume.sh:53-56`); `parked` is
  `parked_step is not None`. `scripts/park.sh:63` marks the current step `P`
  (and `scripts/park.sh:68` records the park in the machine's own state,
  which Gantry does not read).
- `stuck_step`: the first `!` step, by the same rule.
- `shows_progress`: true when any step in the whole file is `x` or `~`, the
  rule devagent's WBS update uses to set a leaf to `~`
  (`scripts/lib/wbs-tracker.py:171`, `scripts/lib/wbs-tracker.py:200`).
- `revision`: the number in the last `## Revision ` heading, or `None` with
  no heading or a number of more than nine digits.
- `problems`: `checklist_no_steps` (no step line in the file; `all_done`
  false), `checklist_active_block_empty` (steps exist but none after the
  last heading, where devagent itself stops; `next_step` `None`, `all_done`
  false) and `checklist_bad_glyph` (a step glyph outside the seven,
  reported once).
- `steps`: every step of the active block, in file order, as `Step`s, a
  step with a bad glyph included; `()` when the active block has none.

The same function reads a working-copy file and a committed version passed
in as text: the time thieves (#29) read each WIP leaf's committed checklist
through it.

## Plans

devagent's plan template puts tasks under `## Tasks` as `- [ ] Task N` lines
(`templates/imPlan_template.md:30-33`), and a dispatched plan carries two
provenance lines (`context:` and `model:`) above its H1
(`templates/imPlan_template.md:1-2`). Real plans also use `Task N` headings
and numbered lists.

`read_plan(text)` never raises. It takes the `## Tasks` section, from the
first `## Tasks` line to the next line starting `## `, and looks at lines
starting in column 0 of three **task shapes**: a check box (`- [ ] text`,
ticked by `x` or `X`), a `Task N` heading (`##` to `####`, then `Task`, space
and a digit, so `### Task ordering` is not one) and a numbered item (`1.` or
`1)`). **The first task-shaped line in the section fixes the shape**, and
only lines of that shape are tasks: check-box steps under `Task N` headings
are not tasks, and neither are numbered steps under them.

| `shape` | When | `next_task` | `marks_done` |
|---|---|---|---|
| `checkbox` | the first task line is a check box | the first unticked line's text, or `None` when all are ticked | true when any line is ticked |
| `headings` | the first task line is a `Task N` heading; or no `## Tasks` section, or one with no task line that a level-2 `## Task N` heading ends, and a `Task N` heading anywhere | the first heading's text | false: these plans do not mark tasks done |
| `numbered` | the first task line is a numbered item | the first item's text | false |
| `unrecognised` | a `## Tasks` section with no task line, problem `plan_tasks_unrecognised` | `None` | false |
| `none` | no `## Tasks` section and no `Task N` heading | `None` | false |

Task text is one line, kept as written; trimming for display is the page's
job. The provenance lines need no special case. **The fence limit:** code
fences are not interpreted, so a task-shaped line inside a fenced block in
the section counts.

## Text rules

Every reader takes text. A leading byte-order mark is dropped, lines are
split on line feed only, and one carriage return at a line's end is dropped,
so a CRLF file reads like its LF form. U+2028, U+2029 and other characters
are kept inside a line. Every digit run read as a number is at most nine
ASCII digits; a longer one is never an error. In a STUCK `Step:` line it
gives `stuck_no_step`; a checklist row or `## Revision` heading holding one
is not read as a step or a revision, with no problem recorded. Reasons, step
names, task text and folder names are returned byte for byte: escaping
happens once, where a page renders them.

## Files and reasons

`read_issue_records` returns the folder's name and a `Record` per file, with
`status` `present` (and the parsed value), `absent`, or `unreadable` (and a
reason). A STUCK marker with unparseable text is still `present`. No reason
holds a path:

| Case | Reason |
|---|---|
| a record file that is a link | `<name> is a link` |
| a record file that is not a regular file | `<name> is not a file` |
| a record file that cannot be read | `<name> cannot be read: <error>` |
| a record file deleted between the listing and the read | none: it is `absent` |
| a record file that is not UTF-8 | `<name> is not valid UTF-8 at byte <offset>` |
| the issue folder is missing (all three records) | `issue folder does not exist` |
| the issue folder is not a directory | `issue folder is not a directory` |
| the issue folder is a link | `issue folder is a link` |
| the issue folder cannot be listed, or is outside the roots | `issue folder cannot be read: <error>` |

`<error>` is the operating system's own text, or the roots refusal, or
`unknown error` when there is none. `read_site_records` returns `status`
`read`, or `not_read` with a reason, the records by folder name, and
`skipped`:

| Case | Reason |
|---|---|
| the planning directory is missing | `planning directory does not exist` |
| the planning path is not a directory | `planning directory is not a directory` |
| it cannot be listed, or is outside the roots | `planning directory cannot be listed: <error>` |
| a link that is not a link to a file inside the roots (in `skipped`) | `is a link` |

## The capacity declaration

devagent's config may declare the operator's capacity for a week
(mtibbits/devagent#743): a shared `[capacity]` table and, for any project,
a `[project.<name>.capacity]` table. Gantry plans the week from it
([week-plan.md](week-plan.md#declared-capacity-from-devagent), #39). It is
a config format, not a per-issue record, and a module of its own reads it:
`gantry/capacity.py`, through the same confined TOML read as the site list.
`gantry build` runs no devagent command.

The tables, their rules and devagent's reader of them are in the pin's
release, `devagent--v3.4.0`, the first that has them. There
`scripts/capacity.sh` is the reader's command, `scripts/lib/capacity.py`
holds every rule, the document and the text, and devagent's
`docs/configuration.md` states them under "Capacity". This section cites
no source line: the agreement test below holds Gantry to that reader.

`gantry/capacity.py` is a port of those rules, not a paraphrase of them:
the rule strings, the order of the lines, one line per key, which rule an
invalid value switches off, the pool sums and the token rounding are
devagent's. A change there follows a change in devagent, never the
reverse.

### The keys

In `[capacity]`, every key but `operator_hours_per_agent_hour` is required:

| Key | Type | Range | Default | What Gantry does with it |
|---|---|---|---|---|
| `operator_hours_per_week` | number | `0 < x <= 168` | required | the week's attention hours, laid on the portfolio file's weekdays |
| `lane_hours_per_week` | number | `0 < x <= 168` | required | a lane's day: these hours over the week's working days |
| `max_lanes` | integer | `1 <= x <= 9007199254740991` | required | the lanes of each working day |
| `tokens_per_week` | integer | `0 < x <= 9007199254740991` | required | the token budget, with the two keys below: shown, never scheduled |
| `tokens_per_window` | integer | `0 < x <= 9007199254740991` | required | the token budget |
| `windows_per_week` | number | `0 < x <= 33.6` | required | the token budget |
| `operator_hours_per_agent_hour` | number | `0 <= x <= 9007199254740991` | none | every site's declared ratio; one above 24 is not used |

In `[project.<name>.capacity]`, every key is optional:

| Key | Type | Range | Default | What Gantry does with it |
|---|---|---|---|---|
| `priority` | integer | `1 <= x <= 9007199254740991` | `5` | checks it; the plan does not use it |
| `max_lanes` | integer | `1 <= x <= 9007199254740991` | none | checks it, against `capacity.max_lanes` too; the plan does not use it |
| `paused` | boolean | none | `false` | `true` pauses the site: none of its leaves is planned |
| `deadline` | date | none | none | checks it; the plan does not use it |
| `reserve_operator_hours_per_week` | number | `0 <= x <= 9007199254740991` | none | checks it, and its sum over the unpaused projects; the plan does not use it |
| `reserve_tokens_per_week` | integer | `0 <= x <= 9007199254740991` | none | checks it, and its sum over the unpaused projects; the plan does not use it |

`integer` is a TOML integer: `3.0` and `true` are wrong types. `number` is a
TOML integer or float, never a boolean, and one that is not finite (`nan`,
`inf`) is out of range. `date` is a TOML local date, or a string that is
exactly `YYYY-MM-DD` in ASCII digits and names a real day; a date-time is a
wrong type. Where a number has no upper bound of its own, the bound is
2^53 − 1, which is 9007199254740991.

### The lines

A broken rule is one line, `capacity: <where>: <rule>`, and one such line
makes the whole declaration invalid: Gantry then uses nothing it declares.
`<rule>` is one of sixteen strings. They are devagent's contract, and Gantry
writes them as devagent does:

```
missing required key
wrong type: expected number
wrong type: expected integer
wrong type: expected boolean
wrong type: expected date
wrong type: expected table
out of range: 0 < x <= 168
out of range: 1 <= x <= 9007199254740991
out of range: 0 < x <= 9007199254740991
out of range: 0 < x <= 33.6
out of range: 0 <= x <= 9007199254740991
not a calendar date
exceeds capacity.max_lanes
[capacity] table missing
reserve_operator_hours_per_week sum exceeds capacity.operator_hours_per_week
reserve_tokens_per_week sum exceeds the effective weekly token budget
```

- A required key that is absent is `missing required key`.
- A value of the wrong type is `wrong type: expected` and its key's type. A
  `capacity` that is not a table, shared or a project's, expects `table`.
- A number outside its key's range is `out of range:` and that range.
- A `deadline` string that names no real day is `not a calendar date`.
- A project's `max_lanes` above the shared one is
  `exceeds capacity.max_lanes`, whether or not the project is paused.
- A project's capacity table in a config with no `[capacity]` table is
  `[capacity] table missing`.
- The last two are the pool rules, and their `<where>` is `pool`. The
  hours rule: the unpaused projects' reserved operator hours add up to more
  than `operator_hours_per_week` plus 1e-9. That fixed tolerance takes up
  binary rounding, so reservations that fill the hours exactly pass. The
  token rule is strict: their reserved tokens, whole numbers, add up to
  more than the effective weekly token budget.

The lines come in devagent's order: the shared table's keys in the order
above, then each project in file order with its keys in the order above,
then `pool`. A key gets at most one line, and a pool rule runs only when
every value it reads is valid.

`<where>` is the dotted path of the key, such as `capacity.max_lanes`. A
part that matches `[A-Za-z0-9_-]+` in full is written bare. Any other part
is written as a JSON string, its non-ASCII characters kept as they are:
`project."my proj".capacity.priority`, `project."a.b".capacity.paused`. So
a name that holds a line feed stays on one line in Gantry's own lines
(devagent's reader does not read such a project; see the project list
below). A line reaches the week
view as plain text, through the page's safe render path.

### Warnings

A key devagent does not know is a warning, not a violation:
`capacity: warning: <where>: unknown key (ignored)`. Gantry mirrors it. The
key is ignored, the declaration stays valid, and the line is a
`capacity_unknown_key` entry of the plan's report. Warnings follow the
violations: the unknown keys of `[capacity]` in file order, then each
project's.

devagent's reader writes one more warning, which Gantry does not mirror:
`capacity: warning: project.<name>: only key is capacity (misspelled project name?)`.
Such a project has no `devdoc_dir`, so Gantry already lists it as a site it
cannot build (#7), and adds no line of its own.

### The document and `capacity_schema`

For a config that breaks no rule, devagent's reader prints a JSON document.
With a violation it prints none. The document's `capacity_schema` is
`"1.0"`, and that is the version Gantry's port implements. It has six
fields: `capacity_schema`; `declared`, whether the config has a
`[capacity]` table; `token_unit`, which is
`weighted_input_token_equivalents`; `shared`; `effective_tokens_per_week`;
and `projects`. The effective weekly token budget is the smaller of
`tokens_per_week` and `tokens_per_window` × `windows_per_week`, that
product rounded half up to whole tokens. Its `binding` names the term that
sets it: `tokens_per_week`, on a tie too, or
`tokens_per_window_x_windows_per_week`.

Gantry never reads that document: it reads the config. It can write the
same document from its own reading (`document_text` in
`gantry/capacity.py`), which is how the two readers are compared. The plan
records the schema as its `capacity.capacity_schema` whenever the config
sets a `capacity`, shared or a project's, valid or not.

### The agreement test

`tests/test_capacity.py` holds the port to devagent's reader. For each
fixture config under `tests/fixtures/capacity/config/`, the one with
hostile project names included, it runs devagent's own
`scripts/capacity.sh` from the checkout that `GANTRY_DEVAGENT_DIR` names, a
checkout of the release `tests/devagent.pin` names, and has Gantry read the
same file. For every fixture but one
([The project list](#the-project-list)) it compares devagent's standard
output with Gantry's document byte for byte, so `20` against `20.0`, the
key order and `capacity_schema` are all seen. It compares devagent's
violation lines and its unknown-key warnings with Gantry's, as bytes too.
The fixtures are fictional and written by hand: none stores devagent's
output. The test skips, warns and fails by the contract test's rules
([Fixtures and the contract test](#fixtures-and-the-contract-test)).

### The project list

One fixture, with a project named `a.b`, is read differently on purpose.
Gantry takes the projects from the config itself: every key of `[project]`
whose value is a table, in file order, whatever its name. devagent's reader
takes them from devagent's own project list, which holds only a name it
prints as one `project.<name>` line: it leaves out a name with a dot or a
line feed in it, and the empty name, so such a project's capacity table gets
no line from devagent and no place in its document. Gantry reads it like any other.
`[project."a.b".capacity]` with `paused = true` pauses the site `a.b` in
Gantry's plan, and a broken rule in that table makes the declaration
invalid for Gantry, though devagent's reader accepts the config. A test
pins the difference.

## Fixtures and the contract test

The STUCK and checklist fixtures under
`tests/fixtures/devagent_records/written/` are written by devagent's own
scripts, never by hand. `tools/devagent_record_fixtures.py` runs seventeen
recipes (`checklist-init.sh`, `checklist-mark.sh`, `stuck.sh`,
`checklist-stuck.sh`, `park.sh`, `resume.sh` and `revise.sh`) against a
devagent checkout at the pin, each in a throwaway devagent home with one
configured project and no network. It replaces every timestamp with a fixed
one in the writer's own form (`2026-01-12T09:30:00+00:00` or
`2026-01-12 09:30`), so the fixtures still parse `created`, and writes a
`provenance.json` beside each folder naming the pin, the devagent commit and
version, the writer and its steps. Three recipes expect exit 1: the two
multi-line reasons, which must still leave a marker, and `stuck.sh` at step
0, which must leave none. The edge cases the writers cannot produce are
derived from these fixtures inside the tests. The plan fixtures
(`tests/fixtures/devagent_records/plans/`) are hand-written to the template
and the shapes above.

To regenerate, with a devagent checkout at the pin (a clone, so the commit
is known; for an extracted archive pass `--commit`):

```
python3 tools/devagent_record_fixtures.py --devagent <checkout>
python3 tools/devagent_record_fixtures.py --devagent <checkout> --samples samples
python3 tools/devagent_record_fixtures.py --devagent <checkout> --check
python3 tools/sample_portfolio.py --fixtures --devagent <checkout>
python3 tools/sample_portfolio.py --fixtures --check --devagent <checkout>
python3 tools/update_expected_leaves.py --devagent <checkout>
python3 tools/update_expected_leaves.py --check --devagent <checkout>
```

A pin move regenerates all of these in one change, because each records the
pin and a test holds it equal to `tests/devagent.pin`:

- `tests/fixtures/devagent_records/written/`, each folder's record files and
  its `provenance.json`, and the copies under `samples/<site>/` (each
  recipe names its site; `--samples` takes the samples root, the folder that
  holds `gantry.sample.toml`, and refuses any other): the first two
  commands.
- `tests/fixtures/portfolio/written.json` and
  `tests/fixtures/portfolio/provenance.json`: devagent's portfolio JSON over
  Gantry's `WBS.md` fixtures; `tests/fixtures/portfolio/samples.json` and
  `tests/fixtures/portfolio/samples_provenance.json`: its document over
  `samples/`, which the sample-site unit tests read, and the hash of each
  input (`tools/sample_portfolio.py --fixtures` writes all four).
- `tests/fixtures/expected_leaves/`: the six sample snapshots and the 14
  under `fixture/` (`tools/update_expected_leaves.py`). Run it after the
  line above, since the fixture snapshots are read from `written.json`.
  `known_differences.json` in that folder is kept by hand; review it against
  the new snapshots.

A pin move also re-runs the build-side differential that
`tests/helpers_config.py` names (#195), since the build-side test sources
model devagent's report entries by hand and no gated test re-checks them.

[worksite-page.md](worksite-page.md#reading-devagents-portfolio-json)
describes the portfolio fixture, the snapshots and their tests.

The writers need devagent's own toolchain: bash, awk, sed, `mktemp`, GNU
`date -Iseconds`, GNU `chmod --reference` (`scripts/lib/log.sh:60`) and
`python3` 3.11 or later; the tool checks each first and exits 2 naming the
one missing. A devagent host trust policy must not be installed on the
machine, since the writers would consult it; the tool asks devagent's own
lookup and refuses. On Windows it runs under Git Bash.

**The contract test** (`tests/test_devagent_contract.py`) regenerates every
fixture from the checkout named by `GANTRY_DEVAGENT_DIR`, masks the
timestamps on both sides, and compares them byte for byte. A difference
fails naming the fixture, the script that wrote it and the first differing
line. It also checks that the plan template still lists check-box tasks
under `## Tasks`, and that each devagent line this page cites still holds
the text it is cited for. Locally it skips when `GANTRY_DEVAGENT_DIR` is
unset or is not a devagent checkout, and warns when the checkout's
`plugin.json` version differs from the pin's, naming both; with `CI` set
each of these fails the run. On Windows it always skips with the reason
"needs devagent's own toolchain (bash and GNU coreutils)". Every CI leg
clones devagent at the pin and checks the tag's commit against the
fixtures' `provenance.json`, because the test workflow has no conditional
steps; only the Ubuntu legs run the test.

## What can move to the portfolio JSON

Gantry has asked devagent for a per-issue stuck flag and next-step field in
its portfolio JSON (mtibbits/devagent#725, emitted by mtibbits/devagent#726).
devagent writes them, as each issue's `stuck`, `checklist`, `next_step` and
`next_step_number` under `projects[].issues`. Once Gantry reads that JSON
(#12), **whether an
issue is STUCK** and **its next step** can come from there. The portfolio
reader (`gantry/portfolio_json.py`) reads the document's nodes and report
today and not its per-issue entries (`projects[].issues`); reading those is
#12's. **The parked
state, the STUCK marker's reason and time, whether a checklist shows
progress, and the plan's next task** stay with this module unless devagent
emits them too. The callers' types do not change shape when a source moves.
