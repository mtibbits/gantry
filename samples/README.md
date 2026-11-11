# Sample worksites

These are fictional inputs for Gantry's tests and demo. Every name in them
is made up: the sites, the owner `sample_owner`, the issues and their
titles. The words devagent and gantry appear only as the names of the two
public tools (in prose, in the `samples/devagent/` folder name and in
`gantry.sample.toml`, which #7 adds).

## Layout

- `devagent/config.toml`: a fictional devagent config. It has a
  `[defaults]` table and one `[project.<site>]` table per site, each with
  an `issue_source` subtable. Every path in it is relative to the file's
  own folder, so `devdoc_dir = "../harbor"` means `samples/harbor/`, and
  no path leads outside `samples/`.
- `gantry.sample.toml`: the sample installation config (#7); its paths are
  relative to `samples/` and its pages go to the ignored `samples/out/`.
- `<site>/`: one planning folder per site, holding its `WBS.md`.
- `harbor/estimates-2026-01-15-effort-and-model-tiers.md`: harbor's
  estimates file, giving each open harbor leaf an effort bucket, an hour
  range and model tiers. Its `#N` cells name `Issue-N` on harbor; GitHub
  auto-links them to this repo's issues, which is harmless for a fixture.
- `portfolio.md`: the sample installation's portfolio file (#22), named by
  the sample config's `portfolio_file`. Its business requirements are
  `life:BR-1` to `BR-3`; its settings give the four readable sites four
  roles: harbor serves two BRs, orchard serves one, quarry has a share and
  serves none, and lighthouse has neither but holds the milestone leaf
  (`Issue-5`). It has no `[thresholds]` table, so the defaults apply.
- `lighthouse/requirements.md`: lighthouse's requirements file (#15). The
  sample config's `[site.lighthouse]` table names it, so lighthouse is the
  one sample site in the trace; the other sites are not.
- `history/`: the sample `WBS.md` history (#28). `plan.toml` lists, per
  site, the dates of earlier versions, each kept as `history/<site>/<date>.md`;
  `tools/sample_history.py` commits them, then each site's current
  `WBS.md` at the plan's `last_date`, into the ignored `history/out/`
  repository. The sample config's `history` keys point harbor, orchard and
  lighthouse into it ([the rules](../docs/wbs-history.md)).

## The hostile title

One open leaf on `lighthouse` has a title holding `<`, `>`, `&` and `"`, so
the demo shows record text rendered as text:

`Render <b>this</b> & "that" as text`

## Sites and roles

| Site | Constant(s) | Role | Shapes it carries |
|---|---|---|---|
| harbor | `BUSY` | well estimated, with model tiers; the busy site | two epics, each with an `issue:` id; a `depends_on` naming a leaf under the other epic; P1 to P4 each present, and leaves with no priority; at least 8 open, 5 finished (`x`), one in-progress (`~`) and one skipped (`-`) `issue:` leaf; `est: ?w` on open leaves that have estimates rows; the estimates file, with a row for every open `issue:` leaf, one epic row and one finished row |
| orchard | `OWN_UNIT` | `est:` values and a unit of its own | `Nd`, `Nw`, `?w` and the non-time `Npt`; no priority at all; at least three issue-less leaves; a `depends_on` entry that is an outline number (`2.1`) and names no issue |
| quarry | `UNESTIMATED` | wholly unestimated; the P0 demo | no `est:` and no estimates file; priorities on some leaves only; an open P0 leaf and the open leaf it depends on (#10); a `depends_on` naming a leaf under another epic; a `depends_on` naming an issue that exists on no site; `h: 0`, `h: 4` and a quoted `h: "2"` on open leaves, and `h: high`, which its panel lists as `bad_h` (#36) |
| lighthouse | `SMALL`, `HOSTILE_SITE` | small | one epic and one top-level leaf with no epic; the hostile title on an open leaf; a requirements file, which puts it in the trace, with `UC`, `SR`, `FR`, `QA` and `CON` items, a use case with `needs:` and an `FR` that `refines:` an `SR` |
| bakery | `NO_WBS` | no `WBS.md` | a folder holding only a short `README.md` |
| marsh | `UNREADABLE` | cannot be read | ordinary node lines, one of whose titles holds a Latin-1 byte that is not valid UTF-8 |

The sample history gives four sites a role each (`as_of` 2026-01-20, a
four-week window):

| Site | Constant | History role |
|---|---|---|
| harbor | `HISTORY_RECENT` | a glyph change inside the window (2026-01-08) |
| orchard | `HISTORY_STALE` | its last glyph change (2025-11-03) is older than the window |
| lighthouse | `HISTORY_YOUNG` | history that begins inside the window (2026-01-05), with no glyph change since |
| quarry | `HISTORY_LESS` | a `WBS.md` and no `history` key: no data, in no repository |

marsh and bakery have no history either.

Every readable site (harbor, orchard, quarry, lighthouse) has at least one
open and at least three finished `issue:` leaves. Ids are unique within a
site and may repeat across sites: `Issue-7` is open on both harbor and
quarry.

`marsh/WBS.md` is deliberately invalid UTF-8. Exempt it by its exact path
from UTF-8 text tools, and never re-save it in an editor, which would
re-encode the byte.

## Using the samples

Import a site's role from `tests/sample_roles.py` and never hard-code a
site name. pytest puts `tests/` on the import path by itself; a tool run
outside pytest adds `<repo>/tests` to `sys.path` first.

## Rules for later issues

- Later issues add their own sample files under `samples/`. Generated
  output goes only in `out/` folders, which git ignores and the sample
  checks skip.
- The busy site's rule: a change that adds an open `issue:` leaf to harbor
  adds its estimates row in the same change, or puts the leaf on another
  site.
- The line-ending rule: `.gitattributes` pins sample `.md`, `.toml` and
  `.json` files to `eol=lf`. A sample file of a type those lines do not
  cover adds its own `eol=lf` line, or `-text` if it is binary, above the
  marsh override (`samples/marsh/WBS.md -text`). No line below the
  override may match `samples/marsh/WBS.md`. The sample check fails
  otherwise, on every platform.
- Reserved folder names: no site may be named `devagent`, `out`,
  `history`, `actuals` or `forecast`.
- The history rule: the newest version of each site's sample history is
  its current `WBS.md`, so a change to a sample `WBS.md` reviews
  `samples/history/` in the same change. A glyph edit is a state change at
  the plan's `last_date`, which can change a history role.
