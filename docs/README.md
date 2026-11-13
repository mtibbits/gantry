# Gantry documentation

One line per document. The repository's README says which views exist today
and how to try the demo.

- [config.md](config.md): the installation config reference, every key, the
  roots a build may read and write, supported platforms and the Claude Code
  plugin.
- [concepts.md](concepts.md): the ideas behind Gantry, for a public reader.
- [worksite-page.md](worksite-page.md): how Gantry reads a site's `WBS.md`,
  what it reports, and what the worksite page and the index show.
- [requirements-format.md](requirements-format.md): the requirements file
  format, its layers and IDs, and what its reader reports.
- [requirements.md](requirements.md): Gantry's own requirements, in that
  format.
- [portfolio-file.md](portfolio-file.md): the portfolio file, the operator's
  private priorities and capacity, and how `gantry portfolio` checks it.
- [week-plan.md](week-plan.md): how Gantry estimates each leaf's attention
  need, how the build plans the week, and what `week-plan.json` holds.
- [wbs-history.md](wbs-history.md): how Gantry reads a site's `WBS.md` history
  from its planning repository, and the sample history it is tested on.
- [demo.md](demo.md): the public demo built from the fictional samples, how it
  is checked, and how its Pages deploy is guarded.
- [commit-check.md](commit-check.md): the commit check that checks what is
  committed here for real pages, configs, local paths and private names, and
  what it can and cannot catch.
- [page-safety.md](page-safety.md): how a page's tests check it is safe to
  open, with no injected markup and nothing loaded from outside it.
