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

<!-- Editor note: tests/test_plugin_versioning.py pins the plugin install lines between the two markers below, and the commands under Versioning & releases. -->
<!-- plugin-install:begin -->
As a Claude Code plugin, Gantry installs from this repository's marketplace
and needs no pip install. Its `/gantry:build` runs the command `python`, or
`python3` when there is no `python`, which must be Python 3.11 or later.

```
claude plugin marketplace add mtibbits/gantry
claude plugin install gantry@gantry
```

Then, in a Claude Code session:

```
/gantry:build <installation config>
```

For how a marketplace install gets a new version, see
[Versioning & releases](#versioning--releases).

Or load a clone in place, which runs whatever the clone has checked out:

```
claude --plugin-dir <clone>
/gantry:build <installation config>
```
<!-- plugin-install:end -->

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

## Versioning & releases

<!-- Editor note: tests/test_plugin_versioning.py pins the commands in this section and the plugin install lines under Install. -->

Gantry follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html),
with plain `X.Y.Z` versions (no pre-release or build suffix), so one version
string is valid in both files that carry it. `.claude-plugin/plugin.json`
carries the version, `pyproject.toml`'s `[project] version` mirrors it, and
`tests/test_cli.py` keeps the two equal. The plugin's entry in
`.claude-plugin/marketplace.json` carries no version, because Claude Code
uses plugin.json's value. [CHANGELOG.md](CHANGELOG.md) follows Keep a
Changelog: each user-visible change adds a line under its `## [Unreleased]`
section.

A break is a change that makes something an existing installation depends
on fail. An installation depends on:

- its installation config: a change that bumps `config_version` is a break
  ([the version rule](docs/config.md#the-version-rule));
- the private files that config names, in formats Gantry defines: today the
  [requirements file](docs/requirements-format.md) and the
  [portfolio file](docs/portfolio-file.md);
- the output folder: its page paths (`index.html`, `sites/<name>.html`), and
  `week-plan.json`, where a change that bumps `plan_version` is a break
  ([its version rule](docs/week-plan.md#the-version-rule));
- a command's contract: `gantry build`, `gantry portfolio` and
  `/gantry:build`.

Before 1.0.0, a break bumps the minor version and any other change the
patch. From 1.0.0, a fix bumps the patch; an added view, command or optional
config key bumps the minor; and a break bumps the major.

A change reaches installed users as a release, under a new version string.
Whether `claude plugin update` also delivers a merge that leaves the version
unchanged to an install from GitHub is not yet checked; the second release's
update check is the first to show it. An install from a local folder runs
that folder in place, so it takes each edit at its next session start. A
fresh install between releases takes `main`'s tree as it is then,
under the last released version string, which is why the release checks
below run while `main` is still at the tag. A clone loaded with
`--plugin-dir` is not pinned: it runs whatever the clone has checked out.

### Cutting a release

1. In one change, bump `version` in `.claude-plugin/plugin.json`, set the
   same value as `pyproject.toml`'s `[project] version`, and move the
   `## [Unreleased]` lines of CHANGELOG.md under a new
   `## [<version>] — <date>` section. Merge it.
2. Tag `main` only while its head is that release commit. If anything merged
   after it, first move that change's `## [Unreleased]` lines into the
   release section, or cut a new version.
3. On a clean, up-to-date `main`, run this. It validates the manifests and
   pushes the annotated tag `gantry--v<version>`:

   ```
   claude plugin tag --push
   ```

4. Save that version's CHANGELOG section to `<section file>` and publish the
   GitHub release on the tag. `--verify-tag` stops the release when the tag
   is missing, instead of letting `gh` create a tag that was never
   validated:

   ```
   gh release create gantry--v<version> --verify-tag --title "gantry <version>" --notes-file "<section file>"
   ```

### Checks every release runs

Run these while `main` is still at the tag.

- The quickstart job at the tag: its run for the tag push, or this:

  ```
  gh workflow run quickstart.yml --ref gantry--v<version>
  ```

- The plugin, by hand. At the first release, a fresh install with the two
  commands under [Install](#install). At each later release, the update
  path from an install of the previous release; it is not yet exercised,
  and the second release is the first to run it:

  ```
  claude plugin marketplace update gantry
  claude plugin update gantry@gantry
  ```

  `claude plugin update` says a restart is required to apply the update.
  Either way, `claude plugin list` then shows the new version. Then build
  the demo with the installed plugin:

  1. In a folder that is in no git work tree, clone the tag, then run the
     demo build in `<clone>`, which runs every preparation step the demo
     needs:

     ```
     git clone --branch gantry--v<version> https://github.com/mtibbits/gantry.git "<clone>"
     cd "<clone>"
     python3 tools/demo_build.py
     ```

  2. Remove `<clone>/.git`, so the history-less sample site is in no
     repository and its history would read "no data: in no repository".
     No page shows history yet (docs/wbs-history.md), so the check is that
     this fails with "not a git repository":

     ```
     git -C "<clone>/samples" rev-parse --show-toplevel
     ```

  3. Delete `<clone>/samples/out`, the sample config's output folder.
  4. Start a Claude Code session in a new, empty folder `<start>` and run:

     ```
     /gantry:build <clone>/samples/gantry.sample.toml
     ```

  5. Check that every page written carries `built <time>; data as of
     <date>`, not `sample data as of <date>`: that shows the installed
     plugin wrote them.

- What is posted about a release (the issue comment, and the PR's record of
  the manifest checks) writes every local path as `<start>`, `<clone>` or
  `<home>`, and must pass this with exit 0 before it is posted:

  ```
  python3 tools/commit_check.py names --text "<file>"
  ```

## License

MIT. See [LICENSE](LICENSE).
