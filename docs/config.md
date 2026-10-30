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
```

There is **no default config location**: no home-folder lookup, no
environment variable and no fallback. A run with no `--config` is a usage
error (exit 2) naming the flag.

## Keys

| Key | Type | Required | Default | Meaning | Owner |
|---|---|---|---|---|---|
| `config_version` | integer | yes | none | the format version; this Gantry reads `1` | #7 |
| `devagent_config` | path | yes | none | devagent's `config.toml`, which names the sites | #7 |
| `output_dir` | path | yes | none | the folder the build writes pages into | #9 |
| `as_of` | TOML date (a string is refused) | no | the build machine's local date when the run starts | the date a build treats as today | every dated view |
| `default_unit_hours` | positive number, integer or float (0, a negative, infinity or a boolean is refused) | no | `6` | the agent-hours given to a leaf with no estimate | #9 |
| `portfolio_file` | path | no | none | the installation's portfolio file of shares and capacity | #22 |
| `fires_list` | path | no | none | the installation's list of things that need the operator now | #30 |
| `[site.<name>]` | table | no | none | per-site overrides, keyed by the devagent `[project.<name>]` table name | #7 |

Keys of a `[site.<name>]` table:

| Key | Type | Required | Default | Meaning | Owner |
|---|---|---|---|---|---|
| `requirements` | path | no | none | the site's requirements file | #15 |
| `estimates` | path, or `false` | no | discovery | the site's estimates file; `false` turns discovery off | #10 |

A path is a TOML string. The **Owner** column names the issue that reads the
key. Every key is type-checked, accepted and resolved now; a key whose owner
has not landed yet has no effect on the build.

An example: the repository's sample (`samples/gantry.sample.toml`) plus an
optional site table.

```toml
config_version = 1
devagent_config = "devagent/config.toml"
output_dir = "out"
as_of = 2026-01-20

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
confine where a build may read (that is #43).

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
- there is no `WBS.md` file in it, or Gantry cannot tell.

A `[site.<name>]` table naming no devagent project is a **warning**: it is
printed on stderr and also returned as data with the sites, for later pages
to list. A missing or invalid devagent config, or one with no `project`
table or an empty one, is an error: a build with no sites almost always means
a wrong path.

`gantry build` prints one tab-separated line per site: the name, the planning
directory (`-` when it cannot be resolved), and `WBS.md found` or
`cannot read: <reason>`. A name that fails the pattern above is printed
escaped (a tab as `\t`, an ESC as `\x1b`), so each site stays one line of
three fields. It writes nothing yet; the page writers join in later issues.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | the config is valid and at least one site is listed (site problems and warnings do not change it) |
| 1 | a config or data error: the installation config or devagent's config is missing, unreadable, invalid or refused |
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

1. the key registry in `gantry/config.py` (`KEYS`, or `SITE_KEYS` for a
   per-site key), with its type, whether it is required, its default and its
   owning issue;
2. a field of the same name on `Config` (or `SiteOverride` for a per-site
   key) that `load_config` fills; a path key is resolved there like the
   others (a test checks every registry key has its field);
3. a row in the tables above, whose Owner cell is the registry's owner
   exactly (a test checks every registry key has its row);
4. `samples/gantry.sample.toml`, when the key names a file the samples
   provide.

A new optional key needs no version bump; anything else follows the version
rule above.
