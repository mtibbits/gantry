# The strategic page

The **strategic page** asks whether the operator's attention is going to the
sites their priorities say it should. It shows one panel per configured
project, grouped under the business requirements each built site serves,
and sets each site's open work and intended share against its backlog
share. The index carries its answer without opening it: one headline, and
each built site's backlog share on its line.

Every full `gantry build` writes it to `<output_dir>/strategic.html`, after
the site pages and the week view and before the index ([worksite-page.md](worksite-page.md#every-site-and-the-index)).
A `--site` build does not. The page is titled `Strategic page`. A line at
the top says `plans and estimates only`: every figure comes from the plans
and their estimates, and actual effort joins later (#25).

The page reads two things, both once per build: each site's open work, from
the same totals its worksite page uses ([worksite-page.md](worksite-page.md)),
and the portfolio file, from the build's one read of it
([portfolio-file.md](portfolio-file.md)). With no `portfolio_file`
configured, the page renders with one note, `no portfolio file configured`,
every panel shows `no intended share set`, and there are no requirement
groups.

## The groups

A site is **built** when the build wrote its worksite page: its line on the
index links its page. Every other configured project is **not built**, with
the reason the index gives it.

The groups come in this order:

1. **One group per business requirement**, in the order the portfolio
   file's business requirements section lists them, headed `{id}: {text}`,
   for example `life:BR-1: Keep the harbor safe to work in, all year round.`
   The heading holds the requirement's text only. A built site appears under
   every requirement its `serves` list names; an ID the portfolio file's
   reader reports as undefined or misplaced places it nowhere. Every
   requirement group is shown, even one no built site serves, which reads
   `no built site serves this requirement`.
2. **`Serves no listed requirement`**: built sites with an intended share in
   the applying allocation (a share of 0 included) that no requirement group
   places.
3. **`No intended share set`**: built sites with neither a share nor a
   requirement group.
4. **`Not built`**: every project that is not built, with its reason as
   `not built: {reason}`. A project here appears nowhere else, even when it
   has a share or a `serves` list: not built takes precedence.

Groups 2 to 4 are shown only when they have members. So each configured
project appears either in one or more requirement groups, or in exactly one
of the other three, never both. Within a group, panels follow devagent's
config order.

Each group heading carries one sum. It joins the parts that apply with
`; `: `intended {pct} as given`, the members' intended shares as the
operator gave them, when an allocation applies and a member has a share;
and `backlog share {pct}`, the built members' backlog shares, when backlog
shares are shown and the group has a built member. With neither it reads
`nothing to sum`. A site that serves several requirements is in several
groups, so when the page has a requirement group it notes:
`Groups overlap when a site serves several requirements, so group sums do not add up to the total.`

## A panel

A built site's panel links its page and shows:

- **Open work**: `{leaves}, {hours} h{inc}`. `{leaves}` is the index's open
  leaf count (`2 open leaves`, `1 open leaf`), and `{hours}` the site's
  total hours, half up to one decimal. `{inc}` is
  `, includes N unestimated leaves` (or `, includes 1 unestimated leaf`)
  whenever N is above 0. Leaves held out by a cycle are open but in no hour
  figure; when there are any the line adds
  `; {n} held out by a cycle, not in hours`.
- **Site-only units**: one line per unit a site sizes leaves in that is not
  time, such as points:
  `also {quantity} {unit} in this site's own unit, left out of totals across sites; {count}, {parts}`.
  `{count}` is `{n} leaves` or `1 leaf`. `{parts}` says how those leaves are
  already in the hours: `at the default unit (counted as unestimated)` for
  the ones with no estimates-file hours, `at estimates-file hours` for the
  rest. A part that is 0 is left out; the first part kept reads
  `N of them …`, the next `M …`, so
  `2 leaves, 1 of them at the default unit (counted as unestimated), 1 at estimates-file hours`.
- **Intended share**: `intended {pct} (allocation from {date})`, the share
  the allocation that applies on the as-of date gives the site, as the
  operator gave it, naming that allocation's date. A site with no share in
  it, or any site when no allocation applies, shows
  `no intended share set`.
- **Backlog share**: `backlog share, not effort: {pct}{inc}`. When no built
  site has open work, it shows `no open work` instead, and no backlog share
  is shown anywhere on the page.
- **Gap**: `gap {points} points against intended {comparable} rescaled over built sites`,
  when the site has both a backlog share and a comparable intended share
  ([The gap](#the-backlog-share-and-the-gap)).

A not-built project's panel shows its name and reason, its intended share
(or `no intended share set`), the requirement IDs its `serves` list names as
`names {ids} in serves`, and `no page, so no open work counted`. It has no
backlog share and no gap.

## The backlog share and the gap

A site's **backlog share** is its hours divided by the sum of hours over
every built site, as a percent; each site counts once, however many groups
it appears in. It says where the open work is, not where time went, so it is
labelled `backlog share, not effort` everywhere it appears.

A site's hours are the worksite page's totals: the midpoint of each open
leaf's estimate, over the leaves its schedule plans. A leaf with neither a
time `est:` nor an estimates-file row counts at the default unit
(`default_unit_hours`, 6 unless the config says otherwise) and is
unestimated. A leaf sized only in a site-only unit counts the same way, and
its unit's quantity is shown on its own line, left out of every total
across sites. A leaf held out by a cycle is in no hour figure. So changing
`default_unit_hours` moves the backlog share of every site with unestimated
leaves.

The gap compares like with like. Backlog shares sum to 100 over the built
sites, but the allocation may also give shares to projects that are not
built, or to names that are not projects in devagent's config. So each built
site's **comparable intended share** is its share divided by the sum of the
applying allocation's shares on built sites, times 100. The **gap** is the
absolute difference between the backlog share and the comparable intended
share, in percentage points. When the built sites' shares sum to 0, no site
has a comparable share and no gap is shown.

Shares are shown as whole percents, half up (12.5 shows as 13%); the gap's
points are rounded the same way. The gap and the headline use the unrounded
values.

## Totals and notes

Above the groups:

- the totals line, `Open work across built sites: {hours} h{inc}`, each
  site once, then `Site-only units are left out of totals across sites.`;
- when the applying allocation puts a share on projects that are not built
  or on names not in devagent's config, the off-page line,
  `Intended share outside the gap comparison: {parts}.`, whose parts are
  `{pct} on projects not built` and
  `{pct} on projects not in devagent's config`, each only when above 0,
  joined with ` and `;
- whenever an allocation applies,
  `Gaps compare each intended share rescaled over built sites.`

## The index

The strategic page fills the index's headline and line slots and adds its
link to the view links
([worksite-page.md](worksite-page.md#the-index)), from the same model the
page is written from, made once per build:

- **The headline**:
  `Largest share gap: {site}, intended {intended}, backlog share {backlog}{inc} (backlog share, not effort)`.
  The site is the built site with the largest gap, comparing unrounded
  gaps, a share of 0 included; on a tie the first in config order wins.
  Gaps that differ only by floating-point error are a tie: with two built
  sites that both hold a share, the two gaps are always equal.
  `{intended}` is its intended share as given, and `{inc}` its unestimated
  count as above. With no applying allocation (or no portfolio file) it
  reads `no intended shares set`; when an allocation applies but no built
  site has both shares, it reads
  `Largest share gap: none, no built site has both a backlog share and a comparable intended share (backlog share, not effort)`.
- **Each built site's line** ends, after a ` · ` separator, with
  `backlog share {pct}`, then ` (includes M unestimated leaves)` when M is
  above 0, or `no open work` when no built site has open work. It is the
  same figure the site's panel shows. A not-built line gets nothing.
- **The view links** gain `Strategic page`, to `strategic.html`, after
  `Week view` when the build wrote the week view.

When `strategic.html` was not written, the index has no headline, no share
on any line and no `Strategic page` link, so it never links a page that is
not there.

## Figures and their sources

Every figure is an element carrying `data-figure`, its kind, and
`data-source`, the record it comes from. On the strategic page a visible
source follows each figure. The kinds:

| `data-figure` | Where | `data-source` |
|---|---|---|
| `hours` | a built site's open work | `WBS.md and {name}`, naming the estimates file read, or `WBS.md, no estimates file read` when none was found or it could not be read |
| `intended` | a panel's intended share | `the portfolio file, allocation from {date}` |
| `backlog` | a built site's backlog share | `computed from open work` |
| `gap` | a built site's gap | `computed from open work` |
| `total` | the totals line | `each built site's WBS.md and estimates file` |
| `group-sum` | each group heading | its parts' sources joined with `; `, or `no intended or backlog share in this group` |
| `headline` | the index's headline | `computed from open work and the portfolio file, allocation from {date}`; with no applying allocation, `the portfolio file`, or `no portfolio file configured` |
| `index-share` | each built site's line on the index | `computed from open work` |

A figure element exists only where a value is shown, except that the
headline and every index share are always figures, including
`no intended shares set`, the no-gap headline and `no open work`.
`no intended share set`, the page's `no open work` and
`no page, so no open work counted` are plain text. A not-built panel with a
share has an `intended` figure and no `gap`.

`data-unestimated` (`data-unestimated="N"`, N the unestimated leaves it includes) is
on every `hours`, `backlog`, `total`, `headline`
and `index-share` figure, including when N is 0, and on `group-sum` (the
summed N of its built members); it is never on `intended` or `gap`. On the
index, the headline and index shares carry `data-source` with no visible
source. Later views use the same attributes for the figures they add.

The page has no script. Its one `script` element is the embedded data
block (`gantry-data`): the stamp, the as-of date, whether a portfolio file
is configured, the applying allocation as given, the projects, the groups,
one entry per panel with its unrounded figures, the totals and the report.

## The report panel

A panel headed `missing or unreadable` lists every portfolio entry the
build found, in report order, as `{section}: {entry}`. The section is
`the portfolio file`, `business requirements` or `settings`, and the entry
is the line `gantry portfolio` prints for it
([portfolio-file.md](portfolio-file.md#checking-the-file)), the milestone
leaf's check included: a full build reads every site's `WBS.md`, so it can
look for the leaf. With no entry it reads `nothing missing or unreadable`.
The file is always named "the portfolio file", never by its path.

## When the page cannot be written

The model, the render and the write are one step. If any of them fails, the
build prints `gantry: error: cannot write strategic.html: <reason>` on
stderr (a fault making or rendering the page reads
`render error: {type}: {message}`), writes the index without the page's
headline, shares or link, and exits 1
([worksite-page.md](worksite-page.md#every-site-and-the-index)). An older
`strategic.html` from an earlier run stays in the output folder, unlinked,
with its old stamp, until the next build that writes the page replaces it.

## Checking placement on an installation

From a checkout, after a build, this prints how many configured projects
the page has and how many are placed in exactly the way the groups above
allow; it prints counts only, no project name, group, share or text:

```
python3 -c "import sys; sys.path.insert(0, 'tests'); from helpers_strategic import placement_line; print(placement_line(sys.argv[1]))" <output_dir>/strategic.html
```

Run it from the repository's top folder (on Windows, `python` in place of
`python3`). A good result reads `N configured projects, N placed`, with N
your devagent config's project count.

## Serving

The page is built from the operator's private data. As with every page
built from real data, real pages are meant to be served only on a private
network, for example a tailnet; Gantry writes them to your output folder and
publishes nothing itself ([README](../README.md#your-data-stays-private)).
The public demo's strategic page is built from the fictional samples only
([demo.md](demo.md)).
