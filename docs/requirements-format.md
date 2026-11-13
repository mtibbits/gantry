# Requirements file format

A **requirements file** is a project's Markdown file of requirements, each
with a stable ID. A leaf in a project's `WBS.md` names the requirements it
serves in `satisfies:`; this page defines those IDs and the files that define
them (#15). The reader is `gantry/requirements.py`; `gantry/trace.py` reads
`satisfies:` and builds the trace.

## Layers

Requirements come in eight layers, after Wiegers and Beatty's *Software
Requirements*. Each layer has a fixed code. Any layer may be empty or absent.

| Code | Layer | What it states |
|---|---|---|
| `BR` | business requirement | an outcome the operator wants across all projects; only in the portfolio file |
| `BRule` | business rule | a policy or rule the work must follow |
| `UC` | use case | a task a person does with the system |
| `SR` | system requirement | what the system as a whole must do |
| `FR` | functional requirement | a behaviour the software must have |
| `QA` | quality attribute | how well it must do it: speed, reliability, ease of use |
| `EI` | external interface | a connection to another system, file or device |
| `CON` | constraint | a limit on the design or the build |

## IDs

A full ID is `<namespace>:<code>-<n>`, for example `gantry:FR-12`:

- `<namespace>` fully matches `[A-Za-z0-9][A-Za-z0-9_.-]*` (devagent's
  `ID_TOKEN_RE`);
- `<code>` is one of the eight codes above;
- `<n>` is a positive integer with no leading zero and at most 9 digits.

Matching is exact and case-sensitive, for the namespace and for the code:
`harborwest:FR-1` never names a requirement of a site named `harborWest`, and
`fr-12` is not an ID. Inside a requirements file an ID may be written bare
(`FR-12`), meaning the file's own namespace.

IDs are never renumbered or reused. A requirement keeps its number for life,
and a number once used is never given to another requirement, even after the
first is withdrawn (see below).

Every full ID is also a valid `<namespace>:<id>` entry in the shape
mtibbits/devagent#888 checks in `satisfies:` (each half matches
`ID_TOKEN_RE`, with exactly one `:`), so every Gantry ID passes devagent's
check. A change to this grammar is raised on that issue first.

## Namespaces

- A project file's namespace is its site's name: the name of the devagent
  `[project.<name>]` table.
- Business requirements are the operator's priorities across all projects,
  so they live only in the portfolio file, under the **installation
  namespace**: `br_namespace` in the installation config's `[requirements]`
  table, `life` by default (`life:BR-3`). A project file holds every layer
  but `BR`; the portfolio file's business-requirements section holds only
  `BR`.
- The link from a site to the business requirements it serves lives in the
  portfolio file, never in a project file: a project file's link into the
  installation namespace is reported and not read.

## Encoding

A requirements file is read as UTF-8. A leading byte-order mark is accepted
and skipped. A file that is not valid UTF-8 is reported `unreadable`, naming
the byte where decoding failed.

## File shape

A requirements file is an ordinary Markdown document. A **requirement** is
an unordered list item (`-`, `*` or `+`) whose text starts with a plain bare
ID followed directly by a colon:

```text
- FR-12: The log lists the checks of the last thirty nights.
```

Everything else is prose: headings, paragraphs, tables, and list items that
do not start like an ID. Prose is ignored and never reported.

