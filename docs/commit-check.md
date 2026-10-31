# Commit check against private data

This repository is public, so a push is public the moment it lands. A real
page, a copied installation config or a local path in a test can reach the
public through one careless `git add -f`, and `.gitignore` does not stop that.
`tools/commit_check.py` guards what gets committed. It has two halves, because
CI has no private data:

- a **committed check** that needs no private data. CI runs it on every push
  and pull request, and the local hook runs it before each commit;
- an **optional local hook** that also counts names and hostnames from the
  operator's private **denylist**, which is never committed.

The tool uses the standard library only. It reads content from git objects
(the index, a commit or `HEAD`), never from the work tree, so line endings and
unstaged edits cannot change a verdict.

## Rules

Rules 1 to 4 need no private data. CI and the hook both run them.

1. **Generated output, anywhere in the tree.** Refused: an `.html` file outside
   `gantry/templates/` and `tests/page_script/known_bad/`; a file named
   `week-plan.json`, `build.json` or `update.json`; any file under a folder
   named `out`. Why: a page or data file built from real data holds private
   project content. None is committed, not even from samples or fixtures.
2. **Installation-config files.** Refused: a `.toml` file that parses and has a
   top-level `config_version` together with `devagent_config` or `output_dir`,
   unless it is under `samples/`, or under `tests/fixtures/` as a
   self-contained fixture (see below). Any file named `*.local.toml` is refused
   everywhere. Why: a real installation config names private folders.
3. **Absolute local paths**, in every tracked text file and in commit messages:
   a drive-letter path (`<drive>:\…` or with a forward slash), a UNC path
   (`\\<server>\<share>`), a rooted path under a home, Users, root or mnt
   folder (`<home>/...`), an MSYS drive root (`/<letter>/`), and a tilde
   followed by a slash or by `~<name>` and a slash. Why: a local path names a
   machine, a user and often a private project. A drive letter must not follow
   a letter or digit; every other form must start a line or follow a character
   that is not a letter, digit, `_`, `.`, `-`, a slash, a tilde or a colon. So
   a form inside quotes, brackets or after `=` is found, while a one-letter
   segment inside a URL and a URL's tilde-user segment pass, and so does prose
   such as a tilde before a digit. A file that starts with a UTF-16
   byte-order mark is decoded as UTF-16; any other file holding a NUL byte is
   skipped as binary; undecodable bytes are read with replacement. Findings
   print the file, line and form, never the matched text, because Actions logs
   on a public repo are public.
4. **Ignore rules.** `git check-ignore --no-index` must ignore a probe file in
   the output folder `samples/gantry.sample.toml` names, in
   `samples/history/out/` and in `samples/devagent/out/`, and must not ignore
   `samples/gantry.sample.toml`, `samples/devagent/config.toml` or any tracked
   file. Only a tracked `.gitignore` counts: `.git/info/exclude` and
   `core.excludesFile` are local to one clone, so they can neither hide a
   missing rule nor fake one. Why: generated output must stay out of
   `git add`, and the probes need no folder to exist.

The form list is one constant, `PATH_FORMS`, in the tool;
`tests/test_source_scan.py` and `tests/test_samples.py` import it, so the three
checks never disagree.

## Modes

| Mode | What it checks | Used by |
|---|---|---|
| `tree` | every tracked file at `HEAD`, rule 4, and the exemption list's stale-entry check | CI, by hand |
| `range <base> <head>` | every file version each commit in the range adds or changes, and each commit's message | CI, by hand |
| `staged` | what the commit adds or changes (the index against `HEAD`), and rule 4 | pre-commit hook |
| `message <file>` | rule 3 on a commit message file | commit-msg hook |
| `ci` | `tree`, plus `range` over the commits the GitHub event names | the commit-check workflow |
| `names` | denylist names and hostnames, as a count | the local hook, by hand |
| `install-hook --denylist <file>` | writes the hooks | once per clone |

