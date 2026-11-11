# WBS.md history

`gantry/wbs_history.py` answers three questions for a site from its planning
repository's git history (#28):

- `last_state_change`: the last date a leaf line's glyph changed in a window
  of weeks, and whether the site's working-tree `WBS.md` matches its
  committed version;
- `committed_text`: a committed file's text at `HEAD`;
- `history_repository`: which repository answers, the one rule every read
  goes through.

No page shows these answers yet: #29 (Derive the five time thieves) reads
them. Until #29 binds them, the signatures below are provisional. When git
cannot answer, the result is **no data** with a reason, never an error that
stops the build.

The reader never builds on the stopgap parser (`gantry/wbs_stopgap.py`), and
never on any WBS tree parser: it is a line scanner over committed versions,
so #12 can delete the stopgap without waiting on it.

## Leaf lines

Each committed version is decoded as UTF-8 with replacement, so an invalid
byte never fails the build, and split into lines where Python's
`str.splitlines` splits them, so a line-ending change (CRLF to LF) is never
a change. The scanner then follows devagent's node rule
(`scripts/lib/wbs-parser.py` at `8d729b5`, the same as at `8a821d5`):

- **Node lines.** A line is a node line when it matches devagent's node
  pattern, `^(\s*)- \[([ x\-!~?P])\]\s+(.+)$`, outside a fenced block.
- **Fences.** A fence opener is a line of three or more backticks or tildes
  (after spaces or tabs), with any info string; a backtick opener whose info
  string holds a backtick is not one. Its closer is the first later fence
  line of the same character, at least as long, with no info string. The
  opener, the closer and the lines between are skipped. An opener with no
  closer is not a fence: the lines after it are still read.
- **Depth.** A node line's depth is its indent, with each tab expanded to
  two columns, divided by two (rounded down), plus one.
- **Leaf lines.** A node line is a leaf line when the next node line's
  depth is not greater than its own, or there is no next node line. That is
  exactly when devagent's nesting rule gives it no children, so no tree is
  built.
- **Identity.** A leaf line's identity is the `issue:` value of its
  trailing `{...}` meta block, read as devagent reads it (one layer of
  quotes removed; a repeated key keeps the last), when that is a non-empty
  string; otherwise the line's text with its meta block removed. No other
  key is read. An identity that repeats is numbered by its occurrence among
  the node lines in file order, so two lines with the same `issue:`, or two
  issue-less lines with the same text, stay two identities. An `issue:`
  identity and a text identity never collide.

**What counts as a change.** Between a commit's version and its first
parent's version, a state change is an identity that is a leaf line in both
with a different glyph. A line added or removed is not a state change; a
line that is a leaf in one version and a parent in the other is not
compared; an issue-less line whose text changes reads as one line removed
and one added.

**Differences from devagent's node rule.** None at `8d729b5`, with one
noted: a node line inside an HTML comment is read as a node line, as
devagent reads it today, until mtibbits/devagent#775 lands and the scanner
follows it.

## The walk

`last_state_change(roots, site, config, as_of, weeks, cache)` reads the
history of `<history directory>/WBS.md`:

- **From `HEAD`, first parent only.** The walk starts at the repository's
  `HEAD`, as the refresh job left it, and follows the first-parent chain of
  commits that touch the file, newest first (`git rev-list --first-parent`).
  Each commit read is compared with its first parent's version, so a merge
  is compared with its first parent and a change made on a side branch
  counts at the merge; versions on side branches are never compared with
  each other.
- **The date rule.** A commit's date is the calendar date of its committer
  time in the commit's own recorded offset (`%ci`), so every machine and CI
  runner agrees, and a pinned as-of date gives the same answer on any day.
- **The window, and where the walk stops.** The window is the `weeks`
  weeks ending on the as-of date: its first day is the as-of date minus
  7 × `weeks` + 1 days. A commit dated after the as-of date is skipped
  without ending the walk, since commit dates need not follow chain order.
  The walk stops after the first commit dated on or before the day before
  the window, so that commit and its first parent's version are the last
  read.
- **The answer.** `date` is the latest commit date among the state changes
  the walk found (inside the window, or in the last commit read, which is
  dated before the window), whatever their order on the chain, or none.
  `commits_read` counts the commits read.
- **History that begins inside the window.** When the walk reaches the
  chain's oldest commit without stopping (its first parent has no
  `WBS.md`, as for a new or moved file) and no state change was found, the
  answer is that commit's date with `history_start` set, so a caller can
  tell a young file from a still one.
