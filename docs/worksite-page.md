# The worksite page

A worksite page shows one site's work: its leaves, the links between them,
their priorities, a schedule across agent lanes, and what Gantry could not
use. `gantry build --config FILE --site NAME` writes it
([On the command line](#on-the-command-line)). This document fixes what the
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
`satisfies: a:X` and `a: Y`; write a list as `[a:X, a:Y]`.

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

`priority:` is `P1` (most urgent) to `P4`. Anything else, `P0` included, is
reported `bad_priority` and counts as P3. A missing priority counts as P3
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
| `bad_priority` | a node's `priority:` is not `P1`–`P4` | treats it as P3 and lists the value |
| `cycle` | open leaves depend on each other in a loop | lists the members |
| `waits_on_cycle` | an open leaf is in, or waits on, a cycle | shows the leaf as held out, with no effective priority |
| `skipped_line` | `- [X] text`, `- [] text`, `- [ ]`, `- [ ] ` or `- [x]text`: a node-shaped line that is not a node (`- [xx] text`, `-[x] text`, `* [ ] text` and a link item with two or more characters of text are not reported; `- [1](url)` is) | lists the line |
| `no_nodes` | the file has no node lines | shows an empty site |
| `site_unreadable` | `WBS.md` cannot be opened, read or decoded as UTF-8 | shows the site as unreadable, with the reason |

## The page

`gantry/views/worksite.py` turns a parsed site into the page's data
(`page_model`), draws the network, and fills `gantry/templates/worksite.html`
(`render_page`). The page's own script, `gantry/static/worksite.js`, draws
the schedule and the details. The page is one self-contained file: it works
when opened from a local file, with no server and no network.

The page shows **open work**: every leaf whose glyph is open and that no
cycle holds out. Finished leaves are left out (a link to one is already
satisfied).

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
is P1 (not its effective priority), it is in progress (`~`), or the schedule
starts it within 10 working days (60 agent-hours at 6 a day) at the default
4 lanes. Every other leaf is a circle whose area is its hours. "Every leaf
as a box" shows them all as boxes; "Rolling wave" returns.

### The model band and table

Where a site records a leaf's model tiers (#10 reads them), the bottom band
of its box names its thinking tier (left) and checking tier (right), each
shaded by a class from a fixed set (`m-haiku`, `m-sonnet`, `m-opus`,
`m-fable`, and `m-other` for any other value). Each lane's label carries a
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

### The interim estimate note

No estimate is read yet, so every duration is the default unit. Until #10
replaces it with per-figure labels, the page carries one note above the
network and schedule while it schedules open leaves and none has an estimate:
`every duration is the default unit; no estimates are read yet`.

### Missing or unreadable

The panel lists each report entry's message, in the report's order, with
two changes:

- the issue-less leaves are one count, at the first of them:
  `{n} leaves have no issue: id and are named by outline position`, or
  `1 leaf has no issue: id and is named by outline position`;
- each leaf held out by a cycle reads `WBS.md line {line}: {key!r} waits on a cycle`.
  It is left out of the network and the schedule, and the rest of the page
  renders.

With nothing to list, the panel says `Nothing is missing or unreadable.`
A site whose `WBS.md` has no nodes shows `This site has no leaves.`, one
with leaves but none open shows `This site has no open leaves.`, and one
whose open leaves all wait on a cycle shows
`Every open leaf waits on a dependency cycle, so nothing can be scheduled; the panel lists them.`;
none of them draws a network or a schedule.

### Foreign entries

A one-site page does not resolve another site's leaves (#11 does). A
leaf's foreign `depends_on` entries, then those of each open row above it,
top-down, are listed in its details as `{raw} (on another site)`, such as
`beta:Issue-3 (on another site)`.

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
  on `focusin`. Listeners are delegated on `document`; there are no inline
  handlers.
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

## On the command line

`gantry build --config FILE` lists the sites and writes nothing. Each site's
line gives its counts as the third field: `open N, finished M` (leaf counts),
then `; ` and `<kind> <count>` for each kind with a count, joined by `, ` in
the table's order. With no entries there is no `;` part:

```text
harbor	<planning directory>	open 8, finished 6
orchard	<planning directory>	open 6, finished 3; no_issue 3, unresolved_ref 1
marsh	<planning directory>	cannot read: WBS.md is not valid UTF-8 at byte 88
```

A site Gantry cannot read prints `cannot read: <reason>` instead; the reasons
are in [config.md](config.md#sites).

With `--site`, `gantry build --config FILE --site NAME` writes that site's page to
`<output_dir>/sites/<NAME>.html` and prints the page's absolute path. It
creates `<output_dir>/sites/` when it is missing, and writes the page to a
temporary file in that folder, then renames it into place, so no partial
page is ever left. Each of these exits 1 with the reason and writes no page
(an existing page is kept as it was):

- `--site NAME: no such site in <devagent config>`;
- `--site NAME: cannot build: <reason>`: the site has a problem (an invalid
  name, no planning directory, no `WBS.md`) or its `WBS.md` cannot be read;
- `--site NAME: render error: <type>: <message>`: building the page's data
  or filling its template failed;
- `--site NAME: cannot write <page>: <reason>`.

An invalid name is printed escaped, as in the listing.