`range` diffs each commit against its first parent (a root commit against the
empty tree), never with a combined diff, so a file added and deleted in one
push is still found, and so is a merge commit's own content, conflict
resolutions included. It reads the exemption list at `<head>`.

`message` and `names --message` check what git will keep of a message file.
When git opened an editor (its "Please enter" comment block or the scissors
line that `commit -v` writes is present), git's default cleanup drops
everything from the scissors line and every line that starts with the comment
character (`#` unless `core.commentString` or `core.commentChar` says
otherwise), and so does the check. `git commit -m` and `-F` keep comment
lines, so a `#`-led subject is checked. `commit.cleanup` is honoured
(`strip`, `scissors`, `whitespace`, `verbatim`). A comment block in another
language reads as no editor, so every line is checked.

Exit codes: `0` clean, `1` a finding, `2` a usage, setup or install error.
Findings go to standard output, one per line, and the last line is
`commit-check: clean` or `commit-check: <N> findings`; errors go to standard
error.

### How `ci` picks its range

`ci` reads `GITHUB_EVENT_NAME` and the payload at `GITHUB_EVENT_PATH`, prints
the range it chose (`ci: range …`), and runs `tree` and the range:

- `pull_request`: the base commit to the head commit. If either is not in the
  clone, `ci` exits 2.
- `push`: `before..after`, except:
  - `after` all zeros (a branch deletion): no commits;
  - `before` all zeros (a new branch), or not in the clone (after a
    force-push): every commit reachable from `after` and not from
    `origin/<default branch>`, or the whole history when the push is to the
    default branch itself.
- any other event: exit 2.

The workflow (`.github/workflows/commit-check.yml`) runs on `push` and
`pull_request`, never `pull_request_target`, with `permissions: contents:
read`, and checks out with `fetch-depth: 0` so the range is present.

## The exemption list

`tools/commit_check_exempt.txt` holds one entry per line; `#` lines and blank
lines are comments:

```
<path> | absolute-path | <text> | <reason>
```

`<path>` is relative to the repo top, with forward slashes. `<text>` is a
literal the exempted line holds. A rule-3 hit is exempt only when it falls
inside an occurrence of that text, in that file, so a docs page exempted for
one example still fails on any other path, even on the same line. Only rule 3
can be exempted: rules 1 and 2 have no exemption by design, and rule 4 is a
repo-level rule.

It cannot grow stale: in `tree`, an entry whose file no longer exists, whose
text no longer holds a flagged form in that file, or that does not parse fails
the check. The entry's text is usually the flagged form itself, so rule 3 does
not scan the exemption list; that adds no exposure, since every entry's text
must occur in its exempted file. The list is empty today, and it never applies
to `names`.

**Review rule.** Changes to `tools/commit_check.py`, its exemption list or the
workflow need explicit review. CI and the hook run the checked-out copy of the
tool and its list, so a change that edits the check could pass its own leak.
When a range's own commits change any of the three, `ci` prints a GitHub
`::warning` annotation naming them. For a push to the default branch every
commit in the range is its own; elsewhere, commits already on the default
branch are not (merging it forward does not warn).

## Placeholders in docs and tests

Docs write a path form as a placeholder that does not trip rule 3, such as
`<home>/...`, `<drive>:\…`, `\\<server>\<share>`, `/<letter>/` or `~<name>`,
and name the bare tilde form in prose. Tests build every form at run time from
pieces (a tilde joined to a slash), so no test source holds one. The tool
assembles its own pattern literals the same way, so it passes its own rule.
Anything else goes on the exemption list, with its reason.

## Committed fixtures

A test fixture may be an installation config only when it is self-contained:
under `tests/fixtures/`, with every string value, in every table and array,
neither absolute nor starting with a tilde, and resolving inside the folder
that holds it. The tool does not know which keys are paths, so every string is
a candidate path; a value such as `"github"` resolves inside and passes. A
fixture never takes a generated-output name: tests build such files at test
time, and a fixture with that shape takes another name, such as
`update-ok.json`.

## The local hook: names and hostnames

