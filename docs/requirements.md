# Gantry requirements

Gantry's own requirements, in the format of
[requirements-format.md](requirements-format.md), in namespace `gantry`. A
bare ID here means `gantry:<ID>`.

Source of truth: this file was derived once from Gantry's v1 design notes.
From now on it changes with the code, in the same pull request as the change
it describes. It is not re-derived from the notes, and the notes are not
updated from it.

IDs are never renumbered or reused. A withdrawn requirement is deleted, and
its withdrawal is recorded in a prose line in its section.

`tests/test_own_requirements.py` reads this file; keep every requirement a
top-level `- ID: text` item.

The file holds every layer but business requirements. An operator's
priorities live only in their installation's private portfolio file, and no
ID here is in another namespace.

## Terms

- **operator:** the person running a Gantry installation.
- **installation:** one private copy of Gantry with its own data and config.
- **site (worksite):** one project, with its planning page.
- **leaf:** a line in a project's work breakdown (`WBS.md`) with no
  children, usually one tracker issue.
- **devagent:** the Claude Code plugin whose `WBS.md` files and config
  Gantry reads.
- **requirements file:** a project's Markdown file of requirements with
  stable IDs, in the format of [requirements-format.md](requirements-format.md).
- **namespace:** the prefix that says whose requirement an ID is: a site's
  name, or the installation's namespace for business requirements.
- **trace:** the links from leaves up to the requirements they serve,
  written as a `satisfies:` meta key on the leaf; **in the trace:** a site
  whose requirements path is configured.
- **anti-goal:** an outcome that makes Gantry fail even if it works.
- **portfolio JSON:** devagent's versioned JSON of every project's plan,
  gathered from their `WBS.md` files.
- **portfolio file:** an installation's private file of the operator's
  priorities and intended shares across sites; never in this repository.
- **fire:** something that needs the operator now; **fires list:** an
  installation's private list of fires that are not yet leaves.
- **autonomy milestone:** the point at which an issue runs from start to
  finish without the operator, marked by one configurable leaf.
- **build machine:** the machine that builds an installation's pages.
- **priority (P0 to P4):** a leaf's WBS priority key, where a lower number
  is more urgent and P0 sits above P1.
- **`est`:** a leaf's WBS estimate key.
- **`h:`:** a WBS key scoring how much of the operator's attention a leaf
  needs, an integer from 0 (none) to 4 (the most).
- **qualified ID:** a requirement ID written with its namespace,
  `<namespace>:<layer code>-<n>`, for example `gantry:FR-12`.

## Business rules

None yet.

## Use cases

- UC-1: An operator opens a site's planning page to see its dependency
  network, its critical path, the zoom, each leaf's models and the schedule.
  {needs: [FR-1, FR-2, FR-3, FR-4, FR-5, FR-7, FR-20, FR-21, QA-5, QA-6]}
- UC-2: An operator opens the strategic page to see, across sites, where
  agent capacity and their own attention are going against their intended
  shares, each site's time thieves, the autonomy milestone and the binding
  resource. {needs: [FR-6, FR-13, FR-14, FR-15, FR-16, FR-19]}
- UC-3: An operator opens the daily screen on a phone and sees at a glance
  whether anything is on fire and what to start warming up on today.
  {needs: [FR-9, FR-10, FR-11, QA-1, QA-2, QA-3]}
- UC-4: An operator reads the tactical week view to see which day and which
  block each planned issue falls on. {needs: [FR-17, FR-18, FR-19, QA-4]}
- UC-5: An operator follows the trace from a leaf to the requirements it
  serves, and from a requirement or a use case to its leaves.
  {needs: [FR-20, SR-5]}
- UC-6: An operator plans Gantry's own work in Gantry, as its first site.
  {needs: [FR-23]}
- UC-7: A visitor views the public demo, which is built only from made-up
  sample data. {needs: [FR-22, CON-3]}
- UC-8: An operator records a fire that is not yet a leaf, from a phone.
  {needs: [FR-12, EI-7]}

## System requirements

- SR-1: Gantry takes its sites from devagent's config and keeps no site list
  of its own. A project added there appears on the next build.
- SR-2: Gantry builds static, self-contained pages with no server-side
  application, so that a build machine can rebuild them on a schedule.
- SR-3: An installation reads and writes only its own data. Every data root
  comes from the installation's config, which lives outside the repository.
- SR-4: Gantry plans with two shared resources, agent lanes and the
  operator's attention, each of them one pool across all sites.
- SR-5: Gantry stores the requirements trace and shows it. It does not judge
  it.
- SR-6: Missing or unreadable data is shown as missing, never hidden. A leaf
  with no priority sorts as P3.
- SR-7: devagent owns the plan data, and Gantry owns what the operator sees.
  Gantry reads devagent's data rather than deriving it again.

## Functional requirements

- FR-1: The worksite page shows the open leaves that have dependency links,
  after transitive reduction, with the critical path and each leaf's slack.
  {refines: SR-2}
- FR-2: The worksite page zooms on a rolling wave. Leaves on the critical
  path, P0 and P1 leaves, work in progress and leaves starting within the
  horizon keep full boxes; the rest are sized by their estimate.
  {refines: SR-2}
- FR-3: Each leaf's thinking model and checking model are shown, read from
  the leaf when it carries them, otherwise from the estimates file.
  {refines: SR-6}
- FR-4: The worksite page shows the worklane schedule, labelled as agent
  capacity only. {refines: SR-4}
- FR-5: An estimate is read as devagent classes it, by what it measures, and
  is shown with its basis. {refines: SR-6}
