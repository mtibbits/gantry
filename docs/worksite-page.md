# The worksite page

A worksite page shows one site's work: its leaves, the links between them,
their priorities, a schedule across agent lanes, and what Gantry could not
use. `gantry build --config FILE` writes every site's page and the index,
and `--site NAME` writes one site's page
([Every site and the index](#every-site-and-the-index)). This document fixes what the
page is built from ([Reading `WBS.md`](#reading-wbsmd)), what it shows
([The page](#the-page)) and how it keeps record values inert
([Page safety](#page-safety)). Every Gantry view reads the same parsed data
(`gantry/model.py`), produced by `gantry.wbs_stopgap.read_wbs` until devagent
exports its own.

## Reading `WBS.md`

Gantry reads a site's `WBS.md` with devagent's grammar
(`scripts/lib/wbs-parser.py`), ported rule by rule; it never calls or copies
devagent's parser. Where Gantry adds a rule (names, the report, links), this
section says so. Nothing in the file stops the read: what Gantry cannot use
it reports, and it reads the rest.

### Bytes and lines

The file is read as bytes and decoded as strict UTF-8. A file that cannot be
opened or read (`WBS.md cannot be read: <reason>`) or is not valid UTF-8
(`WBS.md is not valid UTF-8 at byte N`) gives no nodes and one
`site_unreadable` entry; the message never names the path. A byte-order
mark is kept as text, so a first line that starts with one is not a node.
CRLF reads exactly like LF. Lines are numbered as Python's `str.splitlines`
counts them, which is devagent's convention; an editor may count differently
on a vertical tab, form feed, `\x1c`–`\x1e`, U+0085, U+2028 or U+2029.

### Node lines

A node line is `- [g] text`, after any leading whitespace, where `g` is one
of ` `, `x`, `-`, `!`, `~`, `?` or `P`, followed by whitespace and at least
one more character. Whitespace alone after the glyph, two characters or
more, makes a node with an empty title (a leaf of that shape is reported
`no_issue`, like any other issue-less leaf). The first `# ` heading and
`<!-- wbs-* -->` directives create no nodes; Gantry ignores both.

A line that starts like a node and is not one is reported `skipped_line`:
an unknown glyph (`- [X] text`), an empty `- [] text`, or a valid glyph with
no whitespace and text after it (`- [ ]`, `- [ ] ` with one space,
`- [x]text`). Lines that are not shaped like a node at all are prose and are
not reported: `- [xx] text`, `-[x] text`, `* [ ] text`, and a link item
`- [text](url)` whose text is two or more characters. A link item with
one character of text or none (`- [1](url)`, `- [x](url)`, `- [](url)`) is
node-shaped and reported.

`x` and `-` mark a finished node; every other glyph is open.

### Fences

Lines inside a fenced code block are never nodes. A fence opens on a line of
three or more backticks or tildes, after any spaces or tabs, and closes on
the first later line of the same character with a run at least as long and
nothing but whitespace after it. A backtick opener whose info text holds a
backtick (an inline span such as ```` ``` a`b ````) opens nothing. An opener
with no closer is plain text, and the lines after it are read as usual.
Pairing is devagent's. An unclosed opener scans to the end of the file;
Gantry remembers, per fence character, the shortest run that found no closer
and skips any later opener at least as long, so those scans number at most
one per distinct failing run length.

### Nesting and outline

A node's depth is its indent, tabs expanded to two columns, divided by two
(rounded down), plus one. Its parent is the nearest earlier node of smaller
depth, so an odd indent rounds down and a jump of several levels still nests
one level. Its outline is its parent's outline, a dot, and its 1-based
position among the parent's children (`2.3.1`); top-level nodes are `1`,
`2`, and so on.

A node with children is a **row**; a node without is a **leaf**. Only leaves
are work: counts, links and priorities are over leaves. A leaf's `epic` is
its top-level ancestor's key (`None` for a top-level leaf); a row's `parent`
is its parent row's key. A row holds every node whose outline starts with
the row's outline and a dot.

### Title and metadata

The metadata block is a trailing `{...}` that holds no other brace; anything
before it is the title, so `Brace {in} title {issue: Issue-7}` has the title
`Brace {in} title`. Gantry finds the block in linear time, with the same
result as devagent's regular expression. Inside it, `key: value` pairs are
separated by commas: a `[...]` value is a list, a quoted value runs to its
closing quote (commas included), a plain value runs to the next comma, and a
later duplicate key wins. So `satisfies: a:X, a:Y` reads as two keys,
`satisfies: a:X` and `a: Y`; write a list as `[a:X, a:Y]`. The trace reports
such a key as a probable unbracketed list
([requirements-format.md](requirements-format.md#the-satisfies-key)).

### Identity

Each node has a key, unique in its file. A node's `issue:` id is a non-empty
single value; a list or an empty value leaves the node issue-less. The first
node holding an id is keyed by it. A later node with the same id is
reported `duplicate_issue`, keyed by its outline, and every reference to the
id resolves to the first. An issue-less node is keyed by its outline. When
an outline key equals any `issue:` id in the file, `~<line>` is appended
until it equals none, and the node is reported `key_collision` with the line
of the id its outline equalled. An issue-less leaf is reported `no_issue`;
an issue-less row is not.

Nodes are value objects that hold a `dict`, so they are unhashable: key
collections by `.key`, never by the node.

### `depends_on`

`depends_on` is one entry or a list. An entry is:

- **local** when it has no `:`, or is `<site>:<id>` where `<site>` is exactly
  this site's name and both halves are id tokens (`[A-Za-z0-9][A-Za-z0-9_.-]*`);
- **foreign** when it is `<site>:<id>` with both halves id tokens and another
  site's name (a different case is another name); it stays unresolved and
  unreported, for a later cross-site view: a leaf's in its `deps`, a row's
  only as raw text in `meta["depends_on"]`. A cross-site reader classifies
  a row's entries itself and applies them to the open leaves under the row
  by the rule in [Rows in `depends_on`](#rows-in-depends_on);
- **malformed** otherwise (`beta: Issue-1`, `https://x`, `a:b:c`,
  `:Issue-1`), reported `malformed_ref` once and otherwise ignored.

### Resolution

A local entry resolves only to an `issue:` id in the same file; one that
names none is reported `unresolved_ref`. An outline number is never an id,
so an entry such as `2.1` is reported even when `2.1` is an issue-less
leaf's key. Every node's entries are checked this way, open or finished, and
`bad_priority` likewise covers every node.

### Open nodes only

Links, cycles, held-out keys and effective priority are over open leaves. An
entry naming a finished leaf is satisfied and adds no link; a finished
leaf's or row's own entries are checked but give no leaf a link. A link is `(prerequisite, dependent)`, kept
once, in the order of the dependent's line, then the prerequisite's.

### Rows in `depends_on`

An open leaf takes its own entries and those of each open row above it. An
entry that names a row, whatever the row's glyph, means the open leaves
under it; when none is open the entry is satisfied. An entry adds nothing,
and is not reported, when it names the leaf itself or a row above it, or
when it comes from a row and names that row or a node inside it.

### Priority

`priority:` is `P0` (most urgent) to `P4` (#10). Anything else, such as `P5`
or `P-1`, is reported `bad_priority` and counts as P3. A missing priority counts as P3
and is not reported. A row's priority is checked but given to no leaf. A
leaf's effective priority is the most urgent of its own and that of every
open leaf that waits on it, directly or through other open leaves; it does
not pass through a finished leaf or a foreign entry. Compute it over the
leaves a page schedules:
`effective([l for l in s.leaves if l.open and l.key not in s.held_out], s.links)`.

### Cycles

A dependency cycle among open leaves is reported `cycle` once, at its first
member's line. Its members, and every open leaf that waits on one of them,
are held out (`waits_on_cycle`, one per leaf): they get no effective
priority, and the rest of the site is read as if they were absent.
`SiteData.links` stays complete, held-out ends included; a consumer filters
by `held_out`. Every walk is iterative, so a long chain cannot exhaust the
stack.

## The report

Each entry is a site, a line (`None` for the whole file), a kind and a
message; entries are in line order, whole-file entries first, then by kind
in this table's order.

| Kind | When it fires | What a page does |
|---|---|---|
| `no_issue` | an open or finished leaf has no `issue:` id | shows the leaf under its outline key and lists it in the report |
| `duplicate_issue` | a second node holds an `issue:` id already used | shows both nodes; references reach the first |
| `key_collision` | an outline key equals an `issue:` id | shows the node under its `~<line>` key |
| `unresolved_ref` | a local `depends_on` entry names no `issue:` id in the file | draws no link and lists the entry |
| `malformed_ref` | a `depends_on` entry is neither an id nor `<site>:<id>` | draws no link and lists the entry |
| `bad_priority` | a node's `priority:` is not `P0`–`P4` | treats it as P3 and lists the value |
| `cycle` | open leaves depend on each other in a loop | lists the members |
| `waits_on_cycle` | an open leaf is in, or waits on, a cycle | shows the leaf as held out, with no effective priority |
| `skipped_line` | `- [X] text`, `- [] text`, `- [ ]`, `- [ ] ` or `- [x]text`: a node-shaped line that is not a node (`- [xx] text`, `-[x] text`, `* [ ] text` and a link item with two or more characters of text are not reported; `- [1](url)` is) | lists the line |
| `no_nodes` | the file has no node lines | shows an empty site |
| `site_unreadable` | `WBS.md` cannot be opened, read or decoded as UTF-8 | shows the site as unreadable, with the reason |
| `no_estimates_file` | the site has no estimates file, or its `estimates` key is `false` | reads hours and tiers from `est:` and the default unit only |
| `estimates_ignored` | a discovered `estimates-*.md` file is not the one read: an earlier date, the same date and an earlier name, or no date | lists the file and why |
| `estimates_unreadable` | the configured file does not exist, or the file cannot be opened, is not valid UTF-8, or leads outside the configured roots | continues as if the site had no estimates file |
| `estimates_column_missing` | an estimates table has no `#`-table column Gantry reads (once per file and column) | reads the rest of the table |
| `estimates_cell_bad` | a row's `hours` cell is empty or not a number or range above 0 | the row gives no hours; its other cells are still read |
| `estimates_row_unmatched` | a row `#N` names no `WBS.md` line | lists the row |
| `estimates_row_duplicate` | a second row names the same `#N` | uses the first row |
| `est_unparseable` | an open leaf's `est:` is in none of the other classes | lists it; the leaf may still get hours from its estimates-file row |
| `unestimated` | a scheduled leaf has no time `est:` and no estimates-file hours | schedules it at the default unit and labels every figure that counts it |
| `no_priority` | open leaves have no `priority:` (one entry for the file) | counts them as P3 |
| `no_model_tiers` | no scheduled leaf has a model tier | shows "no per-leaf model tiers recorded" |
| `bad_h` | a node's `h:` is not one digit from 0 to 4 written as a scalar, or is on a row with children; any state, finished leaves included | lists the value; the leaf's attention need comes from its next source ([week-plan.md](week-plan.md#attention-need)) |

## The optional inputs

`gantry/inputs.py` (`apply_inputs`) gives every open leaf an estimate,
model tiers and flags from what its site records, and computes the site's
totals (#10). Missing data is the normal case: no missing or bad input stops
a build, and each is a report entry.

### Which estimate a leaf uses

1. A *time* `est:` on the leaf (`source` `est`).
2. Else the leaf's row in the estimates file, if its `hours` cell reads
   (`estimates_file`).
3. Else the default unit, `default_unit_hours` (`default`): the leaf is
   **unestimated** and listed in the panel.

A placeholder, non-time or unparseable `est:` does not stop the row from
applying. Model tiers, the effort bucket and flags come from the leaf's row
whenever it has one. Each leaf's details name the source of its hours.

### The `est:` classes

A stopgap for mtibbits/devagent#737; #13 deletes these rules. The value is
read as devagent reads metadata (quotes stripped; a list is unparseable),
and the classes are tried in this order, each a full match:

| Class | Pattern | Meaning |
|---|---|---|
| absent | no `est:` | |
| time | `[0-9]{1,9}(\.[0-9]{1,9})?[hdw]` | `h` hours, `d` 6 agent-hours, `w` 5 days; the number must be above 0 |
| placeholder | `\?[A-Za-z]*` | `?` or `?w` |
| non-time | `[0-9]{1,9}(\.[0-9]{1,9})?[A-Za-z]+` | a quantity in a unit of the site's own, such as `48pr`; the unit is not `h`, `d` or `w` in any case, and the number is above 0 |
| unparseable | anything else | `?3d`, `2-3d`, `0h`, `3H` (units are case-sensitive), `3 h`, free text; listed as `est_unparseable` |

An `est:` on a row with children is never read. A non-time quantity enters
no hour figure: the page sums it per unit in its header.

### The estimates file

A site's `[site.<name>] estimates` key (docs/config.md) names its file, read
whatever its name and never replaced by discovery, or, set to `false`,
turns discovery off. Otherwise Gantry lists the top level of the planning
directory (never below it) for regular files whose names match
`estimates-YYYY-MM-DD*.md`, case-sensitively; a matching link that leads
outside the configured roots is kept too, so its read reports the refusal. The latest date is read; among
files of that date, the name that sorts last. Every other match, and every
`estimates-*.md` whose name holds no valid date, is listed as
`estimates_ignored`. With none, the panel says `no estimates file`.

A configured path that does not exist, a file that cannot be opened, and one
that is not valid UTF-8 are each `estimates_unreadable`, naming the config
key or the file's name, never a path; the site then continues as if it had
no file.

The file is read as bytes, decoded as strict UTF-8 (one leading byte-order
mark dropped) and split on line feeds only. A line starting with `|` is a
table line: its outer pipes are dropped, it is split on `|` (a `\|` is not
an escape) and each cell is trimmed. A table whose first header cell is `#`
is read until the first line that does not start with `|`; only its `#`,
`effort`, `hours`, `thinking`, `checking` and `flags` columns are read, so
`P`, `tier` (a checklist tier, not a model tier) and the rest are not. A row
whose first cell is `#N` joins the leaf whose key is `Issue-N`:

- a row naming a row with children or a finished leaf is ignored;
- a row naming no `WBS.md` line is `estimates_row_unmatched`;
- a second row for the same `#N` is `estimates_row_duplicate`, and the
  first is used;
- a read column missing from a table is `estimates_column_missing`, once
  per file and column;
- an `hours` cell that is empty or not a number or a `lo-hi` range above 0
  is `estimates_cell_bad`; that row gives no hours.

`effort` and `flags` are kept as written.

### The totals

The totals cover the leaves the schedule plans: open, and not held out by a
cycle. They are the contract #11 and the portfolio pages read:

- `hours`: the sum of each leaf's midpoint, the value the schedule uses;
  unestimated leaves count at the default unit;
- `hours_lo` and `hours_hi`: the sums of the low and high ends;
- `unestimated_count`: the leaves on the default unit;
- `non_time`: per unit, `{"quantity": sum, "leaves": count}`, in no hour
  figure.

The panel also counts the open leaves with no priority (`no_priority`, held
out leaves included: it reports input quality, not the schedule) and says
when no leaf records a model tier (`no_model_tiers`).

## The page

`gantry/views/worksite.py` turns a parsed site into the page's data
(`page_model`), draws the network, and fills `gantry/templates/worksite.html`
(`render_page`). The page's own script, `gantry/static/worksite.js`, draws
the schedule and the details. The page is one self-contained file: it works
when opened from a local file, with no server and no network.

The network and the schedule show **open work**: every leaf whose glyph is
open and that no cycle holds out. Finished leaves are listed at the foot of
the page instead ([Finished leaves](#finished-leaves)).

### Lanes and states

The network and the schedule have one row per epic, a top-level row that
holds open work, in `WBS.md` order. A row's label is its `issue:` id, if it
has one, then its title. A last `no epic` lane holds the top-level leaves.

A leaf in the `!`, `?` or `P` state is open work like any other: it is drawn
in the network, takes lane time in the schedule, and holds back the leaves
that wait on it. Its box and its schedule bar carry its state's class from a
fixed set (`st-stuck`, `st-blocked`, `st-parked`; `~` is `st-wip`), and its
details name the state:

| State | Glyph | Shown as |
|---|---|---|
| `in_progress` | `~` | `in progress` |
| `stuck` | `!` | `stuck` |
| `blocked_external` | `?` | `blocked` |
| `parked` | `P` | `parked` |

When a site has any stuck, blocked or parked leaf, one line above the
schedule says so: `{n} stuck, blocked or parked leaves are scheduled as if an agent could start them`,
or `1 stuck, blocked or parked leaf is scheduled as if an agent could start it`.

### The network

The network's nodes are the open leaves with a link to another open leaf.
Every link a longer chain already implies is removed (transitive reduction),
and the page says how many. Columns are the earliest slot a leaf can start
in, one slot per leaf; rows are the lanes. A leaf's slack is how many slots
it can slip before the network gets longer. A box is red at slack 0 (the
critical path), tinted at slack 1 to 3, and plain above. A link between two
lanes is dashed, red when it is critical; a top-level leaf's link to an epic
leaf crosses lanes too. Slack 0 can branch, so the critical path the page
names is one chain of critical links from the first slot to the last.
Below the network, the critical path, the near-critical leaves by slack, and
the hours-weighted heaviest chain are listed.

A box shows the leaf's key, its priority as written and its hours, then its
title cut to 40 characters; the full key, title and priority are in the
box's tooltip and in the leaf's details. Hover, select or tab to a leaf to
open its details (state, epic, priority, effective priority, hours, model
tiers, flags, metadata and foreign entries, earliest slot, slack, what it
waits on and unblocks) and to trace everything upstream and downstream.

### Rolling wave

A leaf keeps its full box when its slack is 0, its own priority as written
is P0 or P1 (not its effective priority), it is in progress (`~`), or the schedule
starts it within 10 working days (60 agent-hours at 6 a day) at the default
4 lanes. Every other leaf is a circle whose area is its hours. "Every leaf
as a box" shows them all as boxes; "Rolling wave" returns.

### P0 and devagent's `wbs order`

devagent's `wbs order` counts `P0` as P3 until mtibbits/devagent#889 lands,
so a site with an open P0 leaf ranks some leaves differently from Gantry.
The schedule section then carries one note,
`devagent's wbs order does not know P0 yet (mtibbits/devagent#889), so it ranks these leaves differently: `,
followed by each P0 leaf and each leaf whose effective priority comes from
one, in `WBS.md` order. A site with no such leaf shows no note.

### The model band and table

Where a site records a leaf's model tiers (its estimates file's `thinking`
and `checking` columns, #10), the bottom band
of its box names its thinking tier (left) and checking tier (right), each
shaded by a class from a fixed set (`m-haiku`, `m-sonnet`, `m-opus`,
`m-fable`, and `m-other`, a lavender tint, for any other value, such as
`inherit`, which is shown as text). Each lane's label carries a
table of how many of its open leaves use each tier, linked or not. When no
leaf records a tier, there is no band and no table, and one line says
`no per-leaf model tiers recorded`.

### The schedule

The schedule spreads every open leaf, linked or not, across 1 to 8 agent
lanes (a page control, default 4). The page always uses its own default and
never the portfolio file's lanes: this schedule ignores the operator's
attention. A leaf with an estimate (#10) takes its midpoint; a leaf with
none takes `default_unit_hours` from the installation config and is marked
unestimated. When a lane comes free it takes the ready leaf (every
predecessor finished) with the most urgent effective priority, then the
earliest in `WBS.md`. A lane with nothing ready waits for the next finish.
Leaves with no link appear only here.

Rows group by epic or by lane; by epic, each epic's row expands to list its
leaves ("Expand all epics" and "Collapse all"). One line says which limit
sets the finish: `With {lanes}, the dependency chain sets the finish.` when
the plan ends no later than the heaviest chain of waits, else
`With {lanes}, capacity sets the finish.`; `{lanes}` reads `1 lane` or
`4 lanes`. The schedule is labelled
`agent capacity only; ignores operator attention; the week view is the plan`.

The scheduler is `gantry/schedule.py`, a plain function over leaf data with
no page or site code in it. Its line-for-line twin `gantry/static/schedule.js`
is inlined into the page, and `tests/test_schedule_agreement.py` holds the
two to the same start, end and lane for every leaf, calling the page's copy
through the page-script harness. `tools/check_schedulers.py --config FILE
--out DIR` runs the same comparison on real sites; it refuses a `DIR` inside
the repository and prints only counts.

### Two limits, the summary and the marks

These figures come back from the prototype (#10).

**Two limits on the finish date** sits above the network. One bar sums the
low and high hours of the named critical chain, labelled
`Critical chain ({n} leaves)`; the other sums the scheduled leaves' totals
(`hours_lo` to `hours_hi`), labelled `All {n} scheduled leaves`. Both share
one scale, 560 units for the larger total. Each value carries its N: the
chain's unestimated leaves, and the site's. The caption reads
`At {hpd} agent-hours a day, the critical chain is {a} to {b} working days and all open work is {c} to {d}. With one operator, the total sets the finish date. The chain decides order: its first leaf has to start early enough that the chain is not the last thing left.`
and, when the site's N is above 0, adds ` The total includes {n} unestimated leaves.`

**The schedule summary**, beside the limit line, reads
`With {lanes}, all open work ends around working day {end} and the critical chain around day {crit}. The chain alone needs {chain} days; the work spread evenly across the lanes needs {even}. The lanes are busy {busy}% of the time.`
"Critical chain" here means the named chain, the same one the network
draws. A site where no open leaf waits on another has no chain: the summary
reads
`With {lanes}, all open work ends around working day {end}. No open leaf waits on another, so there is no critical chain; the work spread evenly across the lanes needs {even}. The lanes are busy {busy}% of the time.`,
the chain row of Two limits says `none: no open leaf waits on another` and
draws no bar, and its caption reads
`At {hpd} agent-hours a day, all open work is {c} to {d} working days. No open leaf waits on another, so there is no critical chain, and with one operator the total sets the finish date.` When the site's N is above 0 the summary
adds ` The plan ` followed by
`includes {n} unestimated leaves ({m} on the critical chain)` (or
`includes 1 unestimated leaf ({m} on the critical chain)`) and a full stop;
the no-chain summary adds the plain `includes {n} unestimated leaves` (or
`includes 1 unestimated leaf`) instead.
It carries `data-unestimated` (the site's N) and `data-unestimated-chain`
(the chain's). The server writes it for the default lanes and the script
rewrites it on every change; both sum in the same order and round half up,
so the two texts agree.

**The finish marks.** The schedule draws `mk-end` at the day all work ends
and `mk-crit` at the day the critical chain ends, each carrying the site's
N and a tooltip that names it.

**Priority marks.** A bar whose leaf's own priority is P1 or P2 carries a
dot (`pr-hi`); one whose own priority is P0 carries a ringed dot (`pr-p0`).
The schedule's legend names both, `P0` beside `P1 or P2`.

### Unestimated leaves and their labels

A leaf with no time `est:` and no estimates-file hours is scheduled at the
default unit and is **unestimated** (#10). Its box carries the `unest` class
and its tooltip ends `· unestimated`; its rolling-wave circle is drawn at
the default unit. Every figure built on unestimated hours says so and
carries `data-unestimated="N"`, N = 0 included; the fragment shows only
when N is above 0:

- each lane's label in the network's gutter: that epic's own unestimated
  leaves, with `includes {n} unestimated leaves` (or
  `includes 1 unestimated leaf`);
- the hours note under the network: the unestimated leaves on the heaviest
  chain, ending `; includes {n} unestimated leaves`;
- the caption directly above the schedule, `dates include {n} unestimated leaves`
  (or `dates include 1 unestimated leaf`), with the site's N: any leaf's
  start depends on the whole schedule.

The header names the estimates file read, `Estimates read from {name}, dated {date}.`
or `Estimates read from {name} (no date).` for a configured file whose name
has no date, then the site's non-time quantities,
`Not in any hour figure: {parts}`, where each part is
`{quantity} {unit} ({n} leaves)` and parts are joined with `; `.

### Missing or unreadable

The panel lists each report entry's message, in the report's order, with
two changes:

- the issue-less leaves are one count, at the first of them:
  `{n} leaves have no issue: id and are named by outline position`, or
  `1 leaf has no issue: id and is named by outline position`;
- each leaf held out by a cycle reads `WBS.md line {line}: {key!r} waits on a cycle`.
- the unestimated leaves are one line per `est:` class, at the first of
  them: `{n} leaves with {what} and no estimates-file hours are unestimated: {keys}`,
  or `1 leaf with {what} and no estimates-file hours is unestimated: {keys}`,
  where `{what}` is `no est:`, `a placeholder est:`, `a non-time est:` or
  `an unparseable est:`.
  It is left out of the network and the schedule, and the rest of the page
  renders.

With nothing to list, the panel says `Nothing is missing or unreadable.`
A site whose `WBS.md` has no nodes shows `This site has no leaves.`, one
with leaves but none open shows `This site has no open leaves.`, and one
whose open leaves all wait on a cycle shows
`Every open leaf waits on a dependency cycle, so nothing can be scheduled; the panel lists them.`;
none of them draws a network or a schedule.

### Finished leaves

Below the panel, a plain list headed `Finished leaves` has one line per
finished leaf, in `WBS.md` order: its key, then its issue id or
`no issue id`, then its title, joined by ` · `. It is always shown and never
collapsed, so a link into it lands in every browser. With no finished leaf
it says `No leaf is finished yet.`

### Reading the trace

A site is in the trace when its `[site.<name>]` table sets `requirements`
([requirements-format.md](requirements-format.md)). Every page the build
writes carries the site's trace (#20) in its data, as `trace`, built from
the `SiteTrace` that `gantry/trace.py` computes from the leaves'
`satisfies:` keys; the page never walks the links itself. A site not in
the trace whose leaves name no requirement renders as before: no chips, no
requirements panel and no trace report.

- **Chips.** Each full box in the network carries, in the gap under it, a
  chip for each ID its leaf's `satisfies:` names that resolves, in the
  order written: up to three, then `+{n}` for the rest. A chip shows an ID
  of the site's own namespace without it (`FR-1`) and any other with it
  (`life:BR-1`). A label over 12 characters keeps its code and number whole
  and shortens its namespace with `…` (`devag…:FR-12`), the full ID in the
  chip's tooltip, and the row stays inside the box's width: what does
  not fit folds into `+{n}`. A circle or a schedule bar lists every ID in
  its detail, on a `Satisfies:` line. A business requirement (the
  installation's `br_namespace`) is a chip and a row only: its text is
  never on a worksite page.
- **Requirements panel** (element id `requirements`, headed
  `Requirements`). Shown on a site in the trace, and on any site whose
  leaves name an ID that resolves. Under the heading, the line
  `Select a requirement, or a chip under a box, to mark the leaves that serve it: a solid outline serves it directly, a dashed one through refinement or a use case's needs. Select it again, or press Escape, to clear.`
  The panel lists the site's own requirements (when it is in the trace)
  and every other requirement one of its leaves names, together, grouped by layer
  under `Business requirements`, `Business rules`, `Use cases`,
  `System requirements`, `Functional requirements`, `Quality attributes`,
  `External interfaces` and `Constraints`. A row is the ID, the
  requirement's text and its serving leaves counted: `{n} open` always,
  then `{n} waiting on a cycle` and `{n} finished` when above zero, joined
  by `, `. "Open" counts the serving leaves the page draws. When a row has
  held-out or finished serving leaves, a `serving leaves not drawn` button
  opens a list of them: key, issue id (or `no issue id`), title, then
  `waiting on a cycle` or `finished`, joined by ` · `. A row with neither
  has no such button.
- **Selecting a requirement.** A click or tap on a chip or a panel row, or
  Enter or Space on a focused one, selects it. Every serving leaf the page
  draws is marked, in the network (boxes and circles) and in the schedule:
  a direct one with a solid outline, an indirect one (through `refines:`
  or a use case's `needs:`) with a dashed one, so the two never differ by
  colour alone; the rest dims. A collapsed epic row in the schedule shows
  `{n} lit` for its marked leaves, one form for every count. Selecting the
  same requirement again, or Escape, clears the selection; selecting
  another replaces it. A requirement with no drawn serving leaf marks its
  row and chips and dims nothing.
- **Use cases.** Selecting a use case also marks the panel rows and chips
  of the requirements it needs, and lights its serving set: the leaves
  that name it (direct) and every leaf serving a requirement it needs,
  directly or through refinement (indirect). So a leaf naming an `FR`
  that refines an `SR` the use case needs is lit, marked indirect.
- **Selection and hover.** A selection persists through hover and blur.
  Hovering or focusing a leaf still traces its dependencies while a
  selection is shown, and the trace's look wins while it lasts; leaving
  the leaf restores the selection. A chip is not its leaf: over or on a
  chip, the selection shows, not the dependency trace.
- **Trace report** (element id `trace`, headed `Trace report`). Shown on
  any site whose report has an item, in the trace or not; a site in the
  trace with nothing to report has no `trace` element, though the build
  still prints its line. One line per category, `{label}: {n}`, then its
  items, or `none`: `Unreadable files`, `Malformed entries`,
  `Duplicate IDs`, `Misplaced requirements`, `Refinement cycles` and
  `Undefined IDs`, each item `{source} line {line}: {message}`, or
  `{source}: {message}` for a whole file; then, for a site in the trace,
  `Unserved requirements`, each item an ID, and `Untraced open leaves`,
  each item a key and its title. The unserved label carries the report's
  scope: `served only within this site` while it is `site`, and
  `scope: {scope}` for a scope this Gantry does not know. A requirements
  file is named `the requirements file`, never by its path. Nothing in the
  report stops the page rendering.
- **The `trace` data.** An object with exactly the keys `in_trace`,
  `scope`, `requirements` (one record per row: `id`, `label`, `layer`,
  `text`, `needs`, `light`, `held`, `finished`, `counts`), `leaves` (each
  drawn leaf naming an ID that resolves, with its IDs and labels) and
  `report` (each category with its items). Records are arrays, and the
  light sets are computed at build time from the serving sets: the page's
  script maps a selection to marks with `gantryTrace.marks`, a function
  with no DOM access, and looks every key up in a `Map`.

### Foreign entries

A worksite page does not resolve another site's leaves yet (#68 does): a
full build and `--site` alike keep each foreign entry as text. A leaf's
foreign `depends_on` entries, then those of each open row above it,
top-down, are listed in its details as `{raw} (on another site)`, such as
`beta:Issue-3 (on another site)`.

### Anchors and links

Each leaf on a worksite page, open or finished, has the anchor `leaf-<key>`
exactly once. The key is percent-encoded with nothing kept but letters,
digits and `_.-~`, so an anchor holds no whitespace or markup: `Issue-7`
gives `leaf-Issue-7`, and `a b<c>` gives `leaf-a%20b%3Cc%3E`. The anchor
sits on an element in the page's own HTML, never on one a script builds:

- a leaf the network draws: its node (`g.node`) in the network figure;
- an open leaf only the schedule shows: an empty `span.anchor` at the start
  of the "Schedule across agent lanes" heading;
- a leaf held out by a cycle: an empty `span.anchor` at the start of the
  "Missing or unreadable" heading;
- a finished leaf: its line in the finished list
  ([Finished leaves](#finished-leaves)).

A site's page is `sites/<name>.html` under the output folder, and every
href is relative to the page that holds it. Between pages in `sites/` (a
cross-site link, or another view's link from a worksite page), a leaf's
href is `<name>.html#leaf-<key>`; from a page at the output folder's top
level, such as the index, it is `sites/<name>.html#leaf-<key>`. A browser
matches the fragment, as written, to the id. `gantry/links.py` builds the
anchor and both hrefs, so every view uses the same encoding, and the folder
works opened from disk and under any path prefix.

### The build stamp and sample mode

Every page and the index carry a stamp under their first line. It reads
`built {when}; data as of {date}`, where `{when}` is the build time in UTC
(`2026-11-07 14:03 UTC`) and `{date}` the as-of date. In **sample mode** it
reads `sample data as of {date}` and carries no build time, so two builds of
the same sample data on the same as-of date write byte-identical files.

Sample mode holds when every root the config names resolves inside the
sample directory: the Gantry config itself, the devagent config, every
planning directory (`devdoc_dir`) it names, `output_dir`, and each other
configured path (`portfolio_file`, `fires_list`, and each
`site.<name>.requirements`, `site.<name>.estimates` and
`site.<name>.history`). The sample
directory is `samples/` in the Gantry source tree the running package was
loaded from; an installation with no source tree is never in sample mode,
and a copy of the samples anywhere else is not either. It is the same test
the run's file confinement uses ([Where a build may read and
write](config.md#where-a-build-may-read-and-write)). Both sides are compared after links are
resolved and case is folded as the host folds it, so a folder that does not
exist yet (`samples/out` before the first build) is inside, and `samples-x`
or `samples/../x` is not. No flag or config key switches sample mode on.

## Page safety

Every page follows the rules in [page-safety.md](page-safety.md), and its
tests use that document's checkers and harness. This page applies them so:

- **Text, never markup.** Every record-derived string (titles, ids and
  outline-position names, epic and site names, priorities, metadata values,
  model tiers, flags) reaches the page as text. `gantry/render.py`'s `esc`
  escapes `&`, `<`, `>`, `"` and `'`; attributes are double-quoted, and a
  text is cut before it is escaped. `fill` replaces each `{{NAME}}` in one
  pass, so a value is never read again as a placeholder, escapes every value
  that is not `Markup` (a fragment the page builder made from escaped
  parts), and refuses a placeholder with no value or a value with no
  placeholder.
- **Embedded data.** The page's data is one
  `<script type="application/json" id="gantry-data">` block, written by
  `embed_json`: ASCII JSON with no `NaN`, and `<`, `>` and `&` as `\u`
  escapes, so no value can end the block. Records are arrays, never objects
  keyed by a record value, so a key such as `__proto__` stays data.
- **Fixed classes.** A class name is never built from a record value: the
  model-tier and state classes come from fixed sets with a fallback
  (`m-other`, `st-other`), and slack gives `crit`, `near` or `free`. The
  page's script takes a class from the data only when its own fixed set
  holds it, and a test holds that set equal to `CLASS_TOKENS`.
- **The page's script.** It writes details with `textContent`; where it
  writes markup (the schedule), every record value goes through its own
  `esc`, which escapes the same five characters. Lookups by key are `Map`s.
  A leaf element carries `data-item`, read with `getAttribute`; no selector
  is ever built from a record value.
- **Controls.** The page follows the harness's control convention: a
  `select[data-control="lanes"]` (1 to 8, default 4); buttons
  `data-control="zoom"` (`wave`, `all`), `group` (`epic`, `lane`) and
  `expand` (`all`, `none`, disabled when grouped by lane); and two item
  containers, `network-detail` (the network's `g.node` items) and
  `schedule-detail` (the schedule's `rect.seg` items), whose details open
  on `focusin`. The trace (#20) adds buttons `data-control="requirement"`
  and `requirement-detail`, one per requirements-panel row, valued by the
  requirement's ID, and the chips (`g.chip`, keyed by `data-req`, read with
  `getAttribute`). A selection sets only fixed classes: `req-direct`,
  `req-indirect`, `req-on`, `req-need`, `selecting` and `g-lit`.
  Listeners are delegated on `document`; there are no inline handlers.
- **No resource from any URL.** The head opens with
  `<meta charset="utf-8">`, then the CSP meta with the content
  `default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'`.
  Fonts are system font stacks, and no `url()` other than `url(#id)`
  appears. The CSP guards against loads only: inline script is allowed, so
  escaping stays the defence against injected markup.
- **Limits the harness sets.** The page uses no timer, no storage and no
  layout read; an `svg` holds no `script` or `style` of its own; the
  scripts come last in `body` (`gantry-data`, then `gantry-schedule`, then
  `gantry-page`). `render_page` refuses a script asset that holds
  `</script` or `<!--`. The template and both scripts are package data, LF
  everywhere.

## The index

`<output_dir>/index.html` (`gantry/views/index.py`, `gantry/templates/index.html`)
is headed `Worksites` and carries the build stamp. At the top it lists every
warning the build printed on stderr, with each path in it shown by its
config key or file kind (`the Gantry config`, `the devagent config`,
`output_dir`, `devdoc_dir of <site>`). A heading line,
`Finishes at {lanes} agent lanes: {label}`, says what the finishes count:
at the default 4 lanes, agent capacity only. Then there is one line per
site, in devagent's config order. A built site's line links its page and
gives, joined by ` · `:

- its open leaves, held-out leaves included: `{n} open leaves`, or
  `1 open leaf`;
- its finish: `finishes working day {day}, {date}`, then `; ` and
  `includes {n} unestimated leaves` when it has any, or
  `nothing is scheduled` when no leaf is;
- its report count, the panel's line count: `{n} missing or unreadable`, or
  `nothing missing or unreadable`.

The finish day is the working day the plan ends in: the plan's latest
finish in working days, rounded up, so a plan that needs 8 agent-hours at 6
a day ends in day 2. (The page's schedule summary rounds the same end to the
nearest day, since it says "around".) Working days run Monday to Friday, and
the as-of date is working day 1, even on a weekend; so a plan that ends in
day 3 from Tuesday 2026-01-20 finishes on Thursday 2026-01-22.

A site that was not built shows its name and `not built: {reason}` instead,
and has no link. The index leaves three slots empty for later views: a
headline at the top (`#headline`), one `span.extra` on each site's line,
and a list of links to other views (`#view-links`). The index has no
script; its data block holds the stamp, the as-of date, each site's
summary and the warnings, as shown.

## Every site and the index

`gantry build --config FILE` builds every site devagent's config names, in
its config order. It first reads every site, then renders each site it
read, each in its own `try`, and writes `<output_dir>/sites/<name>.html` for
each, then `<output_dir>/index.html` ([The index](#the-index)). It prints
one absolute path per file it wrote: the pages in config order, then the
index. Then, for each page it wrote whose site is in the trace or whose
trace report has an item, in config order, one line
`trace <site>: unreadable N, malformed N, duplicate N, misplaced N, cycle N, undefined N, unserved N, untraced N`,
every category in that order, zeros included; a site not in the trace
counts `unserved` and `untraced` as 0. The catalog (every requirements
file and the portfolio file) is read once per run, and each site's trace
is built inside its own render step, so a fault there is that site's
`render error:` ([Reading the trace](#reading-the-trace)). Each file is written to a temporary file in its folder, then renamed
into place, so no partial page is ever left; it gets the mode any new file
gets. The build deletes nothing in the output folder: a page an earlier run
wrote for a site that is no longer built stays until the output folder is
replaced (#35 swaps it in whole).

A site that cannot be built does not stop the others. It is listed on the
index with its reason and on stderr as
`gantry: warning: site <name> is not built: <reason>`. The reasons are:

- the site list's and the parser's: an invalid name, no usable
  `devdoc_dir`, no planning directory, no `WBS.md`, or a `WBS.md` that
  cannot be read, is not valid UTF-8 or leads outside the configured roots
  ([config.md](config.md#sites));
- a name that differs only in case from an earlier site's, whatever that
  site's own status:
  `name clashes with {earlier} on a case-insensitive file system`;
- `render error: {type}: {message}` (or `render error: {type}` when the
  message is empty): parsing `WBS.md`, reading the optional inputs,
  building the page's data or filling its template failed;
- `cannot write sites/{name}.html: {why}`, where `{why}` is
  `sites/ is not a folder` when a file stands in its place; each page
  then fails on its own and the index is still written.

A reason names the site and a config key or file kind, never an absolute
path. Two more lines can follow on stderr:

- `gantry: error: cannot write <index path>: <reason>`: the index cannot be
  written. When the output folder itself cannot be made, this is the only
  error line (the not-built lines still come first), and nothing is written;
  when `output_dir` is a file, the reason is `output_dir is not a folder`;
- `gantry: error: no site page was written`.

| Exit | When |
|---|---|
| 0 | the index was written and at least one site page was written (sites not built and warnings do not change it); with `--site`, its page was written |
| 1 | a config error; no site page was written (the index is still written); the index or the output folder cannot be written; with `--site`, any of the errors below |
| 2 | a usage error, such as a missing `--config` |

### One site: `--site`

`gantry build --config FILE --site NAME` writes only that site's page to
`<output_dir>/sites/<NAME>.html`, and no index, and prints the page's
absolute path, then that site's `trace <site>:` line when the full build
would print one. It reads only that site's `WBS.md` and inputs (the site
list still checks each planning directory), every requirements file and
the portfolio file, which the trace needs. It creates `<output_dir>/sites/`
when it is missing and writes the page the same way. Each of these exits 1
with the reason and writes no page (an existing page is kept as it was):

- `--site NAME: no such site in <devagent config>`;
- `--site NAME: cannot build: <reason>`: the site has a problem (an invalid
  name, no planning directory, no `WBS.md`), its `WBS.md` cannot be read,
  its name clashes with an earlier site's
  (`--site NAME: cannot build: name clashes with {earlier} on a case-insensitive file system`),
  or parsing `WBS.md` or reading the inputs failed
  (`--site NAME: cannot build: render error: <type>: <message>`); the
  reason names no absolute path, as in the full build;
- `--site NAME: render error: <type>: <message>`: building the page's
  data or filling its template failed;
- `--site NAME: cannot write <page>: <reason>`.

An invalid name is printed escaped, as on stderr in the full build.