Write the denylist as UTF-8 text, one name or hostname per line, with `#`
comments, and keep it **outside** this repo: the list would leak what it
protects. Then install the hooks in your clone:

```
python tools/commit_check.py install-hook --denylist <file outside the repo>
```

`install-hook` writes `pre-commit` and `commit-msg` hooks into the folder
`git rev-parse --git-path hooks` names (so a linked worktree works), records
the denylist's path as `git config --local gantry.denylist` and the running
interpreter as `git config --local gantry.python`. Neither key is ever
committed. It exits 2 and writes nothing when the denylist is inside any work
tree of the repo, when `core.hooksPath` is set (git would never run the hooks),
or when a hook it did not write exists; it marks its own hooks with a fixed
comment line, and re-installing over its own is allowed.

Each hook runs the CI rules (`staged`, or `message`), then `names` (`--staged`,
or `--message`). Without a recorded denylist the hooks run only the CI rules
and say so. The hooks live in the shared hooks folder, so they arm every
worktree of the clone, including a checkout from before this tool existed;
there the hook stops the commit, says the tool is missing, and names
`--no-verify`.

`names` matches each entry case-insensitively as a whole word, where a whole
word means not next to a letter: `_`, digits and `-` are boundaries. It
matches file paths as well as contents. Before matching it removes:

- `<owner>/<repo>#N` references, `github.com/...` URLs and the
  `Signed-off-by:` and `Co-Authored-By:` trailer lines (what #16's name search
  removes);
- this clone's own public identity, derived at run time from
  `git remote get-url origin`: `<owner>/<repo>` (bare, or in any URL) and
  `<owner>.github.io/<repo>`. Without a GitHub `origin` it removes no identity
  and says so. Any other `word/word` pair is not removed, so a name inside a
  relative path or in `<owner>/<other repo>` is counted;
- authorship metadata, the fields that must carry the author's name:
  `LICENSE`, `author` in `.claude-plugin/plugin.json`, `owner` and each plugin
  entry's `author` in `.claude-plugin/marketplace.json`, and `authors` in
  `pyproject.toml`. The list is one constant, `AUTHORSHIP_FIELDS`, in the
  tool; an issue that adds an authorship field extends it.

`names` reports a **count only**: `names: <N> matches`. `--where` adds each
match's file and line, never the matched text or the entry (a match inside a
printed path shows as `<match>`). Modes: `--staged`, `--message <file>`,
`--all` (every tracked file at `HEAD`, for an issue's closing record) and
`--text <file>` (a pull-request or issue body before it is posted).
`--denylist <file>` overrides the recorded path.

**Fail closed.** A recorded denylist that cannot be read (moved or deleted)
stops the commit and names `gantry.denylist`; an interpreter that no longer
runs stops it and names `gantry.python` and `install-hook`. Fix the setup
first. `git commit --no-verify` skips the hooks; it is the reviewed bypass,
for a commit you have checked another way, and CI still runs the CI rules.

## CI passing does not mean no private content

CI sees only what needs no private data. It does not catch:

- a private portfolio file, fires list, estimates file or real `WBS.md`
  copied in under an ordinary name: every CI rule passes it. Only the local
  hook's names list can catch a name inside one, and only where the operator
  installed it;
- names and hostnames at all: they are the hook's, and the denylist never
  reaches CI;
- a UTF-16 file without a byte-order mark: its NUL bytes make it read as
  binary, and binary files are skipped;
- anything the exemption list covers.

The rest is the reviewer's.

## Real-data runs write outside the tree

A **real-data run** is any run of Gantry or its tools on an installation's own
data. It writes only to a folder outside the repo tree, never into a clone,
and its pull request records counts only, never names, paths or content.

## Threat model

The check is aimed at accidental leaks by an honest committer: a `git add -f`,
a copied config, a pasted path, a private name. A committer who edits the
tool, its exemption list or the workflow is met only by the CI `::warning` and
the review rule above. CI cannot unpublish a push: it makes a leak visible at
once and keeps it out of `main` through review. Making the check a required
status check on `main` is the operator's setting.
