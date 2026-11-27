# Changelog

All notable changes to Gantry are recorded here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Gantry follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The version lives
in `.claude-plugin/plugin.json`; the README's "Versioning & releases" section
has the policy and the release procedure. Each user-visible change adds its
entry as a file in `changelog.d/`, and each release compiles the waiting files
into its section here.

## [Unreleased]

New entries are files in changelog.d/; README's "Versioning & releases" says how.

## [0.2.0] — 2026-11-26

### Added

- Each built site's panel on the strategic page ends with a row of five
  time thieves: too much work in progress, unknown dependencies, unplanned
  work, conflicting priorities and neglected work. Each shows its value, its
  threshold from the portfolio file's `[thresholds]`, whether it is flagged
  and its source, or "no data" with the reason. What only the build machine
  can see (its own checklists and STUCK markers, an uncommitted `WBS.md`)
  is labelled "as seen by this build machine". Neglected work reads each
  site's `WBS.md` history from its planning repository, so the build now
  runs git (docs/strategic-page.md#time-thieves). When one site's thieves
  cannot be derived, its row reads "no data" and `gantry build` warns
  `gantry: warning: site <name>: thieves not derived: <type>`; the exit
  code is unchanged.
- The strategic page shows the fires that name no site, above the groups.
- `gantry build` plans the week from the capacity devagent's config
  declares: its `[capacity]` table and each `[project.<name>.capacity]`
  table, read from the devagent config the installation already names. The
  build runs no devagent command and needs no new config key. The declared
  weekly operator hours are scaled onto the portfolio file's weekdays after
  reserved blocks, or spread evenly over Monday to Friday when the portfolio
  file sets no `attention_hours`; `max_lanes` and `lane_hours_per_week` give
  the lanes and a lane's hours a day in place of `agent_lanes` and 6 hours;
  `operator_hours_per_agent_hour`, up to 24, is the rate of every leaf with
  no `h:`; a project with `paused = true` gets nothing, each open leaf
  unplanned as `paused`; and the token budget is shown, not scheduled. With no
  `[capacity]` table the plan is the one the portfolio file gives, as
  before. An installation whose devagent config already has a `[capacity]`
  table is planned from it from this release on
  (docs/week-plan.md#declared-capacity-from-devagent).
- A declaration that breaks one of devagent's rules is not used: the week is
  planned from the portfolio file, and the week view says so and lists each
  line devagent's own reader writes about it; `gantry build` warns
  `gantry: warning: devagent's capacity declaration is invalid (<count>); it is not used`
  on stderr, since a site it pauses is then planned again. An unknown key
  is ignored and listed. The rules are devagent's, mirrored from its release 3.4.0 and held
  to it by a test that runs devagent's reader on the same config files
  (docs/devagent-formats.md#the-capacity-declaration).
- The week view shows each pool's capacity source, `portfolio file` or
  `devagent capacity declaration (this build machine)`, how the declared
  hours were laid on the weekdays, each weekday whose hours did not fit in
  the day, a declared ratio of 0 or one that is not used, and the token
  budget. The no-plan page shows those of these lines that still apply.
- `week-plan.json` gains `capacity`, each day's
  `attention_hours_lost_to_cap` and each report entry's `text`, the
  unplanned reason `paused` and the report kinds `capacity_invalid` and
  `capacity_unknown_key`. `plan_version` stays 1
  (docs/week-plan.md#the-version-rule).
- The sample devagent config gains a `[capacity]` table that matches the
  sample portfolio file, so the demo's blocks are unchanged: its week view
  names devagent's declaration as the capacity source and gains the token
  budget line.
- Each leaf box on the worksite network and on the week view shows a row of
  five small boxes, one per devagent workflow phase (plan, implement, check,
  merge, wrap up), worked out by step name from the leaf's `checklist.md`:
  a green check for a phase done, a steady green dot for the current one,
  `!`, `P` or `?` for a stuck, parked or blocked-outside step, an empty box
  ahead, a dash for a phase the workflow skips, and a crossed box when the
  checklist cannot be trusted. The box's tooltip states the row as text, and
  both pages' legends name the eight looks and say the row shows the build
  machine's copy of each checklist (docs/worksite-page.md#the-phase-row).
- The samples gain devagent-written checklists on drawn leaves, so the demo
  pages show a row mid-plan, implementing, checking, waiting at `mergemr`,
  stuck, parked and unknown.

### Changed

- **Break:** `gantry build` reads every site from devagent's portfolio JSON
  instead of reading each `WBS.md` itself, and the installation config moves
  to `config_version = 2` with a new required key, `portfolio_json`, naming
  that document. A version-1 config is refused. To upgrade, make both edits:
  set `config_version = 2`, and add `portfolio_json = "<path>"` naming the
  file devagent's aggregator writes (`<devagent home>/portfolio/portfolio.json`;
  add the aggregator to `[refresh] update` after the pulls,
  docs/refresh-and-serve.md#the-update-list). Run the aggregator
  (`/devagent:portfolio`) before each build: a missing or unreadable document
  stops the build with exit 1 and writes nothing (docs/config.md#version-history).
  On Windows, run Gantry under WSL beside the aggregator: a native-Windows
  Gantry cannot share a `devdoc_dir` with it (docs/config.md#supported-platforms).
- A site devagent's config names and the document does not, a project only the
  document holds, and a site whose status the document reads otherwise than
  Gantry are listed "out of date" and not built; give every `devdoc_dir` as
  an absolute path (docs/worksite-page.md#statuses). Not-built reasons use the
  document's words (`no WBS.md: the devdoc dir has no WBS.md`).
- Outside sample mode every page's stamp ends with when devagent wrote the
  data (`; devagent data written <time>`). devagent's own report entries are
  lines of their site's panel, and a metadata list shows as `[a, b]`.
- devagent's near-miss rule decides which mistyped lines are reported, so
  `* [ ] ...` and `+ [ ] ...` lines now fail the P0 check closed, and a line
  indented with no-break spaces no longer does (docs/daily-screen.md#fires).
- The demo build (`tools/demo_build.py`) fetches devagent at the pinned
  release and runs its aggregator: it needs bash and network access, and
  refuses Windows (docs/demo.md).
- The portfolio file's advisory `capacity_missing` entry now reads
  `no capacity.<key>, so the week planner has no capacity to plan into
  unless devagent's capacity declaration is used`, in `gantry portfolio`'s
  output and on the strategic page. With a used declaration the week is
  planned without that key. The entry is still reported from the file
  alone, and `gantry portfolio` still exits 1 on it.
- The full build reads each site's devagent issue folders once, before the
  pages, and the pages and the fire collector share that read; a `--site`
  build reads its one site's folders. A site whose records cannot be read
  still gets its page, with unknown rows and one more "Missing or
  unreadable" line, so the index counts one more for it.
- Worksite network boxes are 78 high (rows 92 apart) to fit the phase row.
- The sample build's `fires:` line reads `fires: 8; sources not fully read: 1`:
  the new harbor `Issue-16` sample is stuck, with the STUCK marker devagent
  writes.
- **Break:** the oldest supported devagent is now 3.4.0. devagent 3.0.0 to
  3.3.x are no longer supported: upgrade devagent to 3.4.0 or later before
  installing this release. Gantry's fixtures, tests and documented devagent
  formats are those of `devagent--v3.4.0` (`tests/devagent.pin`), and the
  portfolio reader names schema 1.2, the schema that release writes; it
  still reads any 1.x document (docs/devagent-formats.md#the-pinned-release).

### Removed

- Gantry's own `WBS.md` parser (`gantry/wbs_stopgap.py`), and with it the
  raw-text reading of metadata: every value is devagent's parse.

## [0.1.3] — 2026-11-19

### Added

- `gantry build` writes the strategic page, `<output_dir>/strategic.html`:
  one panel per configured project, grouped under the business requirements
  each built site serves, then "Serves no listed requirement", "No intended
  share set" and "Not built". Each panel sets the site's open work and its
  intended share against its backlog share (its share of all built sites'
  open hours, not effort), with the gap between them; every figure names its
  source and carries `data-figure` and `data-source`
  (docs/strategic-page.md).
- The index shows the largest share gap as its headline, each built site's
  backlog share on its line, and a link to the strategic page.
- `gantry refresh --config <file>`, the refresh job an OS scheduler runs
  every `interval_minutes`: under a lock in the new `[refresh] dir`, it runs
  the `[refresh] update` commands (no shell, a time limit, the process tree
  stopped on timeout), records the update in `update.json`, builds a new
  generation in a child process and swaps it in whole through `current`
  (docs/refresh-and-serve.md).
- `gantry build --output-dir <dir>` writes one build into another folder;
  a full build with it writes a `build.json` completion marker, holding its
  exit code, as its last file.
- A reader for devagent's portfolio JSON (`gantry/portfolio_json.py`), held
  equal to Gantry's own `WBS.md` reader on every sample site by committed
  snapshots; `gantry build` does not use it yet. Two tools make the
  sample document and the snapshots from the pinned devagent release
  (docs/worksite-page.md#reading-devagents-portfolio-json).

### Changed

- A full build now runs the portfolio file's checks once over its one read
  of the file, the milestone leaf included, and lists the entries on the
  strategic page; `gantry portfolio`'s note says so.
- A full build writes and prints `strategic.html` before the index. When it
  cannot be made or rendered, the build prints
  `gantry: error: cannot render strategic.html: <type>: <message>`; when it
  cannot be written, `gantry: error: cannot write strategic.html: <reason>`.
  Either way it writes the index without the page's headline, shares or
  link, and exits 1.
- The privacy promise in README, BRule-3 and SECURITY.md now reads "Gantry
  itself opens no network connection": `gantry refresh` runs the update
  commands the installation config lists, which connect only where the
  operator points them.
- The week view draws each placement as the worksite page's leaf box: the
  key, the leaf's own priority and the placement's hours, the title cut and
  wrapped as on the worksite page, the facts, and the model band, filled by
  slack and outlined by state and `xsite` with the worksite's colours. One
  rule in `gantry/views/leafbox.py` cuts and classes the box on both pages,
  and both pages insert one cue colour sheet,
  `gantry/templates/leaf_colours.css`
  (docs/week-plan.md#the-week-view-page).
- The week view's lanes row groups its leaves under one heading per site,
  and a legend explains the red fills and the `P0` mark.

## [0.1.2] — 2026-11-18

### Changed

- A worksite page's line of unestimated leaves names at most the first 20
  of each `est:` class, then `and N more`; its count is still the class's
  total (docs/worksite-page.md).
- The portfolio file reads an ATX heading indented one to three spaces as a
  heading, as CommonMark and a rendered page do, so an indented
  `## Settings` or `## Business requirements` is read instead of being
  reported as `no_settings` or `no_br_section`. An indented level-1 or
  level-2 heading now also ends the section above it: requirements below
  it, and their checks, are no longer read, and one between `## Settings`
  and its toml block hides the block. An indented second heading is a
  `duplicate_section`; four or more spaces, or a tab, is still not a
  heading (docs/portfolio-file.md).

### Removed

- The note that devagent's `wbs order` ranks P0 leaves differently, from a
  worksite page's schedule and from the week view's P0 leaves: devagent
  3.1.0 is the first release whose `wbs order` ranks P0 above P1
  (docs/worksite-page.md#priority).

### Fixed

- An estimates file with no table whose first header cell is `#` (prose
  only, other tables only, empty, or an indented or quoted `#` table) is
  now reported as `estimates_no_table` instead of being read silently, and
  the worksite page no longer names it as read (docs/worksite-page.md).
- An `estimates-*.md` name whose date is not a valid date, such as
  `estimates-2026-02-30.md`, is now ignored for that reason, not as a name
  with no date; a configured file with such a name is still read, and the
  page header names the invalid date instead of saying "(no date)"
  (docs/worksite-page.md).
- An estimates-file row such as `#012` that names no `WBS.md` line now
  says it has a leading zero and is read as `Issue-012`, not `Issue-12`
  (docs/worksite-page.md).
- The requirements reader names the real cause of more problems. A
  `needs:` or `refines:` list with text after its `]`
  (`{needs: [FR-1] [FR-2]}`) is reported as a malformed list, not as
  `needs: []`, and the block's other keys are still read. A trailing comma
  in a meta block (`{refines: SR-1, }`) is accepted, as devagent accepts
  it. A second meta block on one item is reported as one wherever the
  first sits, also when both are on continuation lines. A key repeated
  after a first value that was not read now says "the first is not read,
  so neither is" instead of "the first is kept".
- An ID item indented under a thematic break (`* * *`, `- - -`) is read as
  top-level instead of being reported as nested.
- A requirements path that is a directory is reported "the requirements
  file is not a file", and a missing path, or a path under a file, "the
  requirements file does not exist", with the same text on every
  operating system.
- An ID item inside a block quote (`> - FR-1: a`) or indented with a
  no-break space, and a carriage return with no line feed after it (a
  CR-only file), are now reported `malformed_item` instead of being lost
  without a report. A fence inside a block quote hides nothing, so an ID
  item in a quoted example is reported too; show such an example in an
  unquoted fence. A CR cuts its line, so a requirement's text no longer
  runs on into the next line's item.
- docs/requirements-format.md states that a line of only whitespace,
  Unicode whitespace included, is blank, as the reader already read it.
- The trace now reports a probable unbracketed `satisfies:` list
  (`unbracketed_list`) only for the key directly after a `satisfies:`
  that is not a bracketed list, when that key is named like a site or
  `br_namespace`. A site named like a meta key, such as `issue`, `h` or
  `est`, no longer gets that report on every leaf carrying the key. A
  real unbracketed list now gets one report per leaf instead of one per
  split-off key, so the page's `malformed` count drops for such leaves.
  It no longer reports a list whose second entry names no known
  namespace, or an ID written after a bracket that closes too early;
  docs/requirements-format.md lists the rule's known limits.
- `gantry build` refuses an installation config that holds an integer too
  large to convert to a float for a positive-number key, or an installation
  or devagent config with a value nested too deeply for Python's TOML parser
  to read, with one `gantry: error:` line instead of a traceback.
- The portfolio file reports an integer too large to convert to a float as a
  `bad_value` where the key's rule says a finite number, or as a
  `bad_allocation` for a share, instead of accepting it, so
  `gantry portfolio` exits 1 for that file.
- The week plan's mapping note reads "not finite", not "above 24", for an
  integer too large to convert to a float.

## [0.1.1] — 2026-11-17

### Added

- The fires list: a private Markdown file of fires that are not leaves yet,
  the `fires_check_days` config key and `gantry fires --config <file>`,
  which checks it (docs/fires-list.md).
- Threshold and objective paths (#79): a requirement may state a
  `Threshold:` and an `Objective:`, a leaf's `objective:` names the
  requirements it serves at objective only, and selecting a requirement
  with an open objective-only leaf splits its path box into the threshold's
  path and end day and the objective's (docs/requirements-format.md,
  docs/worksite-page.md).
- A security policy with private vulnerability reporting (SECURITY.md).
- The README's Support section: what Gantry promises, what it does not, and
  the supported versions of Python, git, Claude Code and bash; git's oldest
  supported version is 2.45, the oldest that keeps history reads off the
  network (README.md).
- devagent as a supported tool: 3.0.0 or later. Full function needs
  devagent's latest release, and on an older one what needs a newer
  devagent contract is shown as missing (README.md).
- A reader of devagent's per-issue records (#31): STUCK markers, checklists
  and plans, read from every issue folder of a planning directory with no
  link followed, pinned by fixtures that devagent's own scripts write and a
  contract test against devagent 3.0.0 (`tests/devagent.pin`) on the Ubuntu
  CI legs; the harbor sample gains seven issue folders
  (docs/devagent-formats.md).
- The "Served from other sites" panel on a worksite page (#21): in a full
  build, each leaf on another site whose `satisfies:` names one of this
  site's requirements, linked to its leaf on that site's page and lit when
  its requirement is selected; requirement IDs of another built site link
  to its page; the trace report gains `sites_not_read` and
  `undefined_foreign` (docs/worksite-page.md, docs/requirements-format.md).
- Every full build collects the installation's fires across sites (the
  fires list, open P0 leaves and devagent STUCK markers), with a read
  status for every source, and prints `fires: N; sources not fully read: M`
  as its last stdout line (docs/daily-screen.md#fires); a script that read
  the last stdout line of `gantry build` now reads that line. The line
  prints even when the build exits 1 after collecting, such as when the
  output folder cannot be made, so stdout is no longer empty there. A fires
  source that cannot be read at all is named on stderr. Reading every issue
  folder's records makes a full build slower on a large installation
  (mtibbits/gantry#171 follows up).
- The week view page: every full build writes `week.html` beside the
  index, and the index links it as "Week view". It shows the week plan's
  10 working days (5 at first, with a control for all 10), each day's
  blocks, lanes row and what was left, each leaf's hours, need or rate,
  tag and basis, the binding pool, the allocation rule, the mapping, the
  unestimated count, share-less sites, P0 leaves, each site's unplanned
  leaves by reason and the per-site shares and charges; it computes
  nothing itself (docs/week-plan.md#the-week-view-page).
- The fires list also reports lines that look like entries but miss the
  marker rule, such as `[] 2026-10-24 ...`, `1. [xx] ...` or
  `> - [ ] ...`, as unreadable entries
  (docs/fires-list.md#entry-like-lines).
- `python3 tools/next_requirement_id.py <code>` prints the next free
  requirement ID for a layer, counting `main`, the working tree, and open
  issues and pull requests by the owner, a member or a collaborator; it
  writes nothing and refuses rather than guess.

### Changed

- The requirements reader (#79): a meta block that ends a requirement's own
  text before its first nested item is now read, no longer reported; a
  `Threshold:` or `Objective:` list item it cannot place, silent prose
  before, is now reported `malformed_item`; and a statement leaves the
  requirement's text, so the Requirements panel no longer shows it and the
  path box shows it on selection (docs/requirements-format.md).
- In a full build, a requirement served only by another site's leaf is no
  longer unserved (#21): unserved counts, the unserved label and the
  `trace <site>:` line's `unserved` count every read site's leaves, and the
  label names any site not read ("not counted: …") instead of "served only
  within this site". A `--site` build is unchanged
  (docs/worksite-page.md).
- The week plan divides each day between sites by priority tier, P0 work
  and work in progress first, then by the portfolio file's intended shares;
  `week-plan.json` gains `notes`, each leaf's `tier`, and each site's
  shares, share-less days, share note, fair-share targets and P0 list
  (docs/week-plan.md).
- A break now includes raising the oldest supported version of a tool, and
  a release with a break says in its CHANGELOG section what to change
  (README.md).
- `gantry build` prints the path of `week.html` between the site pages and
  `index.html`, and exits 1 with `gantry: error: cannot render week.html:
  ...` or `gantry: error: cannot write week.html: ...` when the week view
  cannot be rendered or written; the index is then written without its
  "Week view" link and the plan file is still written
  (docs/worksite-page.md#every-site-and-the-index).
- Changelog entries are files: a change adds
  `changelog.d/<key>.<kind>.md` instead of a line under `## [Unreleased]`,
  and `python3 tools/changelog.py release <version> <date>` compiles the
  waiting files into the release's section; `check` and `preview` report
  and show what is waiting (README.md).
- `docs/requirements.md` now says to take a new requirement's ID from
  `tools/next_requirement_id.py`, claim it in the issue's body, and that the
  pull request merging second renumbers; IDs are never renumbered or reused
  once they are on `main`. `docs/requirements-format.md` defines a number as
  used once it is on the file's default branch.

## [0.1.0] — 2026-11-14

### Added

- The worksite page, one per project: the dependency network with its
  critical path, a rolling-wave zoom and a schedule across parallel
  worklanes; and the index that links every site's page (docs/worksite-page.md).
- `gantry build --config <file>`, with `--site <name>` to build one site's
  page, and `gantry portfolio --config <file>`, which checks the portfolio
  file without building (docs/config.md).
- The installation config at `config_version` 1: every data path comes from
  it, and a build reads and writes only under the roots it names
  (docs/config.md).
- The week plan: each build plans the week onto blocks, honours dependencies
  across sites, and writes `week-plan.json` (docs/week-plan.md).
- The attention estimate for each leaf, which the week plan is built on
  (docs/week-plan.md).
- The requirements file format, and the requirements trace overlay on
  worksite pages (docs/requirements-format.md, docs/worksite-page.md).
- The portfolio file: the operator's private priorities and capacity
  (docs/portfolio-file.md).
- The `WBS.md` history reader, which reads each site's planning repository;
  no page shows its answers yet (docs/wbs-history.md).
- The `/gantry:build` Claude Code plugin command, which runs the build with no
  pip install (docs/config.md).
- The public demo, built only from fictional sample projects, and its Pages
  site (docs/demo.md).
- The commit check, in CI and as an optional local hook (docs/commit-check.md).
- This plugin marketplace, the version policy and the release procedure
  (README.md).
