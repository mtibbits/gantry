# Gantry

Gantry is a planning tool for people who run many projects at once. Each
project is a worksite with its own tower crane. Gantry reads the work
breakdowns (`WBS.md`) that the [devagent](https://github.com/mtibbits/devagent)
plugin keeps for each project and builds static planning pages from them.
Every number on those pages comes from records already kept.

## What it shows

- **Worksite page**: one per project, the dependency network with its
  critical path, a rolling-wave zoom, and a schedule across parallel
  worklanes. See [docs/worksite-page.md](docs/worksite-page.md).
- **Strategic page**: across all your projects, where your tokens and
  attention are going against where your priorities say they should
  (planned).
- **Daily screen**: for your phone, anything on fire first, then today's
  tasks to start warming up on (planned).
- **Week view**: the week plan as a page, laid out over the operator's
  attention (planned).
- **Requirements trace**: an overlay on worksite pages linking each leaf to
  the requirements it satisfies. See
  [Reading the trace](docs/worksite-page.md#reading-the-trace).

A live demo, built only from fictional sample projects, is at
<https://mtibbits.github.io/gantry/demo/>.

## Try it

Prerequisites: Python 3.11 or later as `python3`, and git. Today the demo
needs nothing else; a later step that needs another program checks for it
and names it (see [docs/demo.md](docs/demo.md)).

<!-- Editor note: .github/workflows/quickstart.yml runs the second block below verbatim on a fresh clone, and tests/test_docs.py pins both blocks and the lines around them. -->
<!-- quickstart:begin -->

Get the code:

```sh
git clone https://github.com/mtibbits/gantry.git
cd gantry
```

Build the demo, from the clone:

```sh
python3 tools/demo_build.py
```

The script prints the demo's output folder last (`samples/out`); open
`index.html` there in a browser.

On Windows, run `py -3 tools\demo_build.py` in place of the second block.

<!-- quickstart:end -->

## Install

Gantry needs Python 3.11 or later and no third-party package at run time.
From a clone, run it in place:

```sh
python3 -m gantry build --config <file>
```

Or install the `gantry` command into a virtual environment (stock Debian and
Ubuntu refuse `pip install` into the system Python, and provide `venv` in the
`python3-venv` package):

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install .
gantry build --config <file>
```

On Windows, `py -3 -m venv .venv` and `.venv\Scripts\activate` take the place
of the first two lines.

As a Claude Code plugin, load a clone and run the build from a session; it
needs no install. A marketplace install comes with the first release.

```
claude --plugin-dir <clone>
/gantry:build <installation config>
```

What a run does (`gantry` here is `python3 -m gantry` when you run in place):

- `gantry build --config <file>` builds every site's worksite page and the
  index that links them, and writes the week plan to `week-plan.json`
  ([docs/week-plan.md](docs/week-plan.md)); `--site <name>` builds one
  site's page.
- `gantry portfolio --config <file>` checks the installation's portfolio
  file without building ([docs/portfolio-file.md](docs/portfolio-file.md)).
- Every key of the installation config is in [docs/config.md](docs/config.md).

## Your data stays private

Gantry is one public tool with many private installations. This repository
holds only the tool, its documentation and made-up sample data.

- Every data path comes from an installation config that lives outside this
  repository and is named on every run; there is no default location
  ([docs/config.md](docs/config.md)).
- A build opens files only under the roots that config names, plus its own
  package files, and checks this on every access; the linked section also
  says where git, reading a site's history repository, may read above them
  ([docs/config.md](docs/config.md#where-a-build-may-read-and-write)).
- The demo is built from the samples only
  ([docs/demo.md](docs/demo.md)), and this repository's Pages site carries
  only the demo and a page that links to it. Pages built from real data are meant to be served only as
  private pages or on a private network, never on a public site; Gantry
  writes them to your output folder and publishes nothing itself.
- A commit check, in CI and as an optional local hook, checks what is
  committed here for real pages, configs, local paths and, in the local
  hook, private names; what it can and cannot catch is in
  [docs/commit-check.md](docs/commit-check.md).

## Documentation

Every document is listed in [docs/README.md](docs/README.md). The ideas
behind Gantry, for a first reading, are in [docs/concepts.md](docs/concepts.md).

## Development

Install Gantry with its test tools, in an activated virtual environment
(above), where `python` is that environment's interpreter:

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

`tests/test_docs.py` checks every relative link and anchor in this README and under
`docs/`, so a new doc needs its line in `docs/README.md`, and the change that
adds a view turns its line above into a link.
`.github/workflows/quickstart.yml` runs the quickstart above verbatim on a
fresh clone.

## License

MIT. See [LICENSE](LICENSE).
