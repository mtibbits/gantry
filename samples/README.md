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

## The hostile title

One open leaf on `lighthouse` has a title holding `<`, `>`, `&` and `"`, so
the demo shows record text rendered as text:

`Render <b>this</b> & "that" as text`

## Sites and roles

| Site | Constant(s) | Role | Shapes it carries |
|---|---|---|---|
| harbor | `BUSY` | well estimated, with model tiers; the busy site | two epics, each with an `issue:` id; a `depends_on` naming a leaf under the other epic; P1 to P4 each present, and leaves with no priority; at least 8 open, 5 finished (`x`), one in-progress (`~`) and one skipped (`-`) `issue:` leaf; `est: ?w` on open leaves that have estimates rows; the estimates file, with a row for every open `issue:` leaf, one epic row and one finished row |
| orchard | `OWN_UNIT` | `est:` values and a unit of its own | `Nd`, `Nw`, `?w` and the non-time `Npt`; no priority at all; at least three issue-less leaves; a `depends_on` entry that is an outline number (`2.1`) and names no issue |
| quarry | `UNESTIMATED` | wholly unestimated | no `est:` and no estimates file; priorities on some leaves only; a `depends_on` naming a leaf under another epic; a `depends_on` naming an issue that exists on no site |
| lighthouse | `SMALL`, `HOSTILE_SITE` | small | one epic and one top-level leaf with no epic; the hostile title on an open leaf |
| bakery | `NO_WBS` | no `WBS.md` | a folder holding only a short `README.md` |
| marsh | `UNREADABLE` | cannot be read | ordinary node lines, one of whose titles holds a Latin-1 byte that is not valid UTF-8 |

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
