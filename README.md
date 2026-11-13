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

Status: `gantry build --config FILE` builds every site's worksite page and
the index that links them, and writes the week plan to `week-plan.json`
(`--site NAME` builds one site's page); the other pages are not built yet. `gantry portfolio --config FILE` checks the
installation's portfolio file without building.

## Your data stays private

This repository holds only the tool, its documentation, and made-up sample
data. Every installation reads its own project data from paths in its own
configuration, which never lives in this repository. Pages built from real
data are meant for a private network, never for a public site.

Installation config reference: [docs/config.md](docs/config.md).

How Gantry reads a site's `WBS.md`, what it reports, and what the worksite
page shows: [docs/worksite-page.md](docs/worksite-page.md).

The requirements file format, its IDs and what its reader reports:
[docs/requirements-format.md](docs/requirements-format.md).

The portfolio file, the operator's private priorities and capacity, and how
`gantry portfolio` checks it: [docs/portfolio-file.md](docs/portfolio-file.md).

How Gantry estimates each leaf's attention need, and what an `h:` score
means: [docs/week-plan.md](docs/week-plan.md).

How Gantry reads a site's `WBS.md` history from its planning repository, and
the sample history it is tested on: [docs/wbs-history.md](docs/wbs-history.md).

Gantry's own requirements, in that format:
[docs/requirements.md](docs/requirements.md).

A commit check keeps real pages, configs, local paths and private names out of
this repository: [docs/commit-check.md](docs/commit-check.md).

How a page's tests check it is safe to open, with no injected markup and
nothing loaded from outside it: [docs/page-safety.md](docs/page-safety.md).

## Development

Install Gantry with its test tools:

```
python -m pip install -e ".[dev]"
```

The page-script tests also need Node.js 24 and the harness's pinned jsdom:

```
npm ci --prefix tests/page_script
```

Node.js is for development only; installed Gantry never needs it. Without
it the page-script tests skip, except under `CI`, where they fail. See
[docs/page-safety.md](docs/page-safety.md).

The WBS.md history tests need `git` on the path, and follow the same rule:
without it they skip, except under `CI`, where they fail. Those tests
rebuild the git-ignored `samples/history/out` in the checkout once per
pytest session (`python tools/sample_history.py` builds it by hand), so run
one pytest session per checkout at a time. See
[docs/wbs-history.md](docs/wbs-history.md).

## License

MIT. See [LICENSE](LICENSE).
