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
- Gantry's commands make no network connection: there is no telemetry and
  no update check. This needs git 2.45 or later: history reads tell git not
  to fetch missing objects (`GIT_NO_LAZY_FETCH`), and older git ignores
  that, so in a partial clone it may fetch them from the clone's remote.
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
  [portfolio file](docs/portfolio-file.md). Files in devagent's formats,
  such as `WBS.md` and the estimates file, follow devagent's
  compatibility, not this rule;
- the output folder: its page paths (`index.html`, `sites/<name>.html`), and
  `week-plan.json`, where a change that bumps `plan_version` is a break
  ([its version rule](docs/week-plan.md#the-version-rule));
- a command's contract: `gantry build`, `gantry portfolio` and
  `/gantry:build`;
- the tools it runs on: raising a stated oldest supported version of a tool
  under [Supported versions](#supported-versions) is a break.

A release with a break says, in its CHANGELOG section, what broke and what
an operator changes.

Before 1.0.0, a break bumps the minor version and any other change the
patch. From 1.0.0, a fix bumps the patch; an added view, command or optional
config key bumps the minor; and a break bumps the major.

A change reaches installed users as a release, under a new version string.
Whether `claude plugin update` also delivers a merge that leaves the version
unchanged to an install from GitHub is not yet checked; the second release's
update check is the first to show it. A fresh install between releases is
expected to take `main`'s tree as it is then, under the last released
version string, so the release checks below run while `main` is still at
the tag; the first release's install check is the first to confirm it. A
clone loaded with
`--plugin-dir` is not pinned: it runs whatever the clone has checked out.

### Cutting a release

1. In one change, bump `version` in `.claude-plugin/plugin.json`, set the
   same value as `pyproject.toml`'s `[project] version`, and move the
   `## [Unreleased]` lines of CHANGELOG.md under a new
   `## [<version>] — <date>` section, where `<date>` is the day you tag.
   Merge it.
2. Tag `main` only while its head is the release commit: the last commit
   that changed the release section. If anything else merged after it,
   first move that change's `## [Unreleased]` lines into the release
   section (that commit is then the release commit), or cut a new version.
3. On a clean, up-to-date `main`, validate both manifests with warnings
   treated as errors. The tag command does not do this; it checks only that
   `plugin.json` and the marketplace entry agree:

   ```
   claude plugin validate --strict .
   ```

4. Then push the annotated tag `gantry--v<version>`:

   ```
   claude plugin tag --push
   ```

5. Save that version's CHANGELOG section to `<section file>` and publish the
   GitHub release on the tag. `--verify-tag` stops the release when the tag
   is missing, instead of letting `gh` create a tag of its own that skipped
   the steps above:

   ```
   gh release create gantry--v<version> --verify-tag --title "gantry <version>" --notes-file "<section file>"
   ```

### Checks every release runs

Run these while `main` is still at the tag.

- Private vulnerability reporting is on: this prints `true`.

  ```
  gh api repos/mtibbits/gantry/private-vulnerability-reporting --jq .enabled
  ```

- The plugin check below runs on the latest Claude Code release: run
  `claude update` first. What is posted about the release records the
  machine's operating system, `claude --version`, `git --version` and
  `bash --version`.

- The quickstart job at the tag: its run for the tag push, or this:

  ```
  gh workflow run quickstart.yml --ref gantry--v<version>
  ```

- The plugin, by hand. At the first release, a fresh install with the two
  commands under [Install](#install). At each later release, the update
  path from an install of the previous release; it is not yet exercised,
  and the second release is the first to run it. That release replaces this
  "not yet" and the one under Versioning & releases with what it showed:

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

## Support

Gantry has one maintainer and is offered as is, under the MIT license. It
supports Linux (checked on Ubuntu) and Windows.

### What is promised

For the latest release:

- No silent breaks: a break ships only under the version rule above, and
  its CHANGELOG section says what to change.
- Every user-visible change is in [CHANGELOG.md](CHANGELOG.md), and every
  release is a tag and a GitHub release.
- Gantry sends nothing anywhere (see
  [Your data stays private](#your-data-stays-private)).
- Gantry needs no third-party Python package at run time.
- The tool versions below work.
- Security reports have a private route, are acknowledged within seven
  days, and have a published policy ([SECURITY.md](SECURITY.md)).

### What is not promised

No deadline for a fix, no release cadence, no backports to older releases,
no commitment to a requested feature, and no platform but Linux and
Windows. Gantry may run elsewhere, such as macOS, but nothing checks it.

For now, only collaborators can open issues and pull requests or comment
on this repository. Outside issues and pull requests will open later, and
will be read when the maintainer can. Security reports go through
[SECURITY.md](SECURITY.md) in the meantime.

### Supported versions

| Tool | Oldest supported | How it is checked |
|---|---|---|
| Python | 3.11 | On every change: 3.11 and the latest 3.x release, on Ubuntu and Windows (`.github/workflows/test.yml`). |
| git | 2.45 | Latest: on every change, the git on the CI runners, Ubuntu and Windows, which may trail git's latest release; each release records the git it ran on. Oldest: stated from git's release notes, not yet tested: 2.45 is the oldest git that honours `GIT_NO_LAZY_FETCH` (see [Your data stays private](#your-data-stays-private)). |
| Claude Code | Not yet stated | At every release: the plugin check under [Checks every release runs](#checks-every-release-runs), on the latest release. |
| bash | Not yet stated | `/gantry:build` runs its launcher with bash. On every change: the launcher's tests, with the bash on the Ubuntu CI runner. At every release: the plugin check, with the bash Claude Code uses there. |
| devagent | 3.0.0 | Gantry reads the `WBS.md` files, config and records devagent writes. Latest: the maintainer's own installation runs the latest devagent release. Oldest: stated, not yet tested. |

Until a tool's oldest version is stated, only its latest release is
supported. That moves with each upstream release and is not a break.
Stating an oldest version widens support; raising a stated one is a break.
A failure on a tool's latest release is a defect, fixed in a release under
the version rule.

devagent needs one more rule. Gantry's full function needs devagent's
latest release. With an older supported devagent, every page still builds,
and anything that needs a newer devagent contract is shown as missing,
never filled in by Gantry. For example, without devagent's portfolio JSON
Gantry reads `WBS.md` instead, and gets none of what only the JSON carries.
A new Gantry feature that needs a newer devagent contract is not a break.

## License

MIT. See [LICENSE](LICENSE).
