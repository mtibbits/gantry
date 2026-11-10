# The portfolio file

An installation's **portfolio file** holds the operator's own priorities:
their business requirements, how they mean to share their attention across
the sites, their weekly capacity, a milestone, and the thresholds the views
use. The installation config names it with `portfolio_file`
([config reference](config.md)).

**The portfolio file is private data that never belongs in a public repo.**
Keep it beside the installation config, outside this repository. The sample
in this repository (`samples/portfolio.md`) is fictional.

Today `gantry portfolio` checks the file; the build does not read it yet.
The first view that needs it in the build adds that read (#23 or #61), and
every later view reuses it.

## Checking the file

```
gantry portfolio --config <file>
python -m gantry portfolio --config <file>
```

`gantry portfolio` reads the installation config, the site list and the
portfolio file, runs every check below except the milestone-leaf check, and
prints one line per report entry, grouped by section, then a count:

```
business requirements
  line 14: duplicate_id: life:BR-2 is also defined on line 12; the first definition is kept
settings
  line 31: bad_value: capacity.agent_lanes: must be an integer, 0 or more, not a boolean; it is not set
note: the milestone leaf is not checked here (this command reads no WBS.md); it is checked once the build reads the portfolio file
2 entries
```

A line is `line N: ` (when the entry has a line), the kind (an advisory
kind is marked ` (advisory)`), the key path (when the entry has one) and the
message. An entry about the whole file is grouped under `the portfolio
file`. The `note:` line is printed only when a milestone is set: the
command reads no `WBS.md`, so it cannot look for the milestone's leaf. The
note stays true until the build reads the portfolio file (#23 or #61), which
rewords it.

It exits 0 when there are no entries and 1 when there is any entry, an
advisory one included. It also exits 1 on a config error and when the config
sets no `portfolio_file`, saying so on stderr; a usage error, such as no
`--config`, exits 2. Nothing it prints about the portfolio file holds that
file's path: it is always "the portfolio file". A config error on stderr names
the config file, as `gantry build` does. It publishes nothing and writes no
file, so the operator can check their real file without a build.

## The file

The portfolio file is UTF-8 Markdown; a leading byte-order mark is accepted,
and so are CRLF line ends. Gantry reads two parts of it and treats the rest
as prose, so the operator can keep notes in it:

- the **business-requirements section**: the text under the heading
  `## Business requirements`, up to the next level-1 or level-2 heading or
  the end of the file. It is read by the requirements reader exactly as
  [the requirements file format](requirements-format.md) states, in the
  installation namespace (`br_namespace`, `life` by default) and with only
  the `BR` layer. Its rules (IDs, duplicates, links) are that format's, and
  its line numbers are the portfolio file's own;
- the **settings block**: the first fenced code block whose info string is
  `toml` under the heading `## Settings`. Every setting below lives there.

A heading is an ATX heading at the start of a line: one to six `#`, then a
space or tab, then its text, with no closing `#`s. Trailing spaces and tabs
are ignored, and the text is matched exactly, so the two headings are level 2
and spelled as above. A line inside a fence is never a heading, so a TOML comment such as
`## Note` inside the settings block, or a fenced example holding
`## Example` in the business-requirements section, does not count. Fences
pair by the requirements format's rule: a fence opened with three or more
backticks or tildes closes at a line of at least as many of the same
character with nothing after it, and an opener with no closer is plain text.

A second `## Business requirements` or `## Settings` heading, or a second
`toml` block under `## Settings`, is reported (`duplicate_section`) and
ignored; the first is read. A file with no business-requirements section is
read with one `no_br_section` entry. A file with no settings block, such as
the business-requirements-only file the operator starts with, is read with
one `no_settings` entry, and every setting is not set.

## Key paths

A report entry names the key it is about by its **key path**, dotted from the
settings root:

- a member of an array of tables is `name[n]`, counted from 1 in file order;
- a segment that is not a bare TOML key (letters, digits, `_` and `-`) is
  written as a JSON string with ASCII escapes, so a key path is always one
  line.

For example: `allocation[2].shares.beta`, `capacity.attention_mapping`,
`capacity.reserved[1].start`, `site.harbor.serves[2]` and
`shares."my.site"`.

An entry gives the file line of the key when the key is written: a `[table]`
or `[[table]]` header, or a bare or dotted `key =` line. A key that is
missing, or is written in another form (a quoted key, or a member of an
inline table), gives the line of the nearest written table or key that holds
it, or no line.

## Settings keys

<!-- Editor note: tests/test_portfolio_file.py checks this table against
gantry/portfolio_file.py's SETTINGS_KEYS in both directions: every Key, its
Default and its Read by. Change both together. -->

| Key | Type and rule | Default | Read by |
|---|---|---|---|
| `portfolio_version` | integer, `1` | required | #22 |
| `allocation` | array of tables (`[[allocation]]`) | empty | #23, Epic #5 |
| `allocation[].from` | TOML local date; a string or a date-time is refused | required | #23, Epic #5 |
| `allocation[].shares` | table of project name to a finite number, 0 or more; the values sum to 100 (within 0.01) | required | #23, Epic #5 |
| `site.<project>.serves` | array of business requirement IDs, full (`life:BR-3`) or bare (`BR-3`, meaning the installation namespace) | empty | #23 |
| `capacity.attention_hours` | table of weekday (`mon` to `sun`) to hours, each a finite number from 0 to 24; a missing day is 0 | not set | Epic #5 |
| `capacity.agent_lanes` | integer, 0 or more: agent lanes per day | not set | Epic #5 |
| `capacity.reserved` | array of tables (`[[capacity.reserved]]`): blocks of attention hours that are spoken for | empty | Epic #5 |
| `capacity.reserved[].day` | weekday, `mon` to `sun` | required | Epic #5 |
| `capacity.reserved[].start` | time `"HH:MM"`, 00:00 to 23:59 | required | Epic #5 |
| `capacity.reserved[].hours` | a finite number above 0 | required | Epic #5 |
| `capacity.reserved[].label` | text | not set | Epic #5 |
| `capacity.block_min_hours` | a finite number above 0 | `1` | Epic #5 |
| `capacity.block_default_hours` | a finite number, at least the `block_min_hours` in use | `2`, or `block_min_hours` when that is larger | Epic #5 |
| `capacity.block_start_cost_hours` | a finite number, 0 or more: operator hours charged per block | `0.25` | Epic #5 |
| `capacity.attention_mapping` | array of exactly five numbers, each 0 or more, not decreasing: operator hours per agent hour for attention need 0 to 4. Only its shape is checked here; [week-plan.md](week-plan.md#attention-need) gives its meaning, Gantry's default and the limits that send it back to that default | not set | #36 |
| `milestone.project` | non-empty text: a project name | not set | #24 |
| `milestone.issue` | non-empty text: the milestone leaf's `issue:` value | not set | #24 |
| `milestone.label` | text | `"autonomy milestone"` | #24 |
| `thresholds.wip_max` | integer, 0 or more | `3` | #29 |
| `thresholds.unknown_deps_max` | integer, 0 or more | `0` | #29 |
| `thresholds.unplanned_max` | integer, 0 or more | `0` | #29 |
| `thresholds.share_gap_points` | a finite number above 0: percentage points | `10` | #29, #25 |
| `thresholds.neglect_weeks` | integer above 0 | `4` | #29 |
| `thresholds.effort_window_weeks` | integer above 0 | `4` | #25 |

Every key is optional except `portfolio_version` and the required keys of an
array member that is written. An absent optional key takes its default, or
is "not set" where it has none. A wrong value is reported (`bad_value`,
naming the key and the rule) and its default, or "not set", is used instead.
A number is an integer or a float and never a boolean, and every number
except those in `attention_mapping` must be finite: `inf` and `nan` are
refused (week-plan.md says what Gantry does with one that is not
finite). A date-time is never accepted where a date is wanted.

- A `[milestone]` table names one leaf by project and `issue:` value; it
  needs both. An `issue` that does not match `[A-Za-z0-9][A-Za-z0-9_.-]*`,
  or that is an outline position (two or more dot-separated integers, such
  as `3.2` or `3.2.1`), is `bad_milestone`: outline positions move at
  `wbs update`, so name the leaf by its issue id. A bare number such as `3`
  is an issue id.
- A `[[capacity.reserved]]` block with a missing or wrong field is reported
  once per field and set aside.
- Every project name is a devagent `[project.<name>]` table name, matched
  exactly and case-sensitively. The portfolio file is keyed by project name
  but is not a site registry: the sites come only from devagent's config.
- The values' meaning belongs to the views that read them; this file owns
  their names, types, defaults and checks. A later issue that needs a new
  key adds it to the registry and to this table in its own change.

### `[site.<project>]` here and `[site.<name>]` in the installation config

Both files key a site table by the devagent project name, and they hold
different keys:

- the portfolio file's `[site.<project>]` holds the operator's priorities for
  the site: `serves`;
- the installation config's `[site.<name>]` holds where the site's files
  are: `requirements`, `estimates` and `history` ([config reference](config.md)).

A key in the wrong file is an unknown key there.

## The version rule

`portfolio_version` is an integer, currently `1`. It is modelled on the
installation config's version rule ([config reference](config.md#the-version-rule)),
with one difference: an unknown key here is reported (`unknown_key`) and
ignored, where the installation config refuses the whole file.

- **Compatible (no version bump):** adding an optional key, and changing a
  default. A changed default is recorded under "Default changes" below.
- **Incompatible (bumps `portfolio_version`):** removing a key, or changing a
  key's type, the meaning of its value, or its rule.

A missing or unknown `portfolio_version` is reported (`bad_version`) and the
settings block is not read, so every setting is not set. The
business-requirements section is still read: its format is the requirements
format's, and carries no version here.

## Default changes

None yet.

## The allocation on a day

Allocations are dated so that a planned shift can be written down ahead of
time. Invalid allocations are set aside first. Of the valid ones, the
allocation that applies on a day is the latest whose `from` is on or before
that day; with none, no intended share is set. Two valid allocations with the
same `from` are reported (`duplicate_allocation_date`) and the first in file
order is used. A view asks for the allocation on the build's as-of date
(`as_of`), never on the build machine's clock.

## The report

Reading never stops a build and never raises on the file's content. Every
problem is one entry naming its kind, its section (`business requirements`,
`settings`, or none for the whole file), the file line where it is known, and
the key path where it applies. No entry holds a path.

| Kind | Section | Meaning |
|---|---|---|
| `portfolio_unreadable` | none | the configured path does not exist, is not a file, cannot be read or is not valid UTF-8; nothing else is read |
| `no_br_section` | business requirements | no `## Business requirements` heading |
| `no_settings` | settings | no `toml` block under a `## Settings` heading; every setting is not set |
| `duplicate_section` | either | a second heading, or a second `toml` block; the first is read |
| `settings_invalid` | settings | the block is not valid TOML, at the file line of the error; every setting is not set |
| `bad_version` | settings | `portfolio_version` is missing or not `1`; every setting is not set |
| `unknown_key` | settings | a key the registry does not have; it is ignored, and nothing under it is reported |
| `undated_allocation` | settings | an allocation's `from` is missing or not a local date; the allocation is not used |
| `bad_allocation` | settings | an allocation's `shares` is missing or not a table, a share is not a finite number 0 or more, or the shares do not sum to 100; the allocation is not used |
| `bad_value` | settings | any other key with the wrong type or out of range; its default, or "not set", is used |
| `duplicate_allocation_date` | settings | a second valid allocation with the same `from`; the first is used |
| `capacity_missing` | settings | advisory: no `attention_hours` or no `agent_lanes`, so the week planner has no capacity to plan into |
| `reserved_exceeds_capacity` | settings | a weekday's reserved hours are more than its attention hours |
| `undefined_priority` | settings | a `serves` ID names no business requirement in this file |
| `misplaced_priority` | settings | a `serves` ID is in another namespace, or is not a `BR` |
| `bad_milestone` | settings | the milestone's `issue` is not an issue id, or is an outline position |
| `unknown_project` | settings | a share, a `[site.<project>]` table or the milestone names a project that is not a site; a share still counts toward its allocation's 100 |
| `milestone_no_leaf` | settings | no leaf on the milestone's site has that `issue:`, or the site could not be read |
| `malformed_item`, `malformed_meta`, `malformed_link`, `misplaced`, `duplicate_id`, `undefined_link`, `refines_cycle` | business requirements | the requirements reader's own kinds, carried with their line and message ([the format](requirements-format.md)) |

When the settings are not read (no block, invalid TOML or a bad version), no
entry comes from them. `bad_milestone` comes from reading the file alone, so
`gantry portfolio` shows it. `unknown_project` and `milestone_no_leaf` need
the installation, so they come from `check_portfolio`: the site list for the
first, and for the second each site's `issue:` values, which come from its
`WBS.md`.

### For a view that reads the file

A view reads `read_portfolio_file(path, br_namespace=...)` once per build and
shows `report_entries(portfolio, site_names, issues_by_site)`, which merges
`Portfolio.report` with `check_portfolio`'s entries in report order, and
nothing else: `Portfolio.brs.report` holds the same business-requirements
entries again. When `settings_read` is false, every setting is not set, the
thresholds included; a view that needs a threshold then uses its default
from `DEFAULTS`. `intended_shares(portfolio, day)` gives the allocation on a
day.

## An example

<!-- Editor note: tests/test_portfolio_file.py reads every ````markdown portfolio-file
block in this file as a portfolio file; keep each one reading with no entry. -->

````markdown portfolio-file
# Portfolio

My notes on where my attention should go this quarter.

## Business requirements

- BR-1: Keep the household running.
- BR-2: Ship the harbor rebuild. {refines: BR-1}

## Settings

```toml
# The settings block: every key is optional except the version.
portfolio_version = 1

[[allocation]]
from = 2026-01-05
shares = { harbor = 60, orchard = 40 }

[site.harbor]
serves = ["BR-2"]

[capacity]
attention_hours = { mon = 4, tue = 4, wed = 4, thu = 4, fri = 2 }
agent_lanes = 2

[milestone]
project = "harbor"
issue = "Issue-5"
```
````

The two smallest forms each read with entries for what they leave out.
Business requirements only, the file the operator starts with, gives one
`no_settings` entry:

```markdown
## Business requirements

- BR-1: Keep the household running.
```

Settings only, with no `## Business requirements` heading, gives
`no_br_section` and, with no capacity, the two advisory `capacity_missing`
entries:

````markdown
## Settings

```toml
portfolio_version = 1
```
````