- **Read once per build.** A `HistoryCache` lasts one build. Within it each
  `(repository, commit, path)` version is resolved once and each blob is
  read and scanned once, and sites that share a repository share its reads.
  A read that fails is not kept, so the next site tries it again and its
  reason names that site. What bounds each build's reads is the window.

## Committed text

`committed_text(repo, rel_path)` takes what `history_repository` returned for
the site (`HistoryResult.repo`, a `Repo` or a `NoData`) and a path relative to
the site's history directory, in `/` form (for example
`Issue-7/checklist.md`):

- **Pass-through.** Given a `NoData`, it returns that same `NoData`, with no
  git call, so its reason reaches the caller.
- Given a `Repo`, it returns the file's text at that repository's `HEAD`,
  decoded with replacement.
- **`Absent`** when the path is not in that commit (a file that exists only
  in the working tree included), or when `rel_path` is empty, absolute
  under either operating system's rules, holds a `..` segment, a newline or
  a NUL, since it can then name no file in the history directory. git's
  "not there" is exit 1 with no `fatal:` line; a warning such as a stale
  alternates entry does not turn it into a failure.
- **`NoData`** only when the git call itself fails (`git failed: ...`). It
  reads one commit, so shallow history never affects it.

`last_state_change` returns the `Repo | NoData` it used as
`HistoryResult.repo`, so a caller passes it straight on.

## The uncommitted check

`HistoryResult.uncommitted` says whether the site's working-tree `WBS.md` (in
its planning directory, the file the build reads) differs from its committed
version. Git hashes the file as a blob with
`git hash-object --path=<history directory>/WBS.md -- <file>` in the site's
repository and compares the id with the blob at `HEAD:<history
directory>/WBS.md`. It writes nothing (no `-w`, no index refresh) and applies
the repository's attributes and line-ending rules (never `--no-filters`), so
a checkout's CRLF copy of an LF blob is not a change where those rules
convert. Git, not Gantry, opens the working-tree file: the reader opens no
working-tree `WBS.md`. The answer is true, false, or, in any no-data case,
not checked (`None`), with the reason in `uncommitted_reason`.

One known limit: a blob committed with CRLF line endings, in a repository
where `core.autocrlf` is `true`, reads as uncommitted even when the
working-tree file is byte-identical to it. `hash-object --path` converts
CRLF to LF there, while `git status` skips that conversion for a file whose
committed version already holds CRLF. Committing the file once with LF line
endings clears it. A test pins this behaviour.

**The line-ending pins.** Both sides are pinned for the sample history. The
gantry repository's `.gitattributes` holds `samples/**/*.md text eol=lf` (and
the same for `*.toml` and `*.json`), so every checkout, Windows included,
holds LF sample files whatever its `core.autocrlf`; and the sample history
tool writes `* text eol=lf` to the generated repository's `info/attributes`,
so `hash-object --path` there equates a CRLF copy with its LF blob.

## Isolated git

Every git call goes through one helper, `run_git`:

- an argument list, never a shell, with no input;
- plumbing only, each with an allow-list of options: `rev-parse`,
  `rev-list`, `cat-file blob` and `hash-object` (never `-w`,
  `--no-filters`, `--batch`, `--textconv` or `--filters`), so no pager or
  `textconv` applies; anything else is refused with an error;
- every inherited `GIT_*` environment variable removed, so a hook's
  `GIT_DIR` cannot redirect a read, and only `GIT_CEILING_DIRECTORIES`,
  `GIT_NO_LAZY_FETCH=1` and `GIT_TERMINAL_PROMPT=0` set back, with
  `LC_ALL=C` so git's messages are not translated;
- run in the history directory to find the repository, then in its top
  level, with every path relative to that top level: `rev-list` takes it
  as the pathspec `:(top,literal)<path>`, `rev-parse` as `<commit>:<path>`
  and `hash-object` as `--path=<path>`;
- a time limit per call.

The user's global git config is kept, so a `safe.directory` entry the
operator relies on still applies. That holds only for config git finds at
its usual places: config named through a `GIT_*` variable
(`GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`, `GIT_CONFIG_COUNT` and its
`GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>` pairs) is removed with the rest,
so a build machine that sets `safe.directory` that way reads "no data: git
failed: ..." for the repository.

## The repository rule

