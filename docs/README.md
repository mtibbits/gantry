# Gantry documentation

One line per document. The repository's README says which views exist today
and how to try the demo.

- [config.md](config.md): the installation config reference, every key, the
  roots a build may read and write, supported platforms and the Claude Code
  plugin.
- [concepts.md](concepts.md): the ideas behind Gantry, for a public reader.
- [worksite-page.md](worksite-page.md): how Gantry reads a site's `WBS.md`,
  what it reports, and what the worksite page and the index show.
- [strategic-page.md](strategic-page.md): the strategic page, each site's
  intended share against its backlog share under the requirements it serves,
  each site's five time thieves, and what it adds to the index.
- [requirements-format.md](requirements-format.md): the requirements file
  format, its layers and IDs, and what its reader reports.
- [requirements.md](requirements.md): Gantry's own requirements, in that
  format.
- [portfolio-file.md](portfolio-file.md): the portfolio file, the operator's
  private priorities and capacity, and how `gantry portfolio` checks it.
- [fires-list.md](fires-list.md): the fires list, the operator's private
  list of fires that are not leaves yet, its entries and how they end, and
  how `gantry fires` checks it.
- [refresh-and-serve.md](refresh-and-serve.md): the refresh job, `gantry
  refresh`: what one run does, the refresh folder, the update record, its
  exit codes, scheduling it, the update list and first-day failures.
- [daily-screen.md](daily-screen.md): the daily screen; today the fires
  every full build collects across sites, what each status means, and the
  `fires:` line the build prints.
- [week-plan.md](week-plan.md): how Gantry estimates each leaf's attention
  need, how the build plans the week from the portfolio file's capacity or
  the capacity devagent's config declares, what `week-plan.json` holds and
  what the week view page shows.
- [wbs-history.md](wbs-history.md): how Gantry reads a site's `WBS.md` history
  from its planning repository, and the sample history it is tested on.
- [devagent-formats.md](devagent-formats.md): devagent's per-issue records
  (STUCK markers, checklists, plans) that Gantry reads, the devagent source
  lines and pinned release each mirrors, and the contract test that pins them;
  and the capacity declaration in devagent's config, its keys, the rule
  strings Gantry mirrors and the test that holds Gantry to devagent's reader.
- [demo.md](demo.md): the public demo built from the fictional samples, how it
  is checked, and how its Pages deploy is guarded.
- [commit-check.md](commit-check.md): the commit check that checks what is
  committed here for real pages, configs, local paths and private names, and
  what it can and cannot catch.
- [page-safety.md](page-safety.md): how a page's tests check it is safe to
  open, with no injected markup and nothing loaded from outside it.
