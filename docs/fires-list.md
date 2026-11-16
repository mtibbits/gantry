# The fires list

A **fire** is something that needs the operator now. The **fires list** is
an installation's small private file of fires that are not leaves yet: a
sudden escalation, a broken service, a call that has to be returned. It is
the one thing Gantry v1 lets the operator write. The installation config
names it with `fires_list` ([config reference](config.md#keys)).

**The fires list is private data that never belongs in a public
repository.** The sample in this repository (`samples/fires.md`) is
fictional.

Today `gantry fires` checks the file ([Checking the file](#checking-the-file)).
No build reads it yet: the fire collector (#32) will read it in every
build, and the daily page (#34) will show it. Gantry never writes to the
file.

## Entries

<!-- Editor note: tests/test_fires_list.py::test_doc_examples_read_as_documented
reads the first fenced block under this heading and the one under "Notes under
an entry" as fires lists; edit them and that test together.
test_doc_remaining_prose_reads_as_prose reads the block under "Entry-like
lines" the same way: every line but its one entry must stay prose, and no
line of it may start with `## `. test_doc_must_report_probe_is_reported and
test_doc_cites_152 pin that section's must-report probe and its #152 note;
ALSO_REPORTED in that test file holds the "also reported" shapes.
tests/test_fires_list.py::test_doc_names_every_kind_status_and_key pins this
doc's `## ` headings and their order, every report kind, status and key, and
a few phrases (it lists them); reword those with that test. -->

The file is UTF-8 Markdown, so it reads well in a phone editor and can hold
headings and notes. Each fire is one **entry**, a top-level list item:

```
- [ ] 2026-10-24 @harbor the release build has been red since morning
- [ ] 2026-10-24 09:40 an escalation that is not a leaf yet
- [x] 2026-10-20 @quarry a disk alert on the backup host
- [>] 2026-10-21 @harbor a flaky deploy, now a leaf
```

(`harbor` and `quarry` stand for any site names.)

A **top-level item** is a line that starts with zero to three spaces, a
list marker `-`, `*` or `+`, and at least one space or tab. Its text after
the marker is read left to right, in **tokens**: runs of characters with no
space or tab.

1. **The state box**: `[`, any one character and `]`. `[ ]` is open (a
   fire), `[x]` or `[X]` resolved, `[>]` promoted to a leaf. Any other glyph
   is a box that did not parse, among them `[-]` and `[/]`, which some phone
   task apps write, and `[?]`. The box must be followed by a space or tab:
   in `- [ ]2026-10-24 …` the box is read, the missing space is a failure,
   and reading goes on from the character after the box. `[` and `]`
   around no character or several (`[]`, `[xx]`), followed by a space, a
   tab or the end of the line, are also a box that did not parse: the
   token is consumed and reading goes on, so `- [] 2026-10-24 …` keeps its
   date.
2. **The date**: a token that begins with four digits and a hyphen is a
   date attempt. It is valid when it is exactly `YYYY-MM-DD` and a real
   calendar date. It is the installation's local date the fire began, and
   the entry's age is measured from it.
3. **The time**, optional: a token of one or two digits, `:` and two
   digits. It is valid from `0:00` to `23:59`, so `9:40` is 09:40 and
   `25:00` is a failure. Any other token, such as `24/7` or `9am`, is not a
   time and begins the site or the reason.
4. **The site**, optional: a token that starts with `@`. The name is the
   whole token after the `@`, up to the next space or tab, so `@harbor:`
   names `harbor:`. It is matched exactly, case included, against every
   site in devagent's config, built or not. A bare `@` names no site and is
   a failure.
5. **The reason**: the rest of the line, required, kept exactly as written.
   A reason whose first word starts with `@` is read as a site: reword it.

**How a failure is read past.** Each field is tried in turn. A token with a
field's shape (a box, a date attempt, a time attempt, an `@` token) is
consumed even when its value is invalid, and that field is then empty. A
required field that is missing (no box, no date, no reason) consumes
nothing, and the next field is tried on the same token. So `[?] 2026-10-24 …`
keeps its date, `- [ ] 2026-02-30 x` has no date but keeps the reason `x`,
an entry with no reason keeps its date, time and site, and
`- 2026-10-24 call back` (no box) keeps its date and the reason `call back`.

An entry with any failure is **unreadable**. It is kept, with its line as
written and the fields that were read, it stays a fire unless its box says
it ended, and it is reported (`fires_bad_entry`, one per line, naming every
failure). Nothing typed into the list vanishes without a trace.

## Lines

Only a line feed ends a line, and a carriage return directly before it is
dropped, so CRLF line ends read as LF ones. A leading byte-order mark is
skipped; one later in the file is a mark like any other (see
[Entry-like lines](#entry-like-lines)). Every other character stays inside its line, including a lone
carriage return, U+2028, U+2029, U+0085, vertical tab and form feed, so a
reason holding one comes back whole, on one line number. A carriage return
at the very end of a file with no final line feed is dropped too.

## Notes under an entry

A bullet indented two or three spaces whose text starts with neither a box,
a box-like pair (`[]`, `[xx]`, `[WIP]`, followed by a space, a tab or the
end of the line) nor a date attempt is a **note** when it comes after an entry or another
note, with only blank lines between. It is not read. This is how a phone
editor nests a line under an entry:

```
- [ ] 2026-10-24 @harbor the release build is red
  - reran it, same failure
```

Any other line ends a run of notes: a heading, a paragraph, a thematic
break, an item that holds nothing, a fence, an entry-like line that
holds nothing, or a bullet indented four or more spaces that starts with
no box or date (a sub-note: the next two-space note is then an entry).
The same bullet with no entry or note above it (first in the file, or
after a heading or a paragraph) is an unreadable entry. A two- or
three-space bullet that starts with a box, a box-like pair or a date is
always an entry, so `  - [] 2026-10-25 …` and `  - [WIP] still digging`
under an entry are unreadable entries, not notes, and
a bullet at column 0 or indented one space is always an entry, so
`- call the supplier back` with no box is an unreadable fire, not a note.

## Items that hold nothing

A top-level item whose text is empty, only spaces, or only a box and spaces
(`- `, `- [ ] `) holds nothing. Phone editors leave one when they continue
a list on Enter. It is skipped with no report entry, and `gantry fires`
counts it (`skipped_empty`). An entry-like line (below) that holds nothing
after its box is skipped and counted alike, whatever marks come before the
box, so `• [ ]`, `> - [ ]` and `– [x]` are skipped and counted like
`1. [ ]`. Only the exact one-character box counts here: a lone `[]` or
`1. []` holds a box-like pair, so it is reported, never skipped.

A **thematic break**, a line of three or more of the same `-`, `*` or `_`,
with spaces or tabs between allowed and up to three spaces before
(`- - -`, `* * *`), is not an entry.

## Entry-like lines

Any other unfenced line is an **entry-like line** when, after any leading
spaces or tabs and an optional number marker (digits followed by `.` or
`)`), at most eight **marks** follow, with spaces or tabs between them
allowed, and then a box, a box-like pair or a date attempt. A mark is any
character that is not a letter or digit in the Unicode sense, a space, a
tab or `[`: a list marker `-`, `*` or `+`, a `•` or `–` bullet (which some
notes apps and autocorrect write), a `>` quote, `_`, a no-break space or a
stray byte-order mark. So these are all entry-like lines, with or without a
space after a marker: `1. [ ] …`, `-[ ] …`, a tab- or four-space-indented
`- [ ] …`, a bare `2026-10-24 …`, `[] 2026-10-24 …`, `1. [xx] …`,
`• [ ] …`, `– [ ] …`, `-` and a no-break space before `[ ] …`, and
`> - [ ] …`. Its fields are read by the same rules, and it is kept as an
unreadable entry and reported (`fires_bad_entry`, ``not a top-level `- `
item``). An ended one, such as `1. [x] 2026-10-20 …`, is reported but is
not a fire.

The rule has covered any short run of marks and box-like pairs since #152,
so a mistyped entry is reported rather than lost: `-[] 2026-10-24 @harbor x`,
for one, is an unreadable entry. These **stay prose**, neither read nor
reported:

- a heading: one to six `#`, then a space, a tab or the end of the line,
  whatever follows;
- a line where a letter or digit, other than a leading number marker, comes
  before any box or date (`Called back 2026-10-24`, `1. call back`);
- more than eight marks before the box or date;
- a number marker after a mark (`> 1. [ ] …`);
- a line starting with `[` whose pair is followed by something other than
  a space, a tab or the end of the line, whatever the brackets hold: a
  link, an image or a footnote, even one whose text starts with a date. A
  one-character label such as `[a](…)` or `[1]: …` is the exception: it is
  read as a box and reported, as before #152;
- a two- or three-space bullet under an entry whose text starts with a mark
  before its box (`  - > [ ] …` is a note).

These are **also reported**, since failing closed beats losing a fire: a
date-led table row (`| 2026-10-24 | … |`), an emphasis-led date
(`**2026-10-24** …`), a one-line HTML comment around an entry, a
struck-through entry (`~~- [ ] …~~`), a GitHub alert (`> [!NOTE]`), a
parenthesised date (`(2026-10-24) …`), a bracket-led word
(`[WIP] still digging`), and a code span or a quotation mark before a box
or a date (`` `2026-10-24` was the day ``, `"2026-10-24",`). A wrapped date such as `2026-10-24**` or
`2026-10-24)` does not parse, so such a line reads undated until it is
fixed. A new entry-like line takes a following two- or three-space bullet
as its note, as any entry does.

The shapes that stay prose, each read as prose, under one real entry:

```
# [ ] 2026-10-24 @harbor a heading stays prose
Called back 2026-10-24 about the crane
1. call back the surveyor
> > > > > > > > > [ ] 2026-10-24 @harbor nine marks
> 1. [ ] 2026-10-24 @harbor a number marker after a mark
[2026-10-24 postmortem](https://example.com) notes
![2026-10-24 crane](crane.png)
[^2026-10-24]: see the log
[runbook](https://example.com) says restart
- [ ] 2026-10-24 @harbor the release build is red
  - > [ ] 2026-10-25 @harbor a mark first, so a note
```

Write each entry as `- ` at the start of its line, and run `gantry fires`
after an edit to see that the count of fires is what you typed.

In short: a bullet at column 0 or 1 is always an entry; at two or three
spaces it is an entry when it starts with a box, a box-like pair or a date
or has no entry above it, and otherwise a note; any other line that is not
a heading and reaches a box, a box-like pair or a date through at most
eight marks is reported, and is prose otherwise; items that hold nothing
and thematic breaks are skipped.

## Fences

A fenced code block is the one way to keep an entry-shaped example in the
file: lines inside a closed fence are not read. A fence opens on three or
more backticks or tildes at any indent and closes on a run of the same
character at least as long with no info string; a backtick run whose info
string holds a backtick is not a fence. `gantry fires` counts the
entry-shaped lines a closed fence hides (`skipped_in_fences`: an item that
starts with a box, a box-like pair or a date, or a line the
[entry-like rule](#entry-like-lines) above would report, with or without a
list marker), so a stray fence that
swallows real entries shows. An HTML comment hides nothing.

An opener with no closer is not a fence: every line after it is still
read, and the opener is reported (`fires_unclosed_fence`).

A known limit: finding fences takes time that grows with the square of the
number of unclosed openers of decreasing length, so a file of thousands of
such lines is slow to read. A real fires list is far smaller.

## How an entry ends

Deleting its line, ticking it resolved (`[x]`) or marking it promoted to a
leaf (`[>]`) ends an entry. An ended entry is not a fire. Nothing else ends
one: Gantry never removes, rewrites or expires an entry. An ended entry is
checked only against the grammar above (a malformed ended line is still
reported); its site and date are not checked further, so an old resolved
line never stands in the way.

## Still a fire?

A fire (an entry not ended, readable or not) whose date is known and whose
age reaches `fires_check_days` whole days is flagged **still a fire?**.
`fires_check_days` is an installation config key, an integer of at least 1,
default 3 ([config reference](config.md#keys)). The flag questions an old
entry; it never drops it. The entry stays a fire until the operator ends
it. An unreadable fire with a date can be flagged; one with no date cannot.

The key lives in the installation config, not in the portfolio file's
`[thresholds]` table: the fires list is a source that fails closed, and its
flag must not depend on a portfolio file being configured or readable.

An entry's age is how long the fire has existed, measured from its typed
date. Age is not freshness: how soon a saved entry reaches a served page
counts from the save, and belongs to the refresh job (#35) and the daily
page (#34).

## The reference time

Ages and the flag are measured against a **reference time**, which the
reader takes from its caller and never reads from the clock. `gantry
fires` uses the as-of date at 00:00 when the installation config sets
`as_of` (the sample config always does, so sample results never change
from day to day), and otherwise the time the command starts. An entry with
no time begins at 00:00 on its date, and its age is meaningful to the day.

A fire dated after the reference has age 0 and is reported
(`fires_future_date`). So with `as_of` set, an entry with a time on the
as-of day itself, such as `- [ ] 2026-01-20 09:00 …` against
`as_of = 2026-01-20`, is in the future; one with only the date of that same
day is not.

An `@name` on a fire that names no site keeps the entry a fire, with its
site as written, and is reported (`fires_unknown_site`).

## Statuses

Reading the file gives one of four statuses:

| Status | Meaning |
|---|---|
| `read` | read, with no report entry; an empty file, or one of only well-formed ended entries, is `read` |
| `read_with_problems` | read, with at least one report entry |
| `not_read` | the configured file does not exist, is not a file, cannot be opened or is not valid UTF-8; no entry is read, and one `fires_list_unreadable` entry names the `fires_list` key, never a path |
| `not_configured` | `fires_list` is not set |

The report kinds are `fires_list_unreadable`, `fires_bad_entry`,
`fires_unclosed_fence`, `fires_unknown_site` and `fires_future_date`.

Only `read` will let the daily screen say "nothing on fire": any of the
other three means the list may hold a fire Gantry could not see. The fire
collector (#32) and the daily page (#34) will apply that rule; no page shows
the fires list yet.

## Checking the file

```
gantry fires --config <file>
python -m gantry fires --config <file>
```

reads the fires list the config names and prints, against the reference
time above:

- one line per fire, in file order, unreadable ones included:
  `line N: SITE, WHEN, AGE[, still a fire?][, unreadable]`, where `SITE` is
  `@name` (with ` (not a site)` when it names no site) or `no site`, `WHEN`
  is the date with ` HH:MM` when the entry has a time, or `no date`, and
  `AGE` is whole days (`3d`), days and hours for a timed entry (`0d 7h`), or
  `age unknown`. The reason is never printed;
- `fires: F, ended: E`;
- `skipped_empty: A, skipped_in_fences: B`, the items that held nothing and
  the entry-shaped lines closed fences hid. Neither is a report entry or
  changes the status;
- one line per report entry, as `line 14: fires_bad_entry: no date`;
- `status: STATUS`.

When the status is `not_read` or `not_configured` nothing was read, so the
fire and count lines are left out.

It exits 0 when the status is `read`; 1 when it is `read_with_problems`,
`not_read` or `not_configured` (the last also says so on stderr), or when
the installation config or the devagent config it names is invalid; and 2
on a usage error such as a missing `--config`. It only reads, so the
operator or a script can run it at any time. `gantry build` does not read
the file yet (#32).

## Editing from a phone

Gantry adds no server and no write endpoint for the fires list. The one
supported edit path is git: keep the fires list in a private git repository
that the refresh job already updates (the planning repository, for
example, or a small repository of its own), and edit it from the phone
with the git host's private web editor or a git client app. A save is then
a commit to the copy the refresh job pulls from, so once the refresh job
(#35) exists an entry will show by the end of the first refresh that starts
after the save, and a rejected save or a pull that cannot fast-forward will
fail loudly.

## Why not a file-sync tool

A file-sync tool is not a supported path. Its delay breaks the bound on how
soon a saved entry shows, and it settles a conflicting edit by writing a
side copy that Gantry never reads, so a new entry could vanish while the
list still reads `read`.

## Where the file may live

- Never in a public repository, and never in Gantry's own repository.
- An installation used for paid work keeps its fires list and its edit
  path within that organization's network and device policy, not on a
  personal phone or a personal git host.
- The build machine only reads the file. If the file shares a repository
  with records the build machine also commits to, a pull that cannot
  fast-forward fails the refresh job's update, which the daily screen will
  show as a failure rather than hide.