`history_repository(roots, path, gantry_root, *, key, site)` decides which
repository answers for a site, and every git read for the site, committed
checklists and the uncommitted check included, uses the repository it
returns.

- **The history directory** is the site's `[site.<name>] history` key when
  set ([config](config.md#keys)), else its planning directory. The
  directory is checked under the run's roots (`gantry/roots.py`) before any
  git call; git is handed only that checked path.
- **The repository** is the one git finds from that directory. Its top
  level may lie above the directory, as a shared planning repository's
  does; `Repo` carries the top level and the history directory relative to
  it.
- **`gantry_root`** is the resolved folder holding the running `gantry`
  package, found from the package's own file (`gantry.roots.gantry_root`),
  never by git discovery. Two guards hang on it, whether or not it is a git
  work tree, and keep the gantry repository's own `.git` from ever being
  read. Paths are compared resolved and case-normalised.
  - **A path below `gantry_root`: in no repository.** Discovery runs with
    `GIT_CEILING_DIRECTORIES` set to `gantry_root`, so a search starting
    below it never climbs into it: the site reads "no data: in no
    repository" (the history-less sample site does, on every machine).
  - **A path at `gantry_root` or above it: refused.** The history
    directory is refused before any git call when it is `gantry_root` or
    an ancestor of it, and after discovery when the repository's top level
    is: either way the site reads "no data: history would come from
    Gantry's own repository".

## No data

Each reason, as the result's text gives it (`no data: <reason>`):

- `git not found`: `git` is not on the path;
- `history directory not found`: the history directory does not exist, or
  is not a directory;
- `the site has no planning directory`: the site has no planning directory
  and no `history` key;
- `history directory is outside the configured roots`: the roots refusal;
- `cannot check the history directory: <reason>`: the directory cannot be
  checked (for example a path with an embedded NUL);
- `in no repository`: no repository holds the history directory below the
  ceiling;
- `history would come from Gantry's own repository`: the repository rule's
  refusal;
- `` `WBS.md` has no commits on or before <as-of> ``: no commit on the
  chain is dated on or before the as-of date, as for a `WBS.md` never
  committed, a history directory with no `WBS.md`, or a file whose every
  commit is after the as-of date;
- `shallow history`: the repository is shallow
  (`git rev-parse --is-shallow-repository`) and its history does not reach
  the version before the last commit read;
- `git failed: <first line of git's message>`: any other git call failed,
  with every absolute path in the message replaced by the site's name.

The uncommitted check alone can also read ``cannot check the working-tree
`WBS.md`: <reason>``, or the roots refusal for the working-tree file. No case
raises.

## The sample history

The tests and the demo read a sample history built at run time, never
committed:

```
python tools/sample_history.py [--samples DIR] [--out DIR]
```

It reads `samples/history/plan.toml`, which lists per sample site the dates
of earlier versions of its `WBS.md`, each kept as
`samples/history/<site>/<date>.md`. It commits each, then the site's current
`samples/<site>/WBS.md` at the plan's `last_date`, into a fresh repository at
`samples/history/out/`, so the history always ends at what the build reads.
The CI test workflow runs it before pytest.

- Every input is normalised to LF; every commit has a fixed author and
  author and committer dates pinned at midday UTC; the repository is
  `sha1`. Every inherited `GIT_*` variable is removed and only an empty
  global config file, an empty template folder (with `GIT_CONFIG_NOSYSTEM`)
  and the identity and dates are set; hooks, signing and line-ending
  conversion are off on each command line. So the commit ids are the same
  on every run and every checkout.
- It writes only under `samples/history/out/`, which git ignores and which
  lies outside the build's output folder. It deletes and rebuilds that
  folder each run, and refuses (exit 1) an output path outside the sample
  directory, one that holds a sample input, a non-empty folder it did not
  make, and a plan whose `last_date` is after the sample config's `as_of`.
  Exit 0 prints the new `HEAD` id.
- The sample config's `history` keys point harbor (a glyph change inside
  the four-week window), orchard (its last change older than the window)
  and lighthouse (history that begins inside the window) into it; quarry
  has a `WBS.md` and no `history` key, so it reads "no data: in no
  repository" ([the roles](../samples/README.md)).

**A change to a sample `WBS.md` reviews `samples/history/` in the same
change.** The newest version of each site's history is its current
`WBS.md`, so a glyph edit there is a state change at the plan's
`last_date`, and can change the site's role.
