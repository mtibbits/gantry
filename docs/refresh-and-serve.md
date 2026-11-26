# Refreshing the data and serving the pages

The **refresh job** keeps an installation's pages current without a Claude
session: an OS scheduler runs `gantry refresh --config <config>` every
`interval_minutes`, and each run updates the data root, records the update,
builds a fresh set of pages and swaps it in whole. This page covers the
refresh half; #64 adds serving the pages.

<!-- Editor note: tests/test_docs.py's test_refresh_doc_covers_the_setup_points,
test_refresh_doc_commands_parse, test_refresh_doc_scheduler_examples_hold_no_path_or_host
and test_refresh_doc_record_examples_read read this page: its seven headings,
the folder entries, the record's fields and statuses, both JSON examples, the
exit-code table and every `gantry refresh` line in the examples. Reword them
together. -->

The `[refresh]` table of the installation config sets the job
([its keys](config.md#keys)):

```toml
[refresh]
dir = "<refresh folder>"
interval_minutes = 15
update = [["git", "-C", "<data clone>", "pull", "--ff-only"]]
step_timeout_minutes = 10
```

## What one run does

One run is:

```
gantry refresh --config <config>
```

or, from a virtual environment, `<python> -P -m gantry refresh --config <config>`.
Run it from an install pinned at a release (a clone checked out at a release
tag and installed, as in [Scheduling](#scheduling)), not from a development
checkout whose code changes under the scheduler. It needs only Python and
the programs the update list names. In order, it:

1. **Takes the lock.** It creates `[refresh] dir` and its `generations/`
   folder when they are missing, then takes an exclusive OS lock on
   `lock` in that folder. When another run holds it, it prints
   `skipped: a refresh is still running`, exits 0 and changes nothing. The
   OS frees the lock when its holder ends, so a run that crashed never
   blocks the next one. Stopping the job from outside (a hand `kill`, or a
   wrapper's own time limit) does not stop an update command that is
   running: it goes on outside the lock and with no time limit, and a pull
   may still hold `.git/index.lock`.
2. **Updates the data root.** It runs each command of `[refresh] update` in
   order, without a shell, in the installation config's folder, each for at
   most `step_timeout_minutes`. A command that runs longer is stopped with
   every process it started: on Linux and other POSIX systems the command
   leads its own process group, which is killed; on Windows
   `taskkill.exe /T /F` is run by its full path under `SYSTEMROOT`, so a
   scheduler's short `PATH` cannot hide it. The first command that exits
   non-zero, runs out of time or cannot be started ends the update; later
   commands do not run. Every command's output, and the build's, goes to
   `last-run.log`.
3. **Records the update.** It writes `update.json` with this run's update
   and the previous record's last build, so the build that follows can show
   a failed update on the very next page.
4. **Builds a new generation.** Whether or not the update succeeded, it runs
   the build in a child process, with the Python that runs the refresh and
   the folder holding the running `gantry` package on `PYTHONPATH`:
   `<python> -P -m gantry build --config <config> --output-dir <refresh folder>/generations/<id>`,
   in `[refresh] dir`, with the same time limit. `-P` keeps the working
   folder off Python's import path, so a `json.py` in a pulled repository or
   the refresh folder never runs inside the build. A build with
   `--output-dir` writes its completion marker, `build.json`, last
   ([the marker](worksite-page.md#every-site-and-the-index)).
5. **Swaps it in whole.** When the build exited 0 or 1 and its `build.json`
   holds that same exit code, the run rewrites `current` to name the new
   generation (a reader on Windows can hold `current` open, so the rewrite
   is retried for 2 seconds; when it still fails, the live generation stays,
   the new one is deleted and the record says `swap failed`). A build that
   exits 1 on purpose (no site page was written, say) is swapped in: its
   pages say why. Any other build (no
   marker, a marker that does not match, another exit code, a crash after
   the index, a timeout) leaves the live generation as it was, and its own
   generation is deleted.
6. **Records the build.** It writes `update.json` again, adding this run's
   build and swap.
7. **Sweeps.** After a swap it deletes every generation but the new one and
   the one `current` named before. A generation that cannot be deleted (a
   file in it is open on Windows) is left for the next run and never fails
   this one. When `current` held something that is not a generation id, the
   sweep is skipped.

Each generation is built from nothing, so a page for a site that is no
longer built is gone from the live generation after the next refresh.

**Freshness.** With the scheduler running the job every `interval_minutes`,
a change pushed to a copy the update pulls is in the live generation by the
end of the first refresh that starts after the push, is not skipped, and
whose update succeeds. That bound holds only while a run finishes within one
interval: keep the update's and the build's time well inside it. When an
update fails, the generation swapped in and the record name the failure and
when data was last updated.

## The refresh folder

`[refresh] dir` holds only the job's own files. It may not equal or contain
the installation config, the devagent config, a planning directory, a
site's `history` directory or the `gantry` package: that is a config error
naming `refresh.dir` ([the rule](config.md#where-a-build-may-read-and-write)).
Gantry itself writes nothing outside it; the update commands write where
they are pointed (the data clones).

- `lock`: the file the run locks; it stays empty.
- `update.json`: [the update record](#the-update-record).
- `last-run.log`: the output of every update command and of the build, with
  Gantry's own warning and error lines, replaced each run. It is where to
  look when a run fails; the record holds no output.
- `current`: the live generation's id and nothing else (no path, no
  newline).
- `generations/<id>`: one folder per generation, each a complete output
  folder (`index.html`, `sites/`, `week.html`, `strategic.html`,
  `week-plan.json`, `build.json`). An id is the run's UTC time and a random
  suffix, such as `20261119T124503Z-0a1b2c3d`, made only of ASCII letters,
  digits and `-`, and never reused. The live generation and the one before
  it are kept.

## The update record

`update.json` is written through a temporary file and a rename, twice per
run: before the build, with this run's update and the previous record's
`last_build`, then after the swap or the failed build, with this run's
`last_build`. Every time is ISO 8601 with its UTC offset. The record holds
no absolute path and no command output. An example, after a run whose pull
failed but whose build was swapped in:

```json
{
  "record_version": 1,
  "attempted_at": "2026-11-19T12:45:03+00:00",
  "ok": false,
  "steps_run": 2,
  "failed_step": {
    "index": 2,
    "program": "git",
    "reason": "exit 1"
  },
  "last_ok_at": "2026-11-19T12:30:02+00:00",
  "interval_minutes": 15,
  "last_build": {
    "at": "2026-11-19T12:45:09+00:00",
    "ok": true,
    "exit": 0,
    "swap": "swapped"
  }
}
```

- `record_version`: the record's format version, `1`.
- `attempted_at`: when this run's update started.
- `ok`: whether every update command exited 0 (an empty `update` list is a
  successful update of zero steps).
- `steps_run`: how many commands were tried, the failing one included.
- `failed_step`: `null` when `ok`; else `index`, the failing command's
  position from 1, `program`, the file name of its first word, and
  `reason`: `exit N` (any integer: a POSIX process killed by a signal
  exits negative, and a Windows code can reach ten digits), `timed out` or
  `could not start`.
- `last_ok_at`: when the data was last updated: this run's `attempted_at`
  on success, else the previous record's, else `null`.
- `interval_minutes`: the configured interval, for a reader judging how
  fresh the data is.
- `last_build`: `null` before any build; else `at`, when the build ended,
  `ok`, true only for a build that exited 0 and was swapped in, `exit`, its
  exit code (`null` when it timed out or could not start), and `swap`:
  `swapped`, `swap failed` or `not swapped`.

The first record of the first run, when its update failed, has its empty
forms:

```json
{
  "record_version": 1,
  "attempted_at": "2026-11-19T12:45:03+00:00",
  "ok": false,
  "steps_run": 1,
  "failed_step": {
    "index": 1,
    "program": "git",
    "reason": "could not start"
  },
  "last_ok_at": null,
  "interval_minutes": 15,
  "last_build": null
}
```

A reader (#34's daily page; `gantry.refresh.read_update_record(roots, path)`)
gets one of four statuses, and never an exception:

- `ok`: a well-formed version-1 record;
- `missing`: there is no `update.json` yet;
- `unreadable`: the file cannot be read, lies outside the run's roots, is
  not strict JSON (duplicate keys, `NaN`, invalid UTF-8 and the like are
  refused), or is not a well-formed version-1 record;
- `unknown_version`: an integer `record_version` other than 1, checked
  first, so a newer record is never misread as a bad one.

A run that finds the previous record `unreadable` or `unknown_version`
prints `gantry: warning: previous update.json is <status>: <reason>;
last_ok_at starts again` and goes on.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | the update succeeded, the build exited 0 and its generation was swapped in; or the run was skipped because another run holds the lock |
| 1 | anything else: a config error (no `[refresh] dir` included), a failed update (even though a generation naming the failure was swapped in, so a scheduler shows the run as failed), a build that failed or exited 1, a failed swap, or a failure of the job's own files |
| 2 | a usage error, such as a missing `--config` |

Each of these failures prints one line on stderr, also written to
`last-run.log`:
`gantry: error: update step <index> (<program>): <reason>`,
`gantry: error: build failed: <why>`,
`gantry: error: build exited 1; its generation was swapped in`, or
`gantry: error: swap failed: current could not be replaced in 2 s`.

## Scheduling

Install Gantry for the job from a clone checked out at a release tag, into
a virtual environment of its own, so that a later checkout of the clone
does not change the running job (`<python>` is that environment's Python,
by its full path; `<config>` is the installation config, by its full path):

```
git -C <clone> checkout gantry--v<version>
<python> -m pip install <clone>
```

Then have the OS scheduler run the job every `interval_minutes`. Quote a
path that holds a space, as the examples do. The job must also run after a
reboot with no one logged in, so on Linux run `loginctl enable-linger` once
for the job's user (a user timer otherwise runs only while that user has a
session), and on Windows create the task with `/RU <user>` and that user's
password (`/RP`), so it runs whether or not the user is logged on.

On Linux, a systemd user service and timer (in the user unit folder, then
`systemctl --user enable --now gantry-refresh.timer`). `gantry-refresh.service`:

```ini
[Unit]
Description=Gantry refresh

[Service]
Type=oneshot
ExecStart="<python>" -P -m gantry refresh --config "<config>"
```

`gantry-refresh.timer`, for an `interval_minutes` of 15:

```ini
[Unit]
Description=Run Gantry refresh every 15 minutes

[Timer]
OnCalendar=*:0/15

[Install]
WantedBy=timers.target
```

On Windows, a Task Scheduler task, for an `interval_minutes` of 15:

```
schtasks /Create /TN "Gantry refresh" /SC MINUTE /MO 15 /TR "\"<python>\" -P -m gantry refresh --config \"<config>\""
```

A run the scheduler starts while the last one is still going is skipped
(exit 0), so a slow run is never doubled.

## The update list

`[refresh] update` is the list of commands that bring the data root up to
date. Each command is a list of words, run without a shell, so no word is
expanded or split. On Windows a `.bat` or `.cmd` program may still be run
through `cmd.exe`, which parses its words by shell rules, so name an
`.exe` there. Typically it is one pull per repository the data root
needs, the planning repository and the fires list's included:

```toml
update = [
  ["git", "-C", "<data clone>", "pull", "--ff-only"],
  ["git", "-C", "<fires clone>", "pull", "--ff-only"],
]
```

The build reads devagent's portfolio JSON (#12), so devagent's aggregator
goes last, after the pulls, and a failed aggregator run is a failed update
(the build still runs, on the document already there, and its pages show
that document's data time):

```toml
update = [
  ["git", "-C", "<data clone>", "pull", "--ff-only"],
  ["bash", "<devagent checkout>/scripts/portfolio.sh"],
]
```

Name the devagent checkout's `scripts/portfolio.sh` by its absolute path.
On Windows, where devagent's toolchain does not run, make the step run the
aggregator under WSL (for example `["wsl.exe", "bash", "<checkout in WSL>/scripts/portfolio.sh"]`, with `wsl.exe` named by its full path) and point `portfolio_json`
at the document it writes, as [config.md](config.md#supported-platforms)
says.
devagent's plugin cache path carries its version, so re-point the step after
each devagent update. The aggregator writes `<devagent home>/portfolio/portfolio.json`
(the home is `DA_HOME`, by default `.claude/devagent` in the home folder); the installation
config's `portfolio_json` names that file. It resolves a relative
`devdoc_dir` against the folder it runs in, the installation config's
folder, so give every `devdoc_dir` absolutely
([config.md](config.md#sites)).

- **Nothing commits to these copies on the build machine.** Then a pull can
  always fast-forward; one that cannot (`--ff-only` refuses it) is a failed
  update, and is shown as one.
- **Clone in full, with no `--depth`, and keep pulling that way.** The build
  reads each site's `WBS.md` history, and reports shallow history as no data
  ([the history rules](wbs-history.md)).
- **A bare program name is looked up only in the absolute entries of
  `PATH`**, never in the current folder or a relative `PATH` entry. A
  scheduler's `PATH` is short, so name each program by its full path when
  it is not on that `PATH`; a program that cannot be found is
  `could not start`. Name a program by a bare name or its full path, never
  a relative path such as `bin/pull`: Linux reads that from the
  installation config's folder, Windows from the job's own working folder.

## First-day failures

- **The pull's credentials must work under the scheduler**, which runs with
  no login session, no SSH agent and no desktop credential prompt. Use a
  credential the job's user can read unattended, such as a deploy key or a
  stored token, and run the job once by hand from a plain shell to check.
- **The build machine's devagent config must name planning directories that
  exist on that machine**: the clones the update list pulls, at the paths
  that config names.
- **The installation config and the devagent config must not arrive through
  the update list.** A bad push to either would stop the job before it
  records anything; keep them as files on the build machine.
- **A stale `.git/index.lock`.** A pull that is stopped (a timeout, a
  reboot) can leave `.git/index.lock` in its clone. Every later pull then
  fetches and fails, so every update fails, and `last-run.log` names
  `index.lock`. To clear it, check that no git process is running in that
  clone, delete `.git/index.lock`, and let the next run pull.
- **Trust.** Whoever can write the installation config's folder,
  `[refresh] dir` or the scheduler's start-in folder can make the job run
  programs as its user: keep those folders writable only by that user.
