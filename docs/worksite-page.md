# The worksite page

A worksite page shows one site's work: its leaves, the links between them,
their priorities and what Gantry could not use. The page itself comes in #9;
this document fixes what it is built from. Every Gantry view reads the same
parsed data (`gantry/model.py`), produced by `gantry.wbs_stopgap.read_wbs`
until devagent exports its own.

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
on a U+2028, form-feed or U+0085 character.

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
not reported: `- [xx] text`, `-[x] text`, `* [ ] text`, `- [text](url)`.

`x` and `-` mark a finished node; every other glyph is open.

### Fences

Lines inside a fenced code block are never nodes. A fence opens on a line of
three or more backticks or tildes, after any spaces or tabs, and closes on
the first later line of the same character with a run at least as long and
nothing but whitespace after it. A backtick opener whose info text holds a
backtick (an inline span such as ```` ``` a`b ````) opens nothing. An opener
with no closer is plain text, and the lines after it are read as usual.
Pairing is devagent's: each unclosed opener scans to the end of the file, so
a file of many unclosed openers reads in quadratic time. `WBS.md` files are
written by their operators, so Gantry accepts that cost.

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
reported `bad_priority` and counts as P3, as does a missing priority. A row's
priority is checked but given to no leaf. A leaf's effective priority is the
most urgent of its own and that of every open leaf that waits on it,
directly or through other open leaves; it does not pass through a finished
leaf or a foreign entry. Compute it over the leaves a page schedules:
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
| `skipped_line` | `- [X] text`, `- [] text`, `- [ ]`, `- [ ] ` or `- [x]text`: a node-shaped line that is not a node (`- [xx] text`, `-[x] text` and `* [ ] text` are not reported) | lists the line |
| `no_nodes` | the file has no node lines | shows an empty site |
| `site_unreadable` | `WBS.md` cannot be opened, read or decoded as UTF-8 | shows the site as unreadable, with the reason |

## On the command line

Until the pages exist, `gantry build` prints each site's counts as the third
field of its line: `open N, finished M` (leaf counts), then `; ` and
`<kind> <count>` for each kind with a count, joined by `, ` in the table's
order. With no entries there is no `;` part:

```text
harbor	<planning directory>	open 8, finished 6
orchard	<planning directory>	open 6, finished 3; no_issue 3, unresolved_ref 1
marsh	<planning directory>	cannot read: WBS.md is not valid UTF-8 at byte 88
```

A site Gantry cannot read prints `cannot read: <reason>` instead; the reasons
are in [config.md](config.md#sites).
