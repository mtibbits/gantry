# Changelog

All notable changes to Gantry are recorded here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Gantry follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The version lives
in `.claude-plugin/plugin.json`; the README's "Versioning & releases" section
has the policy and the release procedure. Each user-visible change adds its
entry as a file in `changelog.d/`, and each release compiles the waiting files
into its section here.

## [Unreleased]

New entries are files in changelog.d/; README's "Versioning & releases" says how.

### Added

- The fires list: a private Markdown file of fires that are not leaves yet,
  the `fires_check_days` config key and `gantry fires --config <file>`,
  which checks it (docs/fires-list.md).
- Threshold and objective paths (#79): a requirement may state a
  `Threshold:` and an `Objective:`, a leaf's `objective:` names the
  requirements it serves at objective only, and selecting a requirement
  with an open objective-only leaf splits its path box into the threshold's
  path and end day and the objective's (docs/requirements-format.md,
  docs/worksite-page.md).
- A security policy with private vulnerability reporting (SECURITY.md).
- The README's Support section: what Gantry promises, what it does not, and
  the supported versions of Python, git, Claude Code and bash; git's oldest
  supported version is 2.45, the oldest that keeps history reads off the
  network (README.md).
- devagent as a supported tool: 3.0.0 or later. Full function needs
  devagent's latest release, and on an older one what needs a newer
  devagent contract is shown as missing (README.md).
- A reader of devagent's per-issue records (#31): STUCK markers, checklists
  and plans, read from every issue folder of a planning directory with no
  link followed, pinned by fixtures that devagent's own scripts write and a
  contract test against devagent 3.0.0 (`tests/devagent.pin`) on the Ubuntu
  CI legs; the harbor sample gains seven issue folders
  (docs/devagent-formats.md).

- The "Served from other sites" panel on a worksite page (#21): in a full
  build, each leaf on another site whose `satisfies:` names one of this
  site's requirements, linked to its leaf on that site's page and lit when
  its requirement is selected; requirement IDs of another built site link
  to its page; the trace report gains `sites_not_read` and
  `undefined_foreign` (docs/worksite-page.md, docs/requirements-format.md).

### Changed

- The requirements reader (#79): a meta block that ends a requirement's own
  text before its first nested item is now read, no longer reported; a
  `Threshold:` or `Objective:` list item it cannot place, silent prose
  before, is now reported `malformed_item`; and a statement leaves the
  requirement's text, so the Requirements panel no longer shows it and the
  path box shows it on selection (docs/requirements-format.md).
- In a full build, a requirement served only by another site's leaf is no
  longer unserved (#21): unserved counts, the unserved label and the
  `trace <site>:` line's `unserved` count every read site's leaves, and the
  label names any site not read ("not counted: …") instead of "served only
  within this site". A `--site` build is unchanged
  (docs/worksite-page.md).
- The week plan divides each day between sites by priority tier, P0 work
  and work in progress first, then by the portfolio file's intended shares;
  `week-plan.json` gains `notes`, each leaf's `tier`, and each site's
  shares, share-less days, share note, fair-share targets and P0 list
  (docs/week-plan.md).
- A break now includes raising the oldest supported version of a tool, and
  a release with a break says in its CHANGELOG section what to change
  (README.md).

## [0.1.0] — 2026-11-14

### Added

- The worksite page, one per project: the dependency network with its
  critical path, a rolling-wave zoom and a schedule across parallel
  worklanes; and the index that links every site's page (docs/worksite-page.md).
- `gantry build --config <file>`, with `--site <name>` to build one site's
  page, and `gantry portfolio --config <file>`, which checks the portfolio
  file without building (docs/config.md).
- The installation config at `config_version` 1: every data path comes from
  it, and a build reads and writes only under the roots it names
  (docs/config.md).
- The week plan: each build plans the week onto blocks, honours dependencies
  across sites, and writes `week-plan.json` (docs/week-plan.md).
- The attention estimate for each leaf, which the week plan is built on
  (docs/week-plan.md).
- The requirements file format, and the requirements trace overlay on
  worksite pages (docs/requirements-format.md, docs/worksite-page.md).
- The portfolio file: the operator's private priorities and capacity
  (docs/portfolio-file.md).
- The `WBS.md` history reader, which reads each site's planning repository;
  no page shows its answers yet (docs/wbs-history.md).
- The `/gantry:build` Claude Code plugin command, which runs the build with no
  pip install (docs/config.md).
- The public demo, built only from fictional sample projects, and its Pages
  site (docs/demo.md).
- The commit check, in CI and as an optional local hook (docs/commit-check.md).
- This plugin marketplace, the version policy and the release procedure
  (README.md).
