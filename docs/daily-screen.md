# The daily screen

The daily screen is the operator's phone view: anything on fire first, then
today's tasks to start warming up on. Its page is planned (#34); this
document states what a full build already collects for it.

## Fires

<!-- Editor note: tests/test_fires.py::test_doc_names_every_token pins the
status, kind and fail-closed names, the two count names, `this_machine` and
the stdout line's form in this section. -->

A **fire** is something that needs the operator now. Every full build
(`gantry build` without `--site`) collects the installation's fires once,
across every site, into one ordered report with a read status for every
source it read. A `--site` build collects none. Collecting reads the fires
list and every site's devagent issue folders once, and it never changes a
page, the plan file or the exit status. A source, or one site of a source,
that fails gets a `not_read` status and the others keep their fires; a
fault outside the sources gives a report with no fires and one `not_read`
status from the source `fires`. Either way the build goes on.

### What is a fire

A fire comes from one of three sources, in this order:

- `fires_list`: every entry of the [fires list](fires-list.md) that has not
  ended (its box is not `[x]`, `[X]` or `[>]`), unreadable entries included. A
  fires-list fire carries the entry's reason as written, or, for an
  unreadable entry, the whole line as written. It is flagged "still a
  fire?" exactly as `gantry fires` flags it
  ([Still a fire?](fires-list.md#still-a-fire)).
- `p0_leaf`: every open leaf whose own `priority:` is P0. A leaf that is P0
  only because an open P0 leaf waits on it is not a fire, and neither is a
  parent row (an epic) marked P0: only its open leaves can be. P0 is Gantry's
  rule ([Priority](worksite-page.md#priority)): the value read as
  devagent reads metadata, then exactly `P0`; so `"P0"` is P0 and `p0`,
  `P00` and `[P0]` are not. Gantry keeps its own rule; FR-9 says when that
  changes.
- `stuck`: every devagent STUCK marker in a site's issue folders whose
  outcome is a fire ([STUCK markers](#stuck-markers)).

A leaf is **open** when its state is `pending`, `in_progress`, `stuck` or
`blocked_external`. A `parked` leaf is not open here, although its page
counts it as open work, and a `done` or `skipped` leaf never is. The state
comes from the leaf's glyph; a `[!]` glyph alone never makes a STUCK fire:
only a STUCK marker does.

An open P0 leaf that also holds a STUCK fire is one fire with both kinds,
`p0_leaf` then `stuck`, and both reasons; it takes the marker's time.

### STUCK markers

A STUCK marker is the `STUCK` file devagent writes in an issue folder
(`<planning directory>/<issue>/STUCK`;
[devagent-formats.md](devagent-formats.md)). The folder is joined to the
first leaf in `WBS.md` order whose `issue:` is exactly the folder's name.
Each marker has exactly one outcome, tested in this order:

1. When the site's `WBS.md` was not read, the marker is a fire, failed
   closed as `leaf_unknown`: Gantry cannot tell whether its leaf is open, so
   it does not assume it is finished.
2. Unless an earlier one applies: when the site has no `WBS.md`, no leaf
   names the folder, or that leaf is `done` or `skipped`, the marker is
   counted in `stuck_no_open_leaf`, not a fire. A parent row is not a leaf,
   so a marker in an epic's folder is counted here even while its children
   are open.
3. Unless an earlier one applies: when that leaf is `parked`, or the
   folder's `checklist.md` shows a parked step, the marker is counted in
   `stuck_parked`, not a fire.
4. Unless an earlier one applies, the marker is a fire. When the folder's
   `checklist.md` could not be read it fails closed as `parked_unknown`:
   Gantry cannot tell whether the issue is parked, so it keeps the fire. A
   folder with no `checklist.md` is not parked.

The two counts name the folders per site, so a page can say "2 stuck on
parked issues" without calling them fires. A marker that could not be read
is still a marker: it is a fire, `step and reason unknown`, when it sits on
an open, unparked leaf. A folder that vanished while the build listed it
holds no marker, as a STUCK file deleted mid-read is absent; any other
folder that could not be listed holds an unreadable one.

A STUCK fire's reason reads `step N name: reason`, with `step unknown` or
`reason unknown` for what the marker does not say, after
`leaf unknown: WBS.md was not read into the build` (it could not be read,
or its site failed after the read) or
`parked unknown: checklist.md could not be read` when it failed closed.

Every STUCK fire has `this_machine` set: STUCK markers are read from this
build machine's planning directories, so a page labels them "as seen by
this build machine". A marker on another machine is not seen.

### The order of fires

Fires come in source order: the fires list, then P0 leaves, then STUCK
markers.

- Fires-list fires with no date first, in file order, then dated ones
  newest first, ties in file order.
- P0 fires by site in devagent's config order, then in `WBS.md` order; a
  merged P0 and STUCK fire stays here.
- STUCK fires newest first, ties by site order then folder name (by code
  point); then those whose time is unknown, by site order then folder name.

The order does not depend on the order a folder listing returns.

### The reference time

Ages are measured against one reference time per build, the same rule
`gantry fires` uses ([The reference time](fires-list.md#the-reference-time)):
the config's `as_of` at 00:00 when the config sets it, else the moment the
build started. A time written with no zone (a fires-list date, a STUCK
marker's `Created:` in `stuck.sh`'s local-minutes form) is read in the
reference's zone: UTC when `as_of` is set, else the build machine's UTC
offset at build start. A `Created:` with an offset keeps it. An age is never
below 0.

Without `as_of`, a time with no zone can be read off in two ways: by up to
one hour when it was written under the other daylight-saving offset, and by
the full zone difference when it was written on a machine in another zone
and pulled in. Set `as_of` for builds that must agree.

### Statuses and "nothing on fire"

Every source gives a status: the fires list one for the file, and the P0
and STUCK sources one per site, in config order. A source that could not
be read at all gives one `not_read` status with no site instead, and so
does a fires step that failed outside the sources (the source `fires`);
the build names each such status on stderr, as
`gantry: warning: fires source <name> is not read: <reason>` or
`gantry: warning: fires not collected: <reason>`. After that failure
`BuildResult.fires_list` is `None` and `BuildResult.records` is empty when
it came before they were read.

| Status | Meaning |
|---|---|
| `read` | read in full |
| `read_with_problems` | read, but some of it could not be: a fire may be hidden |
| `absent` | there is nothing to read: a site with no `WBS.md` has no leaves |
| `not_read` | it could not be read: a fire may be hidden |
| `not_configured` | the fires list is not set |

"Nothing on fire" (`confirmed_none` in the report) is said only when there
are no fires and every status is `read` or `absent`; any other status stops
it, and so does a report with no status at all. For each source:

- The fires list's status is the one `gantry fires` prints
  ([Statuses](fires-list.md#statuses)). One shape reads `read` and still
  hides a fire: a two- or three-space bullet under an entry whose box comes
  after a mark is read as a note
  ([Entry-like lines](fires-list.md#entry-like-lines)); mtibbits/gantry#168
  closes it.
- A site's P0 status is `read` when its `WBS.md` was read, `absent` when it
  has none, and `not_read` (with the site's reason) otherwise, a case clash
  and an invalid site name included. It is `read_with_problems` when the
  parser reported a `WBS.md` line it did not read as a leaf (a
  `bad_priority` or `skipped_line` entry), or a `WBS.md` with no node line
  at all (a `no_nodes` entry), since a mistyped P0 may hide there. The
  parser reports only some mistyped lines, so a P0 can still be
  missed with every status `read`: on a line it neither reads as a leaf nor
  reports, or in metadata it does not read. Examples, not a complete list:
  `- [ ] Fix the crane {priority: P0} today` (text after the braces, so no
  metadata is read), `- [ ] Fix the crane {Priority: P0}` (the key is
  case-sensitive), `* [ ] Fix the crane {priority: P0}` and
  `1. [ ] Fix the crane {priority: P0}` (not a `- ` bullet, so not a node).
  The same shapes hide a STUCK fire: when the leaf a marker names is on
  such a line, or its `issue:` is not read, the marker is counted in
  `stuck_no_open_leaf` with every status `read`.
- A site's STUCK status is `not_read` when it has no planning directory, or
  its planning directory or its records could not be read. It is `read_with_problems`
  when a STUCK fire's marker could not be read, failed closed as
  `parked_unknown` or has no readable time, or when the planning directory
  holds a linked issue folder: Gantry never follows a link, so a linked
  folder is never read. It is also `read_with_problems` when a marker is
  counted in `stuck_no_open_leaf` while the site's P0 status is
  `read_with_problems` for its `WBS.md` lines, since the marker's leaf may
  be the line that was not read. The reason gives counts only, never a
  folder name.

### The stdout line

Collecting reads all three records in every issue folder, so a full build
on an installation with hundreds of issue folders takes seconds longer, and
much longer from a cold disk cache; mtibbits/gantry#171 follows up.

A full build prints one line last on stdout:

```
fires: 7; sources not fully read: 1
```

`fires:` is the number of fires and `sources not fully read:` the number of
statuses that are not `read` or `absent`. The line names no fire and no
site. With nothing on fire and every source read it is:

```
fires: 0; sources not fully read: 0
```

Its form is `fires: N; sources not fully read: M`. It prints whenever the
build got as far as collecting, even when nothing could be written; the
exit status does not change. The first example is the sample installation's
build: quarry's open P0 leaf, the fires list's four open entries and
harbor's two STUCK markers, with marsh's unreadable `WBS.md` as the one
source not fully read.

To find which source is behind a non-zero M, read the report's statuses
(`BuildResult.fires.sources`; a reason never names a path), or run
`gantry fires` for the fires list. Its own `fires: N, ended: M` line
counts the fires list alone, so its N can be smaller than the build's. A
script that read the last stdout line of `gantry build` reads this line
now.

### The report

`gantry.fires.collect_fires` returns a `FireReport`: `fires` in the order
above, `sources` (every status), `stuck_parked` and `stuck_no_open_leaf`
(`{site: folder names}`, sites in config order), `all_read`,
`confirmed_none` and `by_site()`. A fire records its `kinds` (`fires_list`,
`p0_leaf`, `stuck`), one reason per kind, its site (or the `@name` as
written when that names no site), its issue, leaf and title, whether it is
`unreadable`, its fail-closed value (`leaf_unknown` or `parked_unknown`),
when it began and its age, and `this_machine`. Text is kept as written:
escaping happens where a page shows it.

### Adding a source

The sources are an ordered registry, `gantry.fires.SOURCES`. A source is a
`FireSource` with a `name` and a `collect(context)` that returns a
`SourceResult` of fires and statuses; the context holds the build's result,
config, fires list, issue records and reference time. A source that
raises, returns something that is not a `SourceResult`, or gives no status
gets one `not_read` status and loses its fires; the others still run. Its
fires follow the STUCK fires. The order and the P0 and STUCK merge key on
the names `fires_list`, `p0_leaf` and `stuck`, so a new source takes a
name of its own. This interface is provisional until a second
source outside `gantry/fires.py` binds to it.
