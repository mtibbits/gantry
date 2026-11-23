# The week plan

The week plan fits each site's open leaves into the operator's week: the
agent hours a leaf takes, and the operator hours it needs while it runs.
The full build makes the plan and writes it to `week-plan.json` (#61); the
[week view](#the-week-view-page) (#38) shows it. This page states
each leaf's attention need and rate (`gantry/attention.py`, #36), then
[the planner](#the-planner) and [the plan file](#the-plan-file)
(`gantry/planner.py`).

## Attention need

A leaf's **attention need** is how much of the operator it needs while an
agent works on it. Its **rate** is the operator hours it needs for each
agent hour: a leaf of 6 agent hours at a rate of 0.25 needs 1.5 operator
hours. Every leaf gets a rate from the first of four sources that applies,
tagged with that source and a basis naming its inputs.

The week view (#38) shows each placed leaf's need or rate, tag and basis,
which the plan file records; a worksite page shows only a bad `h:`, as a
report entry.

### The scale

The need is a whole number from 0 to 4. 0 means the leaf runs without the
operator; 4 means it needs them throughout. 1 to 3 lie between, and the
mapping below turns each need into a rate.

The meaning of `h:` is Gantry's: this page defines it. devagent's open
contract for the key (mtibbits/devagent#888) fixes only its shape, as a
`WBS.md` key it knows.

### `h:`

`h:` is the operator's own score of a leaf's need, written in its
`WBS.md` metadata. It is one ASCII digit from 0 to 4, written as a scalar:
`h: 3`, `h: "3"` and `h: '3'` are the same score. A value the metadata
reader does not read as exactly one of those digits is not used, and the
leaf's need comes from its next source:

- a digit outside 0 to 4 (`h: 5`, `h: -1`), a decimal (`h: 2.5`) or a word
  (`h: high`);
- a sign or a leading zero (`h: +3`, `h: 03`), an empty value, or a space
  inside the quotes (`h: "3 "`);
- a digit that is not ASCII (a fullwidth 3);
- a list (`h: [3]`, `h: [3, 4]`), or a value still quoted after its quotes
  are read (`h: "'3'"`).

The value is read through the same metadata reader as every other key
(`meta_values`, which #12 replaces), so Gantry has no quote rule of its own.
That reader decides the edge forms: today it reads an unclosed quote
(`h: "3`) or text after a closing quote (`h: "3"x`) as the quoted part, so
both score 3. #12 may change that.

Every `h:` that is not used is a `bad_h` entry in the site's report, listed
in the worksite page's "missing or unreadable" panel
([worksite-page.md](worksite-page.md#the-report)). The entry names the
leaf's key and the value as written. `h:` belongs on a leaf: on a row with
children it is not used either, and its entry says "on a row with
children, not a leaf". Every node is checked whatever its state, so a bad
`h:` on a done or skipped leaf is reported too, though only open leaves are
planned.

An `h:` value reaches a page only as plain text, through the page's safe
render path: in the leaf's metadata in its details, and in a `bad_h`
message.

### The heuristic

A leaf with no usable `h:` and no site ratio gets an **estimated** need:

- 1 to start;
- plus 1 when its checking tier is the top tier, `fable` (exactly: `opus`,
  `inherit` and `Fable` are not);
- plus 1 when its flags name `research` or `spike`, as a whole word in any
  case (`researcher` does not count); both words still add 1;
- plus 1 when it is its epic's first open leaf, a fresh context: it has an
  epic, no leaf of that epic is done, and it is the epic's first open leaf
  in `WBS.md` order. A skipped leaf is not work done;
- capped at 4.

The basis names each step that applied, in that order: for example
"1 to start; checking tier fable; flagged research and spike; first open
leaf of its epic". A leaf with neither model tiers nor flags takes 1, plus
the first-leaf step when it applies, and its basis adds
"no model tiers or flags recorded".

The heuristic never gives 0: Gantry does not assume a leaf runs without the
operator unless the operator scores it so.

Model tiers come from the leaf's recorded tiers, wherever they were read:
the estimates file today, and whatever source #14 gives them. Flags come
only from the estimates file, and only while #14 keeps it as their source: a leaf with no
estimates-file row has no flags.

### The mapping

The mapping turns needs 0 to 4 into rates. Gantry's default is
`0, 0.1, 0.25, 0.5, 1.0` operator hours per agent hour, so need 0 costs
nothing and need 4 costs one operator hour for every agent hour.

The portfolio file's `capacity.attention_mapping`
([portfolio-file.md](portfolio-file.md)) replaces the default for the whole
installation. The portfolio file's own check refuses a mapping that is not
five numbers, each 0 or more, not decreasing. Gantry then refuses one that
holds a value that is not finite or is above its limit. That limit,
`MAX_RATE`, is 24 operator hours per agent hour, which keeps a plan's
arithmetic finite; no real rate comes near it. In each case the default is
in use, with one of these reasons:

- "the portfolio file's attention_mapping was rejected (see the portfolio
  file's report)": the portfolio file's report holds a `bad_value` entry
  for the key;
- "the portfolio file's attention_mapping holds a value that is not
  finite";
- "the portfolio file's attention_mapping holds a value above 24".

An integer too large to convert to a float counts as not finite, so it gives
the second reason, not the third.

A portfolio file that does not set the key gives the default with no
reason. So does one whose settings cannot be read at all; the portfolio
file's own report says why. The mapping in use, its source (`default` or
`portfolio_file`) and any reason are kept together, so the week view can
show them.

### The four sources

A leaf's rate comes from the first of these that applies:

1. `scored`: its `h:` score through the mapping;
2. `measured`: its site's measured ratio, when one is passed;
3. `declared`: its site's declared ratio, when one is passed;
4. `estimated`: its heuristic need through the mapping.

`docs/requirements.md` FR-18 names three of these; it does not list the
declared ratio.

A scored or estimated value keeps its need beside its rate; a measured or
declared value has a rate only. Every value carries a basis led by its
tag: "scored: h: 3", "measured: site ratio 0.3; 12 closed issues",
"declared: site ratio 0.2", "estimated: 1 to start; …". An `h: 0` is a
score like any other, and wins over both site ratios.

A **site ratio** is one rate for every leaf of a site. The two are separate
inputs, `measured=` and `declared=`: #40 will pass the ratio measured from
devagent's history, and the week planner passes the ratio devagent's
capacity declaration states (#39). No caller passes a measured ratio yet,
so today every leaf is scored, declared or estimated. A ratio passed in the
other source's slot is refused (`ValueError`), so no caller can invert the
ranking.

The declared ratio is the `operator_hours_per_agent_hour` of a capacity
declaration the plan uses, the same for every site. A leaf that takes it
has the basis "declared: site ratio 0.25; devagent capacity declaration
(this build machine)". devagent allows a ratio above 24, and no rate may
be: the planner then passes none, the leaves with no `h:` stay estimated,
and the plan's `capacity.declared_ratio` holds the ratio with `used` false
and the reason. A rate is planned in whole parts per million
([What each leaf costs](#what-each-leaf-costs)), so a declared ratio under
half a part per million plans as rate 0, like a declared 0: its leaves run
in the lanes row and need no block.

A site ratio must be a number from 0 to `MAX_RATE`. One that is not is
refused when it is built (`ValueError`), naming the reason: not a number (a
boolean is not one), not finite, negative, or above 24. 0 is a valid site
ratio: a site that runs without the operator. The mapping's values are
held to the same finite and `MAX_RATE` limits; the portfolio file's own
check refuses the rest.

### The site ratio source

A site's **site ratio source** is `measured` when a measured ratio is
passed, else `declared` when a declared one is, else `estimated`. It
depends on the site alone: no leaf's `h:` changes it.

## The planner

`gantry build` (the full build, not `--site`) plans the next 10 working
days after it has built the sites, dividing each day between them by
priority tier and then by the portfolio file's intended shares
(`gantry/planner.py`, `plan_week`). The plan names that rule in
`allocation_rule`: "fair share by intended shares". The
plan is data: it is written to `week-plan.json` ([The plan file](#the-plan-file))
and handed, as one object, to every view rendered from the build's result
after planning. Today that is the index, which does not show it yet; a
worksite page is rendered before the plan is made and does not receive it.
The planner reads no file and no clock: it works from the sites the build
read, the portfolio file's capacity ([portfolio-file.md](portfolio-file.md))
and each leaf's rate ([Attention need](#attention-need)).

Two **pools** are divided between the sites: **operator attention hours**
and **agent lanes**, counted in lane hours. Both come from the portfolio
file, which the plan records as each pool's capacity source
(`portfolio file`).

### Days and capacity

A day's attention hours are its weekday's `attention_hours` less its
reserved blocks, never below 0. A **working day** is any day where that is
above 0: a Saturday counts when the portfolio file gives it hours, unlike
the index's "working day N", which counts Monday to Friday. A weekday whose
reserved blocks take all its hours is not a working day.

The plan starts on the as-of date, if it is a working day, and otherwise on
the next one. It covers 10 working days; the first 5 are the **planned
week**. The as-of date counts as a whole day, so a rebuild later the same
day gives the same plan. Each working day has `agent_lanes` lanes of 6
agent hours. Reserved blocks' `start` times are shown, not planned around.
An as-of date so late that the 10 working days would run past 9999-12-31
is not supported: the build exits 1 with a Python traceback.

### What each leaf costs

A leaf needs the midpoint of its estimate in agent hours, or the default
unit (`default_unit_hours`) when it has no estimate; such a leaf is marked
unestimated. A leaf in progress is charged its full midpoint, because no
record says how much is left. Every placed leaf runs in one agent lane, so
it takes at most 6 agent hours a day and a longer leaf continues on later
days. Its rate charges rate × agent hours of operator attention. A leaf
whose rate is 0 goes in the day's **lanes row** and needs no block.

The planner charges whole minutes, by two rules:

- every input is rounded to whole minutes once, as `int(hours * 60 + 0.5)`:
  a weekday's attention, each reserved block, `block_min_hours`, the start
  cost and each leaf's hours (a leaf needs at least 1 minute);
- a rate becomes whole parts per million once, and no fraction is
  multiplied after that. The operator minutes of some agent minutes round
  up, so a block's work plus its start cost always fits its length; the
  agent minutes that fit some operator minutes round down. A rate under half
  a part per million is rate 0.

Fair-share targets are the one exception: they are exact fractions, never
rounded, compared with the whole-minute charges
([Fair share between sites](#fair-share-between-sites)).

No number in the portfolio file can stop the planner. A value over 24 hours
(a block of `1e308` hours, say) counts as 24, a lane count over 1,000,000
counts as that, and a leaf longer than 1,000,000,000 hours is cut to it.

### Blocks

Leaves with a rate above 0 run inside a **block** on their own site: a span
of one working day in which the operator works on one site only. A block's
length is its start cost (`block_start_cost_hours`) plus the operator hours
of the work placed in it, and it is charged to the day's attention. A block
is never shorter than `block_min_hours`: one whose work needs less is still
charged that length. No block mixes sites. A block has an order and a
length, never a clock time. A site can hold more than one block a day,
never two in a row ([Merged blocks](#merged-blocks)).

A block never runs past the day's attention. A leaf that would is placed
for the most whole agent minutes whose operator minutes still fit, and the
rest of it continues later; the block then goes on with any other leaf of
its site that still fits. A leaf that finds no lane hours left is placed
no further. A block stays open until a block of another site opens or the
day ends, and its padding up to `block_min_hours` is charged when it
closes.

### A ready leaf that fits

At a point in a day, a ready leaf fits when the day still has lane minutes
free and the leaf is under its 6 agent hours that day, and, when its rate
is above 0, either its site's block is open and the day's attention holds
one more agent minute of it, or its site's block is not open and the
attention left, less the open block's padding up to `block_min_hours`,
holds both `block_min_hours` and the start cost plus one agent minute of
the leaf. A block that could hold no leaf is not opened.

### Order within a site

Within a site and [tier](#priority-tiers), leaves go in effective-priority
order with `WBS.md` order breaking ties, a blocker always before the leaf
that waits on it. A started leaf of any priority is tier 2, so it goes
before its site's pending leaves of tier 3. This is
the worksite schedule's rule, and it is one function,
`gantry.schedule.pick_ready`, that both call, so the two orders cannot
drift. Effective priority is taken as the worksite page takes it, over
every open leaf not held out by a cycle: a leaf the plan leaves out still
lifts the leaf it waits on. Effective priority is computed within each
site: a cross-site entry only holds a leaf and never raises its blocker's
priority.

### Priority tiers

Each open leaf has a `tier`, fixed for the whole plan: `p0` when its
effective priority is 0 (a P0 leaf, or a leaf of its site that a P0 leaf
waits on); otherwise `started` when it is in progress (`~`) or stuck (`!`);
otherwise `fair_share`. A leaf the plan started on an earlier day keeps its
tier.

Each day runs in eight phases:

1. `p0` blocks;
2. `p0` rate-0 leaves, in the lanes row;
3. `started` blocks;
4. `started` rate-0 leaves;
5. `fair_share` blocks of the share sites;
6. the share sites' `fair_share` lanes row;
7. `fair_share` blocks of the [share-less sites](#share-less-sites);
8. their lanes row.

In phases 1 to 4, each site with a ready leaf of the tier of rate above 0
gets one block holding those leaves, in the order within a site, limited
only by the day's attention, the lanes and each leaf's 6 agent hours. The
sites go in order of their most urgent such leaf's effective priority, ties
to devagent's config order, fixed when the phase starts; in phase 1 every
such leaf is P0, so config order decides. Then the sites with a ready rate-0
leaf of the tier put those leaves in the lanes row, the sites in the same
kind of order taken over their rate-0 leaves alone. A leaf's priority
orders only the pass that places it, so raising a rate-0 leaf never moves
its site's block ahead of another's. P0 work and work already in progress
go first whatever a site's share, and their charges count against it. A P0
leaf held by another site's open leaf is
not ready until that leaf finishes; a site's `p0` list names, for each
of its P0 leaves, the other sites' leaves it waits on through `depends_on`
entries on its own line or on an open row above it. A hold that reaches
it only through a local blocker is listed on that blocker's entry, not on
the leaf's. Phases 5 to 8 divide what is left by intended shares.

### Merged blocks

At most one block is open: from its opening until a block of another site
opens or the day ends. A phase that gives the next block to the open
block's site continues it: one record, one start cost, its leaves of every
tier in the order placed. So a site can hold more than one block a day,
never two in a row. A block's length is its start cost plus the operator
hours placed in it; its padding up to `block_min_hours` is charged when it
closes, so a short P0 block followed by another site's block is charged
`block_min_hours` alone. A leaf placed in two blocks of one day is listed
in both, but once in its `days` and once in the day's `unestimated` count.

### How long a block runs

A block of phases 1 to 4 runs as long as the day's pools and its leaves
allow. A block of phase 5 or 7 may always reach `block_default_hours`,
measured over the whole merged block (its start cost, any tier 1 or 2 work
and its `fair_share` work). Past it, the block extends one whole minute at
a time: a minute is allowed when the site's attention charged plus that
minute, less its target after that minute, is at most
`block_default_hours`, or when no other site of its class has a ready
`fair_share` leaf of rate above 0 that fits, judged on the attention left
before the minute. Leaves are placed in whole agent minutes against the
smaller of the day's attention and that cap, so a leaf is split where its
block stops and continues in a later block.

When the site with the largest deficit is the open block's own site and
it can place no whole agent minute under its cap while another site of its
class fits (at a rate above 1 one operator minute holds no agent minute),
the cap is lifted for that pick: the block runs on until it has placed at
least one agent minute and `block_default_hours` more operator minutes, or
a pool of the day runs out, and then the next pick is made.

The rule bounds each minute; it promises no bound on a day's result. With
two share sites the tests' installations keep each site within
`block_default_hours` of its attention target at every day's end; three or
more share sites can go past it, and so can a day whose last minutes are
under `block_min_hours`.

### Fair share between sites

For each of the 10 days the planner reads the allocation that applies on
that day ([portfolio-file.md](portfolio-file.md#the-allocation-on-a-day))
against the built sites. Shares of projects that are not built sites are
dropped, with a `shares_dropped` note; the built sites left with a share
above 0 are that day's **share sites**, their shares scaled to sum to 1,
and every other built site is [share-less](#share-less-sites) that day.
With no allocation, or nothing left to scale, every built site is a share
site at an equal share and the day has a `no_allocation` note: "no
intended shares apply on <date>; every site planned with equal shares".

Each site has one ledger per pool for the 10 days: the whole minutes
**charged** and a **target**, kept as exact fractions; its **deficit** is
target less charged. Attention is charged at a block's start cost, at each
placement's operator minutes and at its closing padding; lane minutes at
each placement. A charge of some minutes adds to the targets of the sites
that can use the pool, judged before the charge: a site can use attention
while it has a ready leaf of rate above 0 under its 6 agent hours that day,
and lanes while it has any ready leaf under its 6 agent hours. A site that
cannot absorb a minute today accrues nothing, so the share it cannot use
goes to the share sites that can. A site that can absorb a little, such as
one slow leaf still under its 6 agent hours, accrues its whole share until
it cannot: its target can run ahead of anything it will use, and the other
share sites then sit over their own targets, so their blocks hand over at
`block_default_hours` while both fit.

| A charge to | In phases | Each share site that can use the pool gains | Each share-less site that can use it gains |
|---|---|---|---|
| a share site | any | minutes × its share ÷ the shares of those sites | nothing |
| a share-less site | 1 to 4 | minutes × its share ÷ the shares of those sites | minutes ÷ their number |
| a share-less site | 7 and 8 | nothing | minutes ÷ their number |

A site that changes class from one day to the next keeps its ledger and
competes with it in its new class. The plan records each site's targets at
the horizon's end, `attention_hours_target` and `lane_hours_target`.

In phase 5, while a share site has a ready `fair_share` leaf of rate above
0 that fits, the share site with the largest attention deficit, ties to
config order, gets the next block: it continues its block when that is the
open one and opens one otherwise. Only work of rate above 0 counts here: a
site whose only ready work is rate 0 takes no block and caps no other
site's block.

### The lanes row

The lanes row holds the day's rate-0 leaves, each up to its 6 agent hours
or the lane hours left. In phases 2 and 4 they join site by site, the
sites ordered by their most urgent rate-0 leaf of the tier, ties to config
order. In phase 6, while lane minutes are left and a share
site has a ready `fair_share` rate-0 leaf that fits, the share site with
the largest lane deficit, ties to config order, places its first such
leaf; phase 8 does the same among the share-less sites.

### Share-less sites

A built site with no share above 0 in the day's allocation is share-less
that day. Its P0 and started work still go in phases 1 to 4, and those
charges move the targets of both classes. Its `fair_share` work gets only
what is left once no share site has a ready leaf that fits: phases 7 and 8,
where the share-less sites divide that rest at equal shares by the same
rules. Its site record lists its `shareless_days`, a null `share` on each
of them, and the note "no intended share set; planned only into capacity
left after the sites with shares".

### Started in this plan

A leaf the plan placed on an earlier day and has not finished goes first
among its site's leaves of its tier on each following day, ahead of a more
urgent leaf that became ready since, until it finishes. Several such leaves
of one site
keep the order within a site among themselves.

### States

The planner reads `WBS.md` states only. A leaf blocked externally (`?`) or
parked (`P`) is not planned. Every other open leaf is, an in-progress (`~`)
or stuck (`!`) one included: such a leaf is tier 2, `started`, and goes
before its site's `fair_share` work ([Priority tiers](#priority-tiers)). An
issue parked in devagent whose leaf still reads `[~]` is planned as started
work.

### Dependencies within a site

A leaf is ready on a day when every open leaf it depends on finished on an
earlier planned day: a dependent starts no sooner than the working day
after its blocker's last, even when that day has room.

### Dependencies across sites

A `depends_on` entry naming another site's open leaf holds the leaf until that
leaf's planned finish: the leaf is first ready on the working day after the
day its blocker finishes in the plan, as within a site. An entry naming a
finished leaf is satisfied. An open leaf takes the cross-site entries of each
open row above it, and an entry naming a row of another site means the open
leaves under that row: the leaf is first ready on the working day after the
last of them finishes. A leaf whose cross-site blocker is itself
`beyond_horizon`, or is placed with `hours_after_horizon` above 0, never
becomes ready and is `beyond_horizon`; one whose blocker is unplanned for any
other reason is `waits_on_unplanned`. An entry naming a site not in this
installation leaves the leaf `waits_on_site_absent`, and one that could not be
resolved leaves it `waits_on_unresolved`: the plan does not guess at work the
build cannot see. The planner reads each entry's outcome as the full build
recorded it ([worksite-page.md](worksite-page.md#foreign-entries)) and never
resolves an entry again.

### Local entries that name nothing

A local entry that names nothing is not a dependency: an `unresolved_ref`
(an id no node of the site holds, such as an outline number) or a
`malformed_ref` leaves the leaf planned as if the entry were absent, as on
the worksite schedule. The plan counts them in the leaf's `unresolved_local`:
its own such entries and those of each open row above it. The worksite page
lists each one.

### Cycles across sites

A cycle across sites is a strongly connected component of two or more open
leaves, over every built site's links and the cross-site ones, whose leaves
span two or more sites. Each is reported once in the plan's `report`, as a
`cross_site_cycle` entry. Its leaves are unplanned as `cross_site_cycle`
unless an earlier reason applies ([Unplanned leaves](#unplanned-leaves)): a
blocked or parked leaf, or a leaf of a site's own cycle, keeps that reason.
The leaves downstream of them are `waits_on_unplanned`. A site's own cycle
stays `waits_on_cycle` and is not in `report`; a cycle across sites that
holds one is reported whole. The planner always finishes.

The leaves of a paused site ([Unplanned leaves](#unplanned-leaves)) are
left out before the cycles are found. A paused leaf is in no cycle across
sites and no `cross_site_cycle` entry names one; a leaf that waits on it,
in what would have been a cycle or not, is `waits_on_unplanned`.

### Unplanned leaves

Every open leaf of a built site that is not in the 10 days is listed under
its site, in `WBS.md` order, with its state and one reason, the first of
these that applies:

1. `paused`: its site is paused in devagent's capacity declaration;
2. `blocked_externally`: its state is `?`;
3. `parked`: its state is `P`;
4. `waits_on_cycle`: it is in, or waits on, a dependency cycle within its
   site;
5. `cross_site_cycle`: it is in a dependency cycle across sites;
6. `waits_on_site_absent`: it waits on a site not in this installation;
7. `waits_on_unresolved`: it waits on an entry naming another site that
   could not be resolved;
8. `waits_on_unplanned`: it waits on a leaf that is unplanned for one of
   the reasons above or this one;
9. `beyond_horizon`: it was never placed in the 10 days, a leaf whose
   blocker is itself `beyond_horizon` included.

A site is paused when the capacity declaration the plan uses sets
`paused = true` in its `[project.<name>.capacity]` table (#39); a
declaration that is invalid pauses nothing. A paused site stays a built
site of the plan and keeps its intended share, but every open leaf of it is
`paused`, whatever its state, its priority and what it waits on. Nothing of
it is placed, so it can use neither pool and accrues nothing: its share
goes to the share sites that can
([Fair share between sites](#fair-share-between-sites)), as if its leaves
were done. The plan lists the paused built sites in `capacity.paused`.

### Past the horizon

A leaf placed in the 10 days but not finished by the last one is not
unplanned. It keeps its placements, its record carries the agent hours left
as `hours_after_horizon` (0 for a leaf that finishes), and the leaves that
wait on it are `beyond_horizon`.

### The binding pool

Each day records the pools that ran out while a ready leaf still needed
them, attention first:

- **agent lanes**: no lane minute is left while a ready leaf with minutes
  to place, under its 6 agent hours that day, waits for one, in a block or
  in the lanes row;
- **operator attention hours**: with a lane minute free, such a leaf of
  rate above 0 waits because its site's block stopped at the day's
  attention with work left, or because no block of its site could open.
  A block that stops at the day's attention counts when it stops, while
  its leaf could still take a lane minute: a later leaf of the block that
  takes the last lane minute does not hide it. No block opens when less than
  `block_min_hours` is left. By a deliberate reading of "a ready leaf that
  fits", a day also counts as attention running out when `block_min_hours`
  is left but the start cost plus one agent minute of the ready leaf no
  longer fits.

Both are judged against the day's attention, never against a block's
fair-share cap: a block the cap cut short has not stopped at the day's
attention, even when less attention was left than its leaf wanted. A waiting
leaf of rate above 0 is judged when its tier's
blocks have been planned and before that tier's lanes row: `p0` leaves
after phase 1, `started` leaves after phase 3, the share sites' `fair_share`
leaves after phase 5 and the share-less sites' after phase 7, each against
the block open then. A leaf whose site's block is open is short when the
attention left holds no more agent minute of it, and a leaf whose site's
block is not open is short when the attention left, less the open block's
padding, cannot open one. `ran_out`, `days_run_out` and `binding` keep
their meaning, so [the version rule](#the-version-rule) is not triggered.
A day's values can still differ from an earlier Gantry's for the same
placements: the lanes rows of tiers 1 and 2 now come before the later
tiers' blocks, so a leaf whose turn comes after such a row took the last
lane waits on lanes alone where it used to wait on attention.

A leaf with no lane minute free waits on lanes alone, with one exception:
when a single placement takes the last lane minute and the last of the
attention its block had for that leaf, the day records both pools. A day's
own list covers all 10 days; each pool's days run out, and the binding
pool, read the planned week only. The **binding pool** is the
one that ran out on the earliest day; on the same day, the one that ran out
on more days of the planned week; still tied, operator attention hours.
When neither ran out, the binding pool is null and the plan says
"no pool ran out".

### When there is no plan

The plan has status `no_plan` and one reason, the first that applies, when:

| Reason | When |
|---|---|
| no portfolio_file is configured | the installation config sets no `portfolio_file` |
| the portfolio file's settings were not read | the file is missing or unreadable, has no settings block, holds invalid TOML or a `portfolio_version` this Gantry does not read |
| devagent declares T h a week, but the portfolio file reserves every weekday's attention hours | devagent's capacity declaration is used, and reserved blocks take every minute it could go to: `attention_hours` gives some weekday a minute and none has one left after its reserved blocks, or `attention_hours` is not set and each day from Monday to Friday is reserved for its 24 hours |
| devagent declares T h a week, under one minute on every working day | devagent's capacity declaration is used, and its weekly hours come to under half a minute on every weekday they are scaled onto or spread over |
| devagent's capacity declaration is invalid (N violations), and the portfolio file declares no attention hours | devagent's capacity declaration breaks a rule, and `attention_hours` is absent or was rejected, or every weekday has under half a minute left after its reserved blocks |
| devagent's capacity declaration is invalid (N violations), and the portfolio file declares no agent lanes | devagent's capacity declaration breaks a rule, and `agent_lanes` is absent, was rejected or is 0 |
| the portfolio file declares no usable attention hours after reserved blocks on any weekday | devagent declares no capacity, and `attention_hours` is absent or was rejected, or every weekday has under half a minute left after its reserved blocks; or devagent's capacity declaration is used, and `attention_hours` is set and gives every weekday under half a minute |
| the portfolio file declares no usable agent_lanes, or 0 | devagent declares no capacity, and `agent_lanes` is absent, was rejected or is 0 |
| no site was built | no site's page was rendered |

The four reasons that name devagent are its capacity declaration's (#39).
A declaration is used when it has a `[capacity]` table and breaks no rule:
it gives the week's attention hours and the lanes, so the portfolio file
need set neither. In a reason, T is the declared `operator_hours_per_week`
in its shortest decimal form, never rounded (`30`, `30.5`, `0.001`), and
"N violations" counts the lines devagent's reader writes about the rules
the declaration breaks: "1 violation" for one.

No reason holds a path. A site counts as built when its page was rendered;
a page that later fails to write does not change the plan. A `no_plan` plan
leaves the build's exit code as it was.

## The plan file

The full build writes the plan to `<output_dir>/week-plan.json` and prints
its path after the index's. It is UTF-8 JSON with sorted keys and one
trailing newline, written through a temporary file in the output folder and
renamed into place, and it is written nowhere else. A `no_plan` plan is
written too, so an earlier build's plan never stands in for this one.
`--site` neither writes nor removes the file.

**The plan file holds private leaf keys, and the portfolio file's attention
hours and reserved blocks, and never belongs in a repository.**
The commit check treats `week-plan.json` as generated output, and a real
installation's output folder lies outside this repository.

When the file cannot be written, the build prints
`gantry: error: cannot write week-plan.json: <reason>` on stderr, naming no
path, leaves no partial file and exits 1; the views still received the
plan. When the output folder itself cannot be made, the write is not
attempted and that folder's one error line stands
([worksite-page.md](worksite-page.md#every-site-and-the-index)). When only
the index cannot be written, the plan file still is.

### Fields

Hours are minutes divided by 60, and a date is `YYYY-MM-DD` text.

| Field | Holds |
|---|---|
| `plan_version` | the format version, 1 |
| `status`, `reason` | `planned` with a null reason, or `no_plan` with its reason |
| `as_of` | the build's as-of date |
| `allocation_rule` | the rule that divides a day between sites, as text |
| `horizon_days`, `planned_week` | the 10 working days, and the first 5 of them |
| `mapping` | the attention mapping in use: its `values`, its `source` and why the portfolio file's was `rejected`, or null |
| `pools` | attention, then lanes: each pool's `name`, its `capacity_source` and its `days_run_out` in the planned week |
| `binding` | the binding `pool` and its `first_day`, both null when no pool ran out, and `text`: the pool's name or "no pool ran out" |
| `unestimated_planned` | how many distinct unestimated leaves are placed in the 10 days |
| `days` | one record per working day, below |
| `leaves` | one record per placed leaf, sites in config order, then by first placement |
| `sites` | one record per built site, in config order |
| `report` | devagent's lines about its capacity declaration, on a `no_plan` plan too, then what the plan found across sites: one entry per line or finding, empty when there is none |
| `notes` | what the plan says about the intended shares it applied, by date; empty on a `no_plan` plan |

A day holds its `date` and `weekday` (`mon` to `sun`), its
`attention_hours_declared` and its `attention_hours` after reserved blocks,
its `reserved` blocks (`start`, `hours`, `label`), its `lane_hours`, its
`blocks` in order, its `lanes_row`, what was `left` of each pool
(`attention_hours`, `lane_hours`), the pools that `ran_out` and its count
of `unestimated` leaves. A block holds its `number`, `site`,
`length_hours`, `start_cost_hours` and `leaves` in plan order, each with
its `site`, `key`, `agent_hours` and `operator_hours`. A lanes-row entry
holds `site`, `key` and `agent_hours`.

A day's `attention_hours_lost_to_cap` is what of devagent's weekly hours
does not fit in the day after its reserved blocks (#39), and
`capacity.attention.weekdays_lost_to_cap` lists the same loss by weekday,
each with its `weekday` (`mon` to `sun`) and `hours`, Monday first and only
the weekdays with a loss: a weekday that keeps none of its hours is no day
of the plan, so its loss is in that list alone.

A leaf holds its `site` and `key`, its `rate`, `need`, `tag` and `basis`,
its total `agent_hours`, whether it is `unestimated`, its `state`, its
`hours_after_horizon` and the `days` it is on, its `unresolved_local`
count: its own and its open rows' `depends_on` entries that name nothing,
and its `tier`: `p0`, `started` or `fair_share`.

A site holds its `name`, its `ratio_source` (the
[site ratio source](#the-site-ratio-source): `declared` when devagent's
capacity declaration gives a ratio the plan uses (#39), else `estimated`
until #40 passes a measured one), its `attention_hours_charged` (its block
lengths over the 10 days, start costs and minimum-length padding included), its
`lane_hours_charged` (the agent hours placed), its `attention_hours_target`
and `lane_hours_target` (each pool's fair-share target at the horizon's
end: [Fair share between sites](#fair-share-between-sites)), its
`unestimated_planned` count, its `unplanned` list (`key`, `reason`, `state`,
`unresolved_local`), its `shares` (one per horizon day: the `date` and its
`share` in percent, null on a day the site is share-less), its
`shareless_days`, its `share_note` (null, or the text "no intended share
set; planned only into capacity left after the sites with shares" when
`shareless_days` is not empty) and its `p0` list: each leaf whose
effective priority is 0 ([Priority tiers](#priority-tiers)), in `WBS.md`
order, by `key`, with `held_by`, the `<site>:<issue>` of each
cross-site entry that holds it. An unplanned leaf's `reason` is one of
`paused`, `blocked_externally`, `parked`, `waits_on_cycle`,
`cross_site_cycle`, `waits_on_site_absent`, `waits_on_unresolved`,
`waits_on_unplanned` and `beyond_horizon`
([Unplanned leaves](#unplanned-leaves)). A paused site's record is like any
other's: nothing is charged to it, its targets are 0 and every open leaf of
it is in `unplanned`.

A note holds its `kind`, its `date`, the `projects` it names and its
`text`. A `shares_dropped` note names, in the allocation's own order, the
projects whose shares were dropped that day because they are not built
sites: "shares dropped on <date> for projects that are not built sites:
<names>". A `no_allocation` note names none: "no intended shares apply on
<date>; every site planned with equal shares". Notes go by date, and on one
date `shares_dropped` before `no_allocation`.

The fields added by #37 (`notes`, a leaf's `tier`, and a site's `shares`,
`shareless_days`, `share_note`, `p0`, `attention_hours_target` and
`lane_hours_target`) are read by the week view (#38), except `share_note`:
the week view words the share-less line itself, so `share_note` stays
provisional until a view reads it. A site may hold more than one block a
day, and a leaf may sit in two blocks of one day; the week view shows
both; #33 and #24 must not assume either away. A `p0` entry's `held_by`
keeps absent and unresolved outcomes as well as open ones, since each holds the leaf (#62), and
`share_note` is set for the whole site once any day is share-less. The
targets are the values at the horizon's end; the file holds no per-day
target.

A report entry holds its `kind`, its `leaves`, each a `site` and `key`,
and its `text`. There are three kinds, and entries go in this order. A
`capacity_invalid` entry is one rule devagent's capacity declaration breaks
and a `capacity_unknown_key` entry one key of it that devagent does not
know (#39): its `text` is the line devagent's reader writes about it, its
`leaves` are empty, and these entries keep devagent's order, on a `no_plan`
plan too. A `cross_site_cycle` entry has a null `text` and names its
`leaves` in devagent's config order of site, then `WBS.md` order; such
entries go in the order of their first leaf. The week view lists the
capacity entries' lines ([The week view page](#the-week-view-page)); no
view reads a `cross_site_cycle` entry yet (the week view shows cycle leaves
by their reason), so the shape of `report` stays provisional until one
does (#167); a kind with another shape changes the pinned shape and this
section in the same change.

A planned file, from `tests/fixtures/week_plan/binds_lanes/`, with `days`,
`leaves`, `sites` and `notes`, and the site's `shares`, cut to their first
item:

```json
{
  "allocation_rule": "fair share by intended shares",
  "as_of": "2026-01-19",
  "binding": {
    "first_day": "2026-01-19",
    "pool": "agent lanes",
    "text": "agent lanes"
  },
  "capacity": {
    "attention": {
      "factor": null,
      "rule": "portfolio_file",
      "total_hours": null,
      "weekday_sum_hours": null,
      "weekdays_lost_to_cap": []
    },
    "capacity_schema": null,
    "declaration": "not_declared",
    "declared_ratio": null,
    "lanes": {
      "hours_lost_to_cap": 0.0,
      "lane_hours_per_day": 6.0,
      "lane_hours_per_week": null,
      "max_lanes": 1,
      "working_days": 5
    },
    "paused": [],
    "token_budget": null
  },
  "days": [
    {
      "attention_hours": 8.0,
      "attention_hours_declared": 8.0,
      "attention_hours_lost_to_cap": 0.0,
      "blocks": [
        {
          "leaves": [
            {
              "agent_hours": 2.0,
              "key": "Issue-1",
              "operator_hours": 0.2,
              "site": "alpha"
            }
          ],
          "length_hours": 1.0,
          "number": 1,
          "site": "alpha",
          "start_cost_hours": 0.25
        }
      ],
      "date": "2026-01-19",
      "lane_hours": 6.0,
      "lanes_row": [
        {
          "agent_hours": 4.0,
          "key": "Issue-2",
          "site": "alpha"
        }
      ],
      "left": {
        "attention_hours": 7.0,
        "lane_hours": 0.0
      },
      "ran_out": [
        "agent lanes"
      ],
      "reserved": [],
      "unestimated": 0,
      "weekday": "mon"
    }
  ],
  "horizon_days": [
    "2026-01-19",
    "2026-01-20",
    "2026-01-21",
    "2026-01-22",
    "2026-01-23",
    "2026-01-26",
    "2026-01-27",
    "2026-01-28",
    "2026-01-29",
    "2026-01-30"
  ],
  "leaves": [
    {
      "agent_hours": 2.0,
      "basis": "scored: h: 1",
      "days": [
        "2026-01-19"
      ],
      "hours_after_horizon": 0.0,
      "key": "Issue-1",
      "need": 1,
      "rate": 0.1,
      "site": "alpha",
      "state": "pending",
      "tag": "scored",
      "tier": "fair_share",
      "unestimated": false,
      "unresolved_local": 0
    }
  ],
  "mapping": {
    "rejected": null,
    "source": "default",
    "values": [
      0.0,
      0.1,
      0.25,
      0.5,
      1.0
    ]
  },
  "notes": [
    {
      "date": "2026-01-19",
      "kind": "no_allocation",
      "projects": [],
      "text": "no intended shares apply on 2026-01-19; every site planned with equal shares"
    }
  ],
  "plan_version": 1,
  "planned_week": [
    "2026-01-19",
    "2026-01-20",
    "2026-01-21",
    "2026-01-22",
    "2026-01-23"
  ],
  "pools": [
    {
      "capacity_source": "portfolio file",
      "days_run_out": [],
      "name": "operator attention hours"
    },
    {
      "capacity_source": "portfolio file",
      "days_run_out": [
        "2026-01-19",
        "2026-01-20"
      ],
      "name": "agent lanes"
    }
  ],
  "reason": null,
  "report": [],
  "sites": [
    {
      "attention_hours_charged": 1.0,
      "attention_hours_target": 0.45,
      "lane_hours_charged": 14.0,
      "lane_hours_target": 14.0,
      "name": "alpha",
      "p0": [],
      "ratio_source": "estimated",
      "share_note": null,
      "shareless_days": [],
      "shares": [
        {
          "date": "2026-01-19",
          "share": 100.0
        }
      ],
      "unestimated_planned": 0,
      "unplanned": []
    }
  ],
  "status": "planned",
  "unestimated_planned": 0
}
```

The `no_plan` form keeps every key:

```json
{
  "allocation_rule": "fair share by intended shares",
  "as_of": "2026-01-19",
  "binding": {
    "first_day": null,
    "pool": null,
    "text": "no pool ran out"
  },
  "capacity": {
    "attention": null,
    "capacity_schema": null,
    "declaration": "not_declared",
    "declared_ratio": null,
    "lanes": null,
    "paused": [],
    "token_budget": null
  },
  "days": [],
  "horizon_days": [],
  "leaves": [],
  "mapping": {
    "rejected": null,
    "source": "default",
    "values": [
      0.0,
      0.1,
      0.25,
      0.5,
      1.0
    ]
  },
  "notes": [],
  "plan_version": 1,
  "planned_week": [],
  "pools": [
    {
      "capacity_source": "portfolio file",
      "days_run_out": [],
      "name": "operator attention hours"
    },
    {
      "capacity_source": "portfolio file",
      "days_run_out": [],
      "name": "agent lanes"
    }
  ],
  "reason": "no portfolio_file is configured",
  "report": [],
  "sites": [],
  "status": "no_plan",
  "unestimated_planned": 0
}
```

The `report` list from `tests/fixtures/week_plan/cross_site_cycle/`:

```json
[
  {
    "kind": "cross_site_cycle",
    "leaves": [
      {
        "key": "Issue-1",
        "site": "a"
      },
      {
        "key": "Issue-2",
        "site": "a"
      },
      {
        "key": "Issue-1",
        "site": "b"
      }
    ],
    "text": null
  },
  {
    "kind": "cross_site_cycle",
    "leaves": [
      {
        "key": "Issue-5",
        "site": "a"
      },
      {
        "key": "Issue-7",
        "site": "b"
      }
    ],
    "text": null
  }
]
```

A `p0` entry held by another site, from `tests/fixtures/week_plan/p0/`
(site `a`):

```json
{
  "held_by": [
    "b:Issue-3"
  ],
  "key": "Issue-3"
}
```

A `shares_dropped` note, the first of `tests/fixtures/week_plan/share_unbuilt/`:

```json
{
  "date": "2026-01-19",
  "kind": "shares_dropped",
  "projects": [
    "zz"
  ],
  "text": "shares dropped on 2026-01-19 for projects that are not built sites: zz"
}
```

A share-less site, `c` of `tests/fixtures/week_plan/no_share/`, with
`shares` and `shareless_days` cut to their first item:

```json
{
  "name": "c",
  "share_note": "no intended share set; planned only into capacity left after the sites with shares",
  "shareless_days": [
    "2026-01-19"
  ],
  "shares": [
    {
      "date": "2026-01-19",
      "share": null
    }
  ]
}
```

### The version rule

Adding a field is compatible and does not change `plan_version`. So is a
new value in an enumerated text field other than `status` (an unplanned
reason, a capacity source, the allocation rule's text), and such a value
may also stop being given: a reader acts only on `status`, on whether the
binding pool is null and on the two pool names, and shows any other value
it does not know as text. A new `status` value changes `plan_version`, and
so does removing or renaming a field or changing its type or meaning.
A file written before a field was added lacks it: a reader takes a missing
`report` as empty and a missing `unresolved_local` as 0, and a missing
`notes`, `shares`, `shareless_days` or `p0` as empty and a missing
`share_note`, `tier`, `attention_hours_target` or `lane_hours_target` as
not given.

### The pinned shape

`tests/fixtures/week_plan/shape-v1.json` pins the file's shape at version
1; it is written by hand, never generated from the planner. It holds
`plan_version`, `status_values` and `shape`, a node. A node is a type name
(`string`, `date`, `number`, `integer`, `boolean`), `["null", <node>]` for
a value that may be null, `{"array": <node>}` or
`{"object": {<field>: <node>, …}}`. Every listed field is required and an
unlisted field fails, so `tests/test_plan_file.py` fails on any shape
change until the field is added to the pinned shape. Only `status` is
checked against values. `shape-v1.json` may only gain fields: a removal, a
rename or a type change goes into a new `shape-v2.json` with
`PLAN_VERSION = 2`. It pins what the current writer writes; a version 1
file from an earlier writer may lack a field added since, and the version
rule says how a reader takes it.

### Determinism

The same inputs and as-of date give the same plan and a byte-identical file
on any machine: the planner charges whole minutes and keeps its targets as
exact fractions compared with those charges, breaks every tie by a stated
order, and the file holds no build time. A rebuild 15 minutes later
does not reshuffle today.

## The week view page

Every full build writes `week.html` beside the index, after the site pages
and before `index.html`, and the index links it as `Week view` (#38). It
renders the build's plan and nothing else: it computes no planning value,
and `gantry build --site` writes no week page. The page is read-only in v1.
It holds the installation's titles and keys, like every page in the output
folder. Every text below is a constant in `gantry/views/week.py`, apart
from the model band's `think`, `check` and `none`, which are constants in
`gantry/views/leafbox.py`; `{…}` marks a value from the plan.

**Hours.** Every hour figure is rounded once, half up, to two decimals,
with trailing zeros dropped and an `h`: 135 minutes is "2.25 h", 120 is
"2 h", 7 is "0.12 h". A fair-share target is an exact fraction and rounds
the same way, so a target of 3.025 hours is "3.03 h". A share is rounded
the same way and shown in percent ("33.33%"). A rate is written as is,
with no exponent ("0.000001"). A scaling factor is rounded the same way to
four decimals ("1.5", "0.6667").

**Above the days**, in order:

- `the as-of date {as_of} is not a working day; the plan starts {weekday} {date}`,
  only when the as-of date is not one of the plan's days;
- the binding pool, `binding: {pool} (ran out first on {weekday} {date})`, or
  `no pool ran out in the planned week`; the line carries
  `data-figure="binding"` and `data-source`, `week plan as of {as_of}`;
- the allocation rule:
  `{rule} (Gantry v1); devagent's forecast (mtibbits/devagent#745) may allocate differently`;
- the mapping:
  `attention mapping: {values} operator hours per agent hour for needs 0 to 4, {source}`,
  where the source is `default` or `from the portfolio file`, then
  `the portfolio file's mapping was not used: {reason}` when it was not;
- `capacity from: {parts}`, each part a pool's name and its capacity source,
  then what the plan says of devagent's capacity declaration (#39), each
  line only when it applies:
  - how its weekly hours were laid on the weekdays:
    `devagent declares {total} a week; the portfolio file's weekdays after reserved blocks sum to {sum}; each day is scaled by {factor}`
    when they were scaled by a factor other than 1, or
    `devagent declares {total} a week; the portfolio file sets no attention_hours, so it is spread evenly over Monday to Friday`;
  - `{weekday}: {lost} of devagent's weekly hours do not fit in the day after its reserved blocks`,
    once for each weekday that lost hours to its day's cap, Monday first,
    from the plan's `capacity.attention.weekdays_lost_to_cap`: a weekday
    that kept none of its hours is no day of the plan and is named here
    all the same;
  - `devagent's capacity declaration is invalid; the portfolio file's capacity is used`
    when the declaration breaks a rule, then a list of the lines devagent's
    reader writes about it, one per violation, as written (the report's
    `capacity_invalid` entries);
  - a list of its unknown-key lines, as written (the report's
    `capacity_unknown_key` entries);
  - `devagent declares 0 operator hours per agent hour; unscored leaves run in lanes without blocks`
    when its ratio is used and planned as rate 0, which is 0 and any ratio
    under half a part per million; or, for a ratio that is not used, the
    plan's reason as written;
  - `token budget (not scheduled): {n} {unit} a week`, its effective weekly
    token budget in plain digits, which nothing is planned with;
- the count, `Includes {n} unestimated leaves` or `Includes 1 unestimated leaf`,
  carrying `data-unestimated` (empty when the count is 0);
- the share-less sites, `{n} sites have no intended share set; they are planned only into capacity left after the sites with shares`
  or `1 site has no intended share set; it is planned only into capacity left after the sites with shares`,
  then per site `{site}: on every planned day` or `{site}: on {days}`;
- each of the plan's notes, as written;
- the P0 leaves, each linked and marked `held by {holders}` when a
  cross-site entry holds it, captioned `P0 leaves`.

**The legend.** Below those lines, and only with a plan, a legend reads
`critical: slack 0 on its own site's network` and
`near-critical: slack 1–3 on its own site's network`, each beside a swatch
of that box fill, then
`P0: in the P0 tier, because its own priority or that of a leaf it blocks, directly or not, is P0`.
It then explains the workflow phase row (#173), as the worksite page's
legend does: `Workflow phase row, left to right: plan, implement, check, merge, wrap up, from each issue's checklist.md`,
one cell of each look beside its word (`done`, `current`, `stuck`,
`parked`, `blocked outside`, `ahead`, `not in this workflow`, `unknown`),
and `The row shows this build machine's copy of each checklist, which may lag an issue running on another machine.`
These are workflow phases, devagent's five spans of an issue's steps, not
the eight phases each planned day runs in ([Priority tiers](#priority-tiers)).

**The days.** All 10 days are on the page; with the page script, the
first 5 show and the `5 days` and `10 days` buttons show or hide the rest.
With scripts off, every day and list shows and the buttons do not. Each
day is headed `{weekday} {date}`, the as-of day marked `today`, then
`{hours} attention after reserved blocks` and each reserved block,
`reserved {start}, {hours}: {label}`, or `reserved {start}, {hours}` when it
has no label. Each block is headed with its site, its length and
`start cost {hours}`; a day with none reads `no block planned`. The day's
lanes row follows under `runs in a lane; no block needed`. The row mixes
sites, so its leaves sit under one sub-heading per site, the sites in the
order they first appear in the row and each site's leaves in plan order.
Then `left: {attention_pool} {attention}; {lanes_pool} {lanes}`.

Each placement is drawn as the worksite network's leaf box
([docs/worksite-page.md](worksite-page.md#the-network)), by one rule both
pages share (`gantry/views/leafbox.py`):

- **The top line** holds the leaf's key cut to 16 characters, its own
  priority as written cut to 6 (none when it has none), and its agent hours
  in that placement. A cut value ends in `…`.
- **The title**, cut to 40 characters and wrapped to two lines of 25, is
  linked to the leaf on its worksite page when that page was written, and
  unlinked when it was not. A leaf with no title shows its key there,
  linked the same way, where the worksite box shows no title line.
- **The workflow phase row**, when the leaf's worksite page model has one,
  sits between the title and the facts: the same five cells as the
  worksite box ([docs/worksite-page.md](worksite-page.md#the-phase-row)),
  in an `<svg>` of their own whose label and tooltip are the row as text,
  `phases: {runs}` or `phases unknown: {why}`. The row is read from
  `phases` and `phases_why` in that site's page model, and kept only when
  it is five fixed look names, either all `unknown` with a text reason or
  none `unknown`; anything else draws no row, as does a leaf with no
  checklist. A schedule-only leaf has its row too. The page's embedded
  data is unchanged.
- **The facts** follow, as before: `unestimated` or `unestimated (3 pr)`
  for an unestimated leaf with a non-time `est:`, its state when it is not
  pending (`in progress`, `stuck`, or the state as written), `P0` on a P0
  leaf (an unknown tier as written), `need {need}, {tag}` or
  `{rate} h per agent hour, {tag}` when it has no need, and its basis, so a
  tier-less leaf reads "no model tiers or flags recorded". The tag is
  `scored`, `measured`, `declared` or `estimated`
  ([The four sources](#the-four-sources)). A leaf split across blocks or
  days reads `continues {weekday} {date}` with its next placement's day on
  every placement but its last; on its last, a leaf with hours left after
  the 10 days reads `continues after {weekday} {date} ({hours} left)`,
  naming the plan's last day.
- **The model band**, when the leaf records a model tier, reads
  `think` and its thinking model and `check` and its checking model, each
  cut to 9 characters and `none` when that half is not recorded, shaded as
  on the worksite page. A leaf with no model tiers has no band.

The box carries the worksite box's classes: its slack class (`crit` at
slack 0, `near` at 1 to 3, `free` above), its state class, `unest` when it
is unestimated, and `xsite` when it waits on another site. The slack is the
leaf's slack on its own site's network, read from that site's page model in
the same build; an unlinked leaf, or a leaf whose site has no page model,
has none. The fill is by slack, the outline by state, and a leaf that waits
on another site then takes the `xsite` outline colour and width over its
state's, keeping a held state's dash. The colours are the worksite page's,
from the one sheet both pages insert (`gantry/templates/leaf_colours.css`).

**Unplanned leaves.** Under `Unplanned leaves`, each built site lists its
unplanned leaves with their reason in words
([Unplanned leaves](#unplanned-leaves)):
`paused in devagent's capacity declaration`, `blocked externally`, `parked`,
`waits on a cycle`, `part of a cycle across sites`,
`waits on a site not in this installation`,
`waits on a reference that could not be resolved` and
`waits on an unplanned leaf`, and a reason the page does not know as
`not planned: {code}`. Leaves `beyond_horizon` are counted,
`{n} leaves beyond the 10 days` or `1 leaf beyond the 10 days`, and listed
behind a `show or hide` button. A site with none reads
`no unplanned leaves`. Each site that was not built follows as
`{name}: not built: {reason}`, including a site whose page could not be
written: its leaves stay in the days, unlinked.

**Per site.** Under `Per site`, a table with the columns `site`,
`share on {weekday} {date}` (the plan's first day; a site with no share
that day reads `no intended share set`) and each pool, where each pool's
cell is `{charged} charged, {target} target` over the 10 days.

**No plan.** When the plan's status is `no_plan`, the page shows
`no week plan: {reason}` and the stamp; the index still links it. When
devagent declares no capacity that is all. Otherwise the capacity lines
above follow the reason, each only when it applies: `capacity from: {parts}`
when the declaration is used, the note of an invalid declaration with its
violation lines, the unknown-key lines, the ratio line and the token
budget. With an invalid declaration no capacity is used on this page, so
it has no `capacity from:` line and the note reads
`devagent's capacity declaration is invalid` and no more. A `no_plan` plan
holds no attention record, so the scaling and the cap lines are never
here.

**Errors.** When the page cannot be rendered, the build prints
`gantry: error: cannot render week.html: <type>: <message>`; when it cannot
be written, `gantry: error: cannot write week.html: <reason>`. Either exits
1, the index is written without its link and the plan file is still
written. A `week.html` from an earlier build is not deleted
([docs/worksite-page.md](worksite-page.md#every-site-and-the-index)).

The page reads the plan's file form. Of `report` it renders the lines of
the two capacity kinds (#39) and no other entry: it shows cycle leaves by
their reason, and #167 will read `report`. Its embedded
data holds the plan, then, per leaf the page names, its site, key, title,
`est:` value, own priority and model tiers.
