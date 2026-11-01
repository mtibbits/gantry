# Gantry

Gantry is a planning tool for people who run many projects at once. Each
project is a worksite with its own tower crane. Gantry reads each project's
work breakdown (`WBS.md`, in the format the
[devagent](https://github.com/mtibbits/devagent) plugin keeps) and builds
three kinds of planning page:

- a **worksite page** for each project: the dependency network with its
  critical path, a rolling-wave zoom, and a schedule across parallel
  worklanes;
- a **strategic page** across all your projects: where your tokens and
  attention are going against where your priorities say they should;
- a **daily screen** for your phone: anything on fire, then today's tasks to
  start warming up on.

Status: design approved, nothing built yet.

## Your data stays private

This repository holds only the tool, its documentation, and made-up sample
data. Every installation reads its own project data from paths in its own
configuration, which never lives in this repository. Pages built from real
data are meant for a private network, never for a public site.

Installation config reference: [docs/config.md](docs/config.md).

How Gantry reads a site's `WBS.md`, and what it reports:
[docs/worksite-page.md](docs/worksite-page.md).

A commit check keeps real pages, configs, local paths and private names out of
this repository: [docs/commit-check.md](docs/commit-check.md).

How a page's tests check it is safe to open, with no injected markup and
nothing loaded from outside it: [docs/page-safety.md](docs/page-safety.md).

## License

MIT. See [LICENSE](LICENSE).
