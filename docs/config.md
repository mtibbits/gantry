# Installation config reference

A Gantry **installation** is one private copy of Gantry with its own data. Its
**installation config** is one TOML file that tells a Gantry run where that
installation's data lives. Keep it outside this repository. If you do keep
one in a clone, name it `*.local.toml`: the `.gitignore` rule for that name
keeps it out of commits, and no other name is covered.

Every run names its config explicitly:

```
gantry build --config <file>
python -m gantry build --config <file>
gantry portfolio --config <file>
python -m gantry portfolio --config <file>
```

`gantry portfolio` checks the portfolio file the config names, without
building ([the portfolio file](portfolio-file.md#checking-the-file)).

There is **no default config location**: no home-folder lookup, no
environment variable and no fallback. A run with no `--config` is a usage
error (exit 2) naming the flag.

## Keys

<!-- Editor note: tests/test_portfolio_file.py reads the portfolio_file row
below; the issue that adds the build's read of the file (#23 or #61) rewords
"the build does not read it yet" and that test together. -->

| Key | Type | Required | Default | Meaning | Owner |
|---|---|---|---|---|---|
| `config_version` | integer | yes | none | the format version; this Gantry reads `1` | #7 |
| `devagent_config` | path | yes | none | devagent's `config.toml`, which names the sites | #7 |
| `output_dir` | path | yes | none | the folder the build writes pages into | #9 |
| `as_of` | TOML date (a string is refused) | no | the build machine's local date when the run starts | the date a build treats as today | every dated view |
| `default_unit_hours` | positive number, integer or float (0, a negative, infinity or a boolean is refused) | no | `6` | the agent-hours given to a leaf with no estimate | #9 |
| `portfolio_file` | path | no | none | the installation's portfolio file ([format](portfolio-file.md)); `gantry portfolio` checks it; the build does not read it yet (#23 or #61 adds the read) | #22 |
| `fires_list` | path | no | none | the installation's list of things that need the operator now | #30 |
| `[requirements]` | table | no | none | settings for requirements files | #15 |
| `[site.<name>]` | table | no | none | per-site overrides, keyed by the devagent `[project.<name>]` table name | #7 |

Keys of a `[site.<name>]` table:

| Key | Type | Required | Default | Meaning | Owner |
|---|---|---|---|---|---|
| `requirements` | path | no | none | the site's requirements file ([format](requirements-format.md)); the site is in the trace only when this is set | #15 |
| `estimates` | path, or `false` | no | discovery | the site's estimates file, read whatever its name and never replaced by discovery; `false` turns discovery off; absent, the planning directory's latest `estimates-YYYY-MM-DD*.md` is read ([the rules](worksite-page.md#the-estimates-file)) | #10 |

Keys of the `[requirements]` table:

| Key | Type | Required | Default | Meaning | Owner |
|---|---|---|---|---|---|
| `br_namespace` | name matching `[A-Za-z0-9][A-Za-z0-9_.-]*` (any other value is refused) | no | `life` | the installation namespace: the namespace of the operator's business requirements, which live only in the portfolio file ([format](requirements-format.md#namespaces)) | #15 |

A path is a TOML string. The **Owner** column names the issue that reads the
key. Every key is type-checked, accepted and resolved now; a key whose owner
has not landed yet has no effect on the build.

An example: the repository's sample (`samples/gantry.sample.toml`), which
puts lighthouse in the trace, plus an optional site table and the
`[requirements]` table at its default.

```toml
config_version = 1
devagent_config = "devagent/config.toml"
output_dir = "out"
as_of = 2026-01-20
portfolio_file = "portfolio.md"

[requirements]
br_namespace = "life"

[site.lighthouse]
requirements = "lighthouse/requirements.md"

[site.harbor]
estimates = false
```

## The version rule

`config_version` is an integer, currently `1`. A file with no
`config_version`, or with a number other than the one this Gantry reads, is
refused, naming both numbers.

- **Compatible (no version bump):** adding an optional key. An older Gantry
  then refuses the newer key as an unknown key, which says what to upgrade.
- **Incompatible (bumps `config_version` by one):** making a key required,
  removing a key, or changing a key's type or meaning. This holds before the
  first release as after it. Gantry then refuses a file with the older number,
  naming both numbers, and the history below records what changed.

### Version history

| Version | Change |
|---|---|
| 1 | Initial format (#7). |

## Encoding

Both files are read as UTF-8. A file that starts with a byte-order mark (which
some Windows editors and PowerShell 5.1 write) is refused with a message
saying so: save it without one.

## Unknown keys

An unknown key is refused, naming it, at the top level and inside a
`[site.<name>]` table (for example `unknown key 'site.harbor.foo'`), so a
typo in a private file fails loudly instead of being ignored.

## Paths

These rules hold in every config Gantry reads: this file and devagent's
`config.toml`.

- **A bare `~` expands** to the home folder: `~` alone, or `~` followed by
  `/`, on every operating system. A backslash after `~` counts as a separator
  only on Windows; on Linux such a value is refused as a user-name form.
  `~` followed by a user name is refused on every operating system (only a
  bare `~` is expanded).
- **A relative path resolves against the folder of the file that holds it**,
  never against the working directory. In the installation config that is the
  config's own folder; for a `devdoc_dir` it is the folder of devagent's
  `config.toml`. This base is Gantry's rule: devagent defines none, so a
  relative `devdoc_dir` can behave differently in devagent's own scripts.
- **A path absolute only on another operating system is never re-rooted**
  under the config's folder. A drive-letter path read on Linux, or a
  `/`-rooted path with no drive read on Windows, is an error naming the key
  in the installation config, and that site's problem as a `devdoc_dir`.
- **On Windows, a drive-relative path** (a drive letter and a colon with no
  separator after them) is refused: write it absolute or relative.

Resolution only makes paths absolute; it does not open them, and it does not
confine where a build may read; [Where a build may read and
write](#where-a-build-may-read-and-write) does.

## Where a build may read and write

A run opens files only under its **roots**, checked each time Gantry opens,
lists, stats, writes or deletes a file, not only when it reads the config.
Every file access in Gantry goes through one module, `gantry/roots.py`.

**What a root is.** A run's roots are:

- the installation config itself and the devagent config it names;
- the value of every path key (every key whose kind is a path, or a path or
  `false`, in the [keys table](#keys)), at the top level and in each
  `[site.<name>]` table. A key's root kind comes from the key, never from the
  disk: `devagent_config`, `portfolio_file`, `fires_list`, `requirements` and
  `estimates` are **file** roots, which admit only that one file (a file key
  that names a folder makes nothing under it readable); `output_dir` is a
  **directory** root, which admits everything under it;
- every site's planning directory (`devdoc_dir`), read from the devagent
  config; a `devdoc_dir` that cannot be resolved adds no root, and that site
  keeps its problem;
- the `gantry` package directory, read-only (the page templates and static
  files).

The roots grow as the config is read: the run first reads only the file
given with `--config`, then only the devagent config that file names, and
only then the planning directories. **No default config is ever read**, from
the working directory, the home folder or the environment.

**Writable roots** depend on the command. `gantry build` may write only under
`output_dir`; a later `gantry refresh` will write only under its `[refresh]
dir`, and a later `gantry serve` writes nothing. Every other root is
read-only. A writable root may not equal or contain an input root (either
config file, a planning directory or the package directory): that is a
config error, exit 1, naming `output_dir` and every input it holds. This is
the one change for a config that worked before: an `output_dir` of `.`
beside the installation config, for example, now fails with
`key 'output_dir' contains the config file itself`. Point `output_dir` at a
folder of its own, such as `out`. A missing `output_dir` and its missing
parents are still created.

**Files are resolved where they are opened.** Each path is resolved at the
moment of use, with symlinks and Windows junctions followed and `..`
collapsed, and compared with each root (case-folded as the host folds it).
A path that lands outside every root is refused, and the refusal names the
config key or file kind and the site, never a path: `WBS.md for site harbor
is outside the configured roots`. The refusal is reported where that file's
other errors are: a planning directory or its `WBS.md` becomes that site's
reason, an estimates file an unreadable-estimates entry (the page still
builds), a config file a config error (exit 1), and a page write a failed
write naming `output_dir` (exit 1 for `--site`). A refused write is a
*write* refusal, a refused read a *read* refusal; neither is an operating
system error.

**A link at a root moves that root.** Because a root is itself resolved with
links followed, a planning directory that is a link (a planning tree kept on
another drive) becomes a root at its target. That is allowed on a real
installation. For the demo it is caught by sample mode (below), by the test
that lists every root of the sample config, and by the Pages stamp check. A
link *below* a root that leads out of every root is what the open-time check
refuses.

**Sample mode** means every root lies in the sample directory, `samples/`
under the gantry root (the folder holding the running `gantry` package),
found from the package itself and never from the working directory or the
config's folder. The package directory is not counted. A copy of the samples
anywhere else is never sample mode. See
[the build stamp](worksite-page.md#the-build-stamp-and-sample-mode).

**The threat model.** The check guards against links, `..` segments and stray
paths in data and config, and against reading config the run was not given.
It is not a defence against a concurrent adversary on the build machine who
swaps a path component for a link between the check and the open, and a hard
link is not detected (it is indistinguishable from an ordinary file).

## Sites

The sites come only from devagent's config: they are the keys of its parsed
`project` table, in the order each first appears in the file. A nested table
such as `[project.<name>.permissions]` is a field of its site, never a site.
A site's planning directory is its `devdoc_dir`. Gantry keeps no site list
of its own: a project added to devagent's config appears with no change to
the installation config.

A site is listed with the reason it cannot be read, and does not stop the
others, when:

- its name does not fully match `[A-Za-z0-9][A-Za-z0-9_.-]*` (devagent's
  `ID_TOKEN_RE`; names are used in file names and in `<site>:Issue-N`
  references);
- it has no `devdoc_dir`, or one that is not a usable path;
- its planning directory does not exist or is not a directory;
- there is no `WBS.md` file in it, or Gantry cannot tell;
- its `WBS.md` cannot be opened or read (`WBS.md cannot be read: <reason>`),
  or is not valid UTF-8 (`WBS.md is not valid UTF-8 at byte N`).

A `[site.<name>]` table naming no devagent project is a **warning**: it is
printed on stderr and also returned as data with the sites, for later pages
to list.

A site is in the trace only when its `requirements` key is set. Two kinds of
site cannot be in the trace, because their name cannot be a requirements
namespace: a site named like `br_namespace` (exactly, case included), and a
site whose name fails the pattern above. Each is still listed; if its
`requirements` key is set, the key is not read and a warning on stderr
names the site and the key (`key 'site.<name>.requirements' is not read:
... so it cannot be in the trace`). With no key set there is no warning.
The rules are in [requirements-format.md](requirements-format.md#in-the-trace).

A missing or invalid devagent config, or one with no `project` table or an
empty one, is an error: a build with no sites almost always means
a wrong path.

`gantry build` builds every site: each site's worksite page to
`<output_dir>/sites/<name>.html`, then `<output_dir>/index.html`, which
lists every site with its open leaves, finish and report count, or why it
was not built. It prints one absolute path per file written. A site that
cannot be built is listed on the index and on stderr with its reason, and
does not stop the others; a name that fails the pattern above is printed
escaped (a tab as `\t`, an ESC as `\x1b`). The reading rules are in
[worksite-page.md](worksite-page.md#reading-wbsmd).

`gantry build --site NAME` writes only that site's worksite page, and no
index, creating `<output_dir>/sites/` when it is missing;
[worksite-page.md](worksite-page.md#every-site-and-the-index) has the
pages, the index, the reasons and the errors. The page's durations use
`default_unit_hours`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | the index was written and at least one site page was written (sites not built and warnings do not change it), or `--site` wrote its page |
| 1 | a config error: the installation config or devagent's config is missing, unreadable, invalid or refused; no site page was written (the index is still written); the index or the output folder cannot be written; or, with `--site`, a site that is not configured, clashes in case with an earlier site or cannot be built, a render error, or a page that cannot be written |
| 2 | a usage error, such as a missing `--config` |

## Supported platforms

Windows and Linux, on Python 3.11 or later (for `tomllib`), with no
third-party package at run time; pytest is a development dependency only.
CI runs the tests on both, on Python 3.11 and the newest release. macOS is
not tested, though nothing here is meant to prevent it.

## The Claude Code plugin

The repository is also a thin Claude Code plugin. Load it from a clone and
run the build from a session:

```
claude --plugin-dir <clone>
/gantry:build <installation config>
```

`/gantry:build` runs `tools/gantry-build.sh`, which runs
`python -P -m gantry build --config <config>` with the plugin's root on
`PYTHONPATH`, so it needs no pip install. It runs the command `python`, or
`python3` when there is no `python` (stock Debian and Ubuntu provide only
`python3`), and first checks that it is Python 3.11 or later. `-P` keeps the
working directory off `sys.path`, so a `gantry` folder there cannot shadow
the plugin's package. The argument is passed as one quoted argument, never
through shell expansion of its contents, and a relative path resolves
against the session's working directory. Because the argument is taken
literally, `~` in it is not expanded: write the path in full, or relative
to the working directory.

The launcher's grammar is `gantry-build.sh [<config>]`: with no argument it
runs `gantry build`, which reports the missing `--config` (exit 2), and an
empty argument counts as none; with one,
it passes it as `--config`; with more, it is a usage error (exit 2). With
neither `python` nor `python3` on `PATH` it exits 127.

On Windows, a Microsoft Store `python` alias that is not a real install fails
the version check; the launcher's message shows what that command reported.
Install Python, or turn the alias off in Windows' app execution alias
settings.

## Adding a key

A later issue that needs a key adds it, in its own change, to:

1. the key registry in `gantry/config.py` (`KEYS`, `SITE_KEYS` for a
   per-site key, or `REQUIREMENTS_KEYS` for a key of the `[requirements]`
   table), with its type, whether it is required, its default and its
   owning issue;
2. a field of the same name on `Config` (or `SiteOverride` for a per-site
   key, or `RequirementsSettings` for a `[requirements]` key) that
   `load_config` fills; a path key is resolved there like the
   others (a test checks every registry key has its field);
3. a row in the tables above, whose Owner cell is the registry's owner
   exactly (a test checks every registry key has its row);
4. `samples/gantry.sample.toml`, when the key names a file the samples
   provide.

A new optional key needs no version bump; anything else follows the version
rule above.