Lines inside fenced code blocks are ignored too, so a file can show
examples. A fence opened by three or more backticks or tildes is closed by
the first later line of the same character, at least as long, with nothing
after it. A backtick opener whose info string holds a backtick is not an
opener. An opener with no closer is ordinary text and hides nothing.
Lines inside an HTML comment are hidden too; see [Comments](#comments).

### Candidates

A list item, unordered or ordered (`1.` or `1)`), is a **candidate** when
its text starts like an ID: optional emphasis or code marks (`*`, `_`, `` ` ``,
`~`), an optional namespace and `:`, then letters, an optional `-`, digits
and optional letters, then optional closing marks and spaces, then `:`. A
candidate that is not a requirement is reported `malformed_item`, with every
reason that applies:

```text
- FR12: no '-' between the code and the number
- Fr-12: 'Fr' is not a layer code
- FR-012: the number is not a positive integer without a leading zero
- FR-1234567890: the number has more than 9 digits
- NFR-4: 'NFR' is not a layer code
- FR-3a: letters after the number
- FR-3 : a space before the colon
- **FR-3:** marks around the ID
- gantry:FR-12: a namespace (a requirement is defined with a bare ID)
1. FR-3: an ordered list item
```

So, outside fences and comments, a typo of the shape letters, an optional
`-`, digits, then a colon is never silently lost. A typo outside that shape, such as `FR_1:` or `FR.1:`, is
prose and is not reported. The rule also catches prose
bullets that begin like an ID, such as `Step-1:`, `Q3:` and `v2:`, and
finding labels such as `R1:`, `N2:` and `B1:`. They are reported malformed:
reword them (`Step 1:`, `In Q3,`) so that they no longer start like an ID.

### Text

A requirement's text runs from its first line through each following line
indented deeper than its list marker. It stops at a blank line (empty, or
spaces only), at a fenced line, or at a line indented no deeper than the
marker; a tab counts as four spaces. A comment line inside the item is
skipped: the text joins across it (see [Comments](#comments)). The lines are joined with single
spaces after their leading whitespace is removed. A requirement with no text
(`- FR-6:`) is read with empty text.

A list item inside that text is a **nested** item. It keeps its marker in
the joined text (`... - sub point`) and never opens an item of its own.

A list item is nested too when it is indented to at least the **content
column** of an **enclosing list item**. The content column is where an
item's text starts: 2 for `- a`, 4 for `-   a`, 3 for `1. a`. An
enclosing list item is an earlier list item with only blank lines, or
lines indented to at least its content column, in between. Fenced and
commented lines count by their indent too. A blank line does not end the
enclosing item, so FR-2 and FR-3 here are both nested, as a Markdown
renderer shows them:

```text
- FR-1: a

  - FR-2: b

  - FR-3: c
```

A nested item that starts like an ID is reported "requirements are not
nested" and is not read. If it is inside the parent's text, its line
still joins that text. That holds under a prose bullet too: write every
requirement as a top-level list item. An item indented less than the
content column (` - FR-2:` under `- FR-1:`) is a sibling. An item under a
paragraph or a heading is not nested. A line indented less than the
content column always ends the enclosing item, because the reader does
not follow lazy continuation lines.

### Comments

A **block comment** is a line, outside a fence, that starts with `<!--`
after at most three spaces, counted from the line start. Fences and
comments are read in one pass from the top: a `<!--` in a fence is code,
and a fence marker inside a comment is hidden with it. As on a rendered
page:

- A block comment hides whole lines, from its first line through the
  first line holding `-->`. A requirement in it is not read, and a
  candidate in it is not reported.
- Inside a requirement's text, a block comment is skipped: the text joins
  across it.
- A block comment with no `-->`, or one inside a list item that ends
  before its `-->`, hides every line after it and is reported
  `malformed_comment`. A renderer shows lines again after a later
  comment's `-->`; the reader does not. A list item whose text starts
  with `<!--` opens such a comment too.
- Any other `<!--` hides nothing. In a requirement's text, outside a code
  span, it stays in the text and is reported `malformed_comment`: put the
  comment on its own line. A list item that starts with a comment and
  then an ID (`- <!-- x --> FR-1: y`) is not read, and is reported
  `malformed_comment`.

```markdown
# Requirements

- FR-1: The log lists each check
  <!-- was: each night -->
  of the last thirty nights.
<!--
- FR-2: A draft, hidden until it is agreed.
-->
- FR-3: The log keeps thirty nights.
```

FR-1 reads "The log lists each check of the last thirty nights.", and
FR-2 is not read.

## Links

A requirement may carry one meta block in devagent's brace form, either at
the end of its first line (where devagent's own `WBS.md` lines put it) or at
the end of its joined text. The block is removed from the text. When the text
holds a nested item, only a block on the first line is read: a block at the
end of the joined text may belong to the nested item, so it is reported and
not read.

```text
- UC-1: A keeper logs the lamp's nightly check. {needs: [FR-1, QA-1]}
- FR-1: A keeper records a check
  with its time and the lamp's state. {refines: SR-1}
```

Its keys:

- `refines:` on any requirement: one target, never itself
  (`{refines: SR-1}`);
- `needs:` on a use case only: a bracketed list of one or more targets
  (`{needs: [FR-1, QA-1]}`). A scalar `needs: FR-1` is read as a one-entry
  list. A use case with no `needs:` key needs nothing and is not reported,
  so a use case may be written before its requirements.

A target is a bare ID, in the file's own namespace, or a full ID
(`ferry:SR-2`). A value or list entry may carry one layer of matching quotes
(`'SR-1'` or `"SR-1"`), which is removed: devagent's writer quotes a value
holding `:`. `refines:` may point in any direction between layers: the
reader stores the trace and does not judge it.

Reported, with the requirement still read:

- `malformed_meta`: a block that cannot be read (an entry with no key, an
  unclosed `[` or quote, or a block with no closing `}` or with a brace
  inside it), any key other than `refines` and `needs`, a key
  repeated in one block (the first is kept), a list given to `refines:`,
  `needs: []`, a second meta block on one item, and a block that starts with
  a word and a colon anywhere else in the text. A second or mid-text block
  stays in the text, and its links are not read.
- `malformed_link`: a target that is not a requirement ID; it is dropped.
- `misplaced`: `needs:` on an item that is not a use case, and a link into
  the installation namespace from any other namespace; neither is read. In
  the portfolio file's own section such a link is legal, because there the
  installation namespace is the file's own.

A link to another site's namespace is returned unresolved and not reported
here: the trace resolves it against the catalog and reports `refines:`
cycles that cross files ([the trace report](#the-trace-report)).

## Withdrawn requirements

A withdrawn requirement is deleted from the file. Its ID is still never
reused. That is the author's rule: the reader sees one version of the file,
so it cannot check it. A leaf that still names a deleted ID is reported
`undefined` by the trace ([the trace report](#the-trace-report)). Record
the withdrawal in a prose line, so that the next author does not reuse the
number:

```text
FR-7 was withdrawn on 2026-11-05.
```

## The report

The reader returns the requirements (ID, layer, text, links, source and
line) and a report. Each entry names its source (the file's path, or the
label a caller passes, such as the portfolio file's for its
business-requirements section) and its 1-based line, counted in the whole
file a section came from. No entry stops the read. Entries are sorted by
line, then in this order:

| Kind | When |
|---|---|
| `unreadable` | the path does not exist, is not a file, or is not valid UTF-8; the set is empty |
| `malformed_item` | a candidate that is not a requirement, with its reasons |
| `malformed_meta` | a meta block that cannot be read, or a key or value shape it does not allow |
| `malformed_link` | a link target that is not a requirement ID |
| `malformed_comment` | an HTML comment the reader does not hide: in a requirement's text (read as written) or before an ID (not read); or a block comment with no `-->`, or whose list item ends first, which hides every line after it |
| `misplaced` | an item in a layer the file does not hold (it is dropped), `needs:` on an item that is not a use case, or a link into the installation namespace from another namespace |
| `duplicate_id` | an ID defined twice, naming both lines: the first definition is kept and later ones are dropped |
| `undefined_link` | a link into the file's own namespace that names no requirement in it |
| `refines_cycle` | `refines:` links that loop within the file, reported once at the loop's earliest line; a self-link is the one-step loop |

## In the trace

A site is **in the trace** only when the installation config sets its
`requirements` key ([config.md](config.md)); there is no default path. Only
an in-trace site's open leaves can ever be listed as untraced; a site that
is not in the trace contributes only leaves whose `satisfies:` names an ID
that resolves. The report's fields are below.

Two kinds of site can never be in the trace, because their name cannot be a
project namespace:

- a site whose name equals `br_namespace`;
- a site whose name does not fully match `[A-Za-z0-9][A-Za-z0-9_.-]*`.

Each is still a site, and still listed. If its `requirements` key is set,
the key is not read and `gantry build` prints a warning on stderr naming the
site and the key; with no key set there is nothing to warn about. The
build's exit code does not change.

```text
gantry: warning: <config>: key 'site.life.requirements' is not read: site life has the name of the installation namespace (br_namespace = 'life'), so it cannot be in the trace
```

## The `satisfies:` key

A leaf names the requirements it serves in its `satisfies:` meta key, read
from the parsed leaf with the same rule as every other meta value. One ID may
be written bare or quoted. More than one ID **must** use the bracketed list:
an unbracketed `satisfies: a:X, a:Y` splits at its first comma into
`satisfies: a:X` and a separate key `a: Y`, and the second ID is lost.

```text
- [ ] Log the nightly check {issue: Issue-4, satisfies: lighthouse:FR-1}
- [ ] Show the last thirty nights {issue: Issue-5, satisfies: [lighthouse:FR-2, lighthouse:QA-1]}
```

- Each entry is a full ID, namespace included (`lighthouse:FR-1`, never
  `FR-1`). An entry that is not one, such as `FR-1`, `lighthouse:FR-01` or
  `a:b:FR-1`, is reported `malformed_satisfies` and ignored.
- When a leaf has `satisfies:`, each other key of the leaf that equals a
  known namespace (a site's name, or `br_namespace`) is reported
  `unbracketed_list`, naming the key: a probable unbracketed list.
- An ID repeated on one leaf counts once.
- `satisfies: []` names nothing.

## Served

A requirement is **served** by a leaf that is not skipped when the leaf
names it, or names a requirement that refines it, transitively. A use case
is also served through a requirement it needs, directly or through
refinement: a leaf naming an `FR` that refines an `SR` the use case needs
serves the use case. Serving flows up the links, never down: a leaf naming
a use case does not serve what the use case needs.

- A skipped leaf (`-`) never serves; a done leaf (`x`) does.
- Each serving leaf is marked **direct** when it names the requirement and
  **indirect** otherwise; a leaf that serves a requirement both ways is
  direct.
- Each walk visits a requirement once, so a `refines:` loop across files
  cannot loop the walk; the loop is reported.
- Constraints (`CON`) are never listed as unserved: they bound all work
  rather than being delivered by a leaf. A leaf may still name one.

## The trace report

Once per build the trace reads the **catalog**: every in-trace site's
requirements file, in the site's namespace, and, when `portfolio_file` is
set, the portfolio file's business requirements. Every read passes
`br_namespace`. Each leaf's IDs and every link from one namespace into
another resolve against the catalog. Each site's report has these fields:

| Field | Holds |
|---|---|
| `unreadable` | `unreadable`: the site's configured requirements file could not be read |
| `malformed` | the reader's `malformed_item`, `malformed_meta`, `malformed_link` and `malformed_comment`, plus `malformed_satisfies` and `unbracketed_list` |
| `duplicate` | the reader's `duplicate_id` |
| `misplaced` | the reader's `misplaced` |
| `cycle` | `refines_cycle`: the reader's loops within the file, and `refines:` loops that cross files, each reported once, at the member listed first in the catalog |
| `undefined` | the reader's `undefined_link`, `undefined_cross_link` (a link into another namespace naming no requirement) and `undefined_id` (a leaf entry naming no requirement) |
| `unserved` | the site's own requirements that no leaf serves, constraints aside |
| `untraced` | the site's open leaves that name no ID that resolves |

An undefined ID or link names one of these reasons: the namespace is not a
site of the installation, nor `br_namespace`; the namespace has no
configured requirements file; its file, or the portfolio file, could not be
read; the portfolio file has no `## Business requirements` section; the file
defines no such ID; or the ID is in `br_namespace` and
`portfolio_file` is not set.

`unserved` counts only this site's leaves, so its `unserved_scope` is
`"site"`; "Trace across projects" (#21) widens it. `untraced` lists an open
leaf with no `satisfies:` key, with `satisfies: []`, or with only malformed
or undefined entries; the bad entries are reported as well.

No entry stops the build. A site not in the trace has no `unserved` and no
`untraced` list; its leaves' IDs still resolve, and its `malformed` and
`undefined` entries are in its own report. The portfolio file's own entries,
and the links from a business requirement into a site, belong to no site's
report: they stay with the catalog.

## Example

<!-- Editor note: tests/test_requirements.py reads every ```markdown and ```markdown portfolio block in this file; keep each a clean requirements file. -->

A project file, read in namespace `gantry` with every layer but `BR`. It is
an illustration, not Gantry's own requirements:

```markdown
# Requirements

## Use cases

- UC-1: An operator builds a site's planning page. {needs: [FR-1, QA-1]}

## System requirements

- SR-1: The tool reads each site's work breakdown.

## Functional requirements

- FR-1: The build writes one page per site. {refines: SR-1}
- FR-2: The page lists every open leaf,
  with its estimate when it has one.

## Quality attributes

- QA-1: A site's page builds in under ten seconds.

## Constraints

- CON-1: The build needs no network connection.

FR-3 was withdrawn on 2026-11-05.
```

The business-requirements section of a portfolio file, read in namespace
`life` with only `BR`:

```markdown portfolio
- BR-1: Ship one project release each quarter.
- BR-2: Finish a project's first release before starting another. {refines: BR-1}
```

The empty form: a file with every layer empty is valid, and reads with an
empty report.

```markdown
# Requirements

## Use cases

## Functional requirements
```