- FR-6: For each site, estimates are shown against actuals. That
  calibration is what later estimates and forecasts use. {refines: SR-6}
- FR-7: An unestimated leaf takes devagent's estimate from closed work once
  one exists, read from the leaf once devagent writes estimates there. Until
  then it is scheduled at a default unit and marked unestimated, and any
  total that includes such leaves says how many. {refines: SR-6}
- FR-8: Leaves are ordered by priority, P0 to P4, a lower number more
  urgent. A priority that devagent reports as invalid is shown as reported,
  not guessed. {refines: SR-7}
- FR-9: A fire is any open leaf at P0. A leaf whose priority is P0 is a fire
  even while devagent reports P0 as invalid. Gantry orders P0 above P1 until
  devagent's own order does, and notes where the two orders disagree.
  {refines: SR-7}
- FR-10: The daily screen lists fires first: open P0 leaves, stuck markers
  on open issues that are not parked, and fires-list entries. It says
  "nothing on fire" only when every source was read and the page is fresh.
  {refines: SR-6}
- FR-11: The warm-up list shows today's planned tasks in order, each with
  its site, its estimate and context from the issue's records: the
  checklist's next step and the plan's next task. {refines: SR-1}
- FR-12: The fires list is the one record the operator keeps for Gantry
  beyond devagent's. It is a private file that can be edited from a phone,
  and Gantry only reads it. {refines: SR-3}
- FR-13: For each site, the strategic page shows its open work, its intended
  share against its share of the backlog, and its share of effort once
  per-issue actuals exist. {refines: SR-4}
- FR-14: The strategic page shows five time thieves for each site, each
  derived from records: work in progress, unknown dependencies (unresolved
  and cross-site links), unplanned work (fires), conflicting priorities
  (intended share against backlog share) and neglected work (from the
  history of the site's `WBS.md`). {refines: SR-1}
- FR-15: The autonomy milestone's status is read from the done state of one
  configurable leaf. {refines: SR-1}
- FR-16: The strategic page shows the operator's attention per finished
  issue, for each site, over time, from devagent's per-issue operator time.
  {refines: SR-4}
- FR-17: The week view lays the next working days out in days and blocks,
  using the operator capacity declared in devagent's config (EI-1) and the
  agent lanes. The operator's attention is one shared pool, and each block
  is on one site. {refines: SR-4}
- FR-18: Each leaf's need for the operator's attention is on the 0 to 4
  scale: the leaf's `h:` when it is set, otherwise devagent's measured
  ratio, otherwise an estimate shown as an estimate. {refines: SR-4}
- FR-19: The binding pool of the planned week is shown. Once devagent's
  forecast models the operator's attention, each site's history of binding
  resources is shown beside it, labelled as history. {refines: SR-4}
- FR-20: A leaf names the requirements it serves in its `satisfies:` key.
  The worksite page shows them, lights every leaf that serves a selected
  requirement, and lights a selected use case's requirements and their
  leaves. {refines: SR-5}
- FR-21: A dependency on another site's leaf, in devagent's cross-project
  notation, is drawn as a cross-site link when that site is in the
  installation, and is reported otherwise. {refines: SR-1}
- FR-22: A demo build from the sample installation produces every view, and
  nothing else reaches the public demo. {refines: SR-3}
- FR-23: Gantry's own planning is a site like any other, and this file is
  its requirements file. {refines: SR-1}
- FR-24: Where devagent's portfolio JSON is available for a site, that
  site's plan is read from it; otherwise it is read from the site's
  `WBS.md`. {refines: SR-7}

## Quality attributes

- QA-1: On a phone, the daily screen's first view, without scrolling,
  answers both questions of UC-3.
- QA-2: A fire appears on the daily screen within one refresh interval (by
  default 15 minutes) of its record reaching the build machine.
- QA-3: A page shows when it was built. Past two refresh intervals, or when
  a source could not be read, it says so and never says "nothing on fire":
  it fails closed.
- QA-4: The same inputs and the same as-of date give the same plan and the
  same pages, apart from the build stamp.
- QA-5: Text taken from records is shown as text, never as markup, and a
  page loads nothing from outside itself.
- QA-6: The daily screen works at phone width and on a touch screen.

## External interfaces

- EI-1: devagent's `WBS.md` files and its config.
- EI-2: devagent's versioned portfolio JSON. An unknown major version is an
  error.
- EI-3: devagent's estimates file, a dated snapshot of effort, hours and
  model tiers.
- EI-4: devagent's per-issue records: stuck markers, the checklist's next
  step and the plan's next task, read through one named adapter. Each is
  read as the build machine can read it; how per-machine state reaches that
  machine is not specified here.
- EI-5: devagent's per-issue actuals: agent time, operator time and tokens.
  Each is read as the build machine can read it; how per-machine state
  reaches that machine is not specified here.
- EI-6: The history of a planning repository's `WBS.md`.
- EI-7: The installation's fires list, which is private and can be edited
  from a phone.
- EI-8: A phone browser.
- EI-9: The installation's private portfolio file: the operator's intended
  shares across sites.

## Constraints

- CON-1: Gantry needs no upkeep of its own. Every number comes from records
  kept as a side effect of the work.
- CON-2: Gantry asks for no more data entry than paper would. A layer is
  derived from an existing record or written once, and the operator is never
  asked to backfill one.
- CON-3: Gantry never exposes personal data publicly. The public repository
  holds only the tool, its docs and made-up sample data, and pages built
  from real data are served only privately.
- CON-4: Gantry is not a dashboard nobody acts on. It must still be used to
  plan in a firefighting week.
- CON-5: Gantry never moves issues out of devagent.
