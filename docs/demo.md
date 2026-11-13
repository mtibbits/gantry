# The demo and its Pages deploy

Gantry's public demo is the set of pages `gantry build` writes from the
fictional sample installation under `samples/`. CI builds it, checks it
against an expected list, and deploys it to GitHub Pages under `/demo/`,
with a generated holding page at the site root. This page states how the demo
is made, what the check holds it to, and how the deploy is guarded (#45).

## What the demo is

- **Samples only.** Every input is under `samples/`: the sample config
  `samples/gantry.sample.toml`, the sample devagent config and planning
  folders, and the sample WBS.md history. The demo is never built from a real
  installation, and a copy of the samples anywhere else is not sample mode
  (docs/config.md).
- **A pinned date.** The sample config's `as_of` fixes the planning date, and
  in sample mode every page's stamp reads `sample data as of <as_of>` with no
  build time. Two builds on different days write the same bytes.

## How it is made

    python tools/demo_build.py

The tool deletes and recreates the demo output folder (`output_dir` of the
sample config, `samples/out`), then runs its steps in order:

1. **sample history**: `tools/sample_history.py`, which rebuilds the sample
   WBS.md history repository under `samples/history/out` (docs/wbs-history.md);
2. **build**: `gantry build --config samples/gantry.sample.toml`, in process.

The steps are the tuple `STEPS` in `tools/demo_build.py`. An issue that adds a
preparation step (#12's portfolio steps, say) inserts it into `STEPS` before
`build`, and its own tests cover it. The issue asks that a step needing a
program beyond Python and git check the program is on the path and stop the
run naming it. Neither step here needs one, so no such check exists yet: the
change that adds the first such step (#12's) adds the check with it.

The clone tests build from a depth-1 clone of the committed `HEAD`, so they
fail, asking for a commit, while `tools/`, `gantry/` or `samples/` holds an
uncommitted edit or an untracked file: about 40 tests in
`tests/test_demo_build.py` and `tests/test_check_demo.py`. While iterating on
uncommitted work there, leave them out with
`--ignore tests/test_demo_build.py --ignore tests/test_check_demo.py`, then
commit and run the whole suite.

**The clean guard.** Before deleting anything the tool refuses, with one
`demo_build: <reason>` line and exit 1, when the folder is not strictly inside
`samples/`, when the folder or any folder between `samples/` and it is a
symlink or a junction, when git does not ignore the folder (`.gitignore` and
the clone's `.git/info/exclude` count, a personal global excludes file does
not), when git tracks a file in it (one added with `git add -f`), or when the
tool's own repository root (the folder above `tools/`, whatever the working
folder) is not the top level of a git work tree. Paths are judged as written, never
through a link. A failed step stops the run: `step <name> failed (exit <n>)`,
or the exception's type and its traceback.

**The last line.** On success the last line of output is the folder, relative
to the repository root in POSIX form with an LF line end (`samples/out`). CI
reads it from there; no other line is meant for a machine.

## How it is checked

    python tools/check_demo.py samples/out

The check reads the folder and prints every finding as
`check_demo: <file>: <problem>`, file relative to the folder, then a count,
and exits 1; or it prints one `ok:` line and exits 0. It never stops at the
first finding. Its rules:

- **The folder holds pages.** A missing folder, or one with no `.html` file,
  is a finding.
- **Every page is reachable.** Every page except `index.html` is linked from
  `index.html`.
- **Every link lands.** Each `a[href]` on each page, HTML or SVG, names a file
  in the folder (exact case, a file and not a folder) and, with a fragment, an
  element with that `id` in it; a bare `#x` names an `id` on the same page; no
  link leaves the folder. A link with a scheme, or one starting with `/`, is
  the external-resource rule's.
- **The sample sites.** Each sample site the build can read has its page
  `sites/<name>.html`; a NOT_BUILT site (one the build reports and skips) has
  no page, and the index names it in a list item without a link; no page is
  named for a site the samples do not have.
- **The expected list** (below): every `required` entry is present, every
  `pending` entry absent, and every page other than `index.html` and the
  site pages, and every file that is not a page, is named by an entry.
- **The stamp.** Each page has exactly one element whose class list holds
  `stamp`, and that element's own text (its child elements' text left out) is
  `sample data as of <as_of>`. No page carries a build time: no
  `built <date> <time> UTC; data as of` text and no `data-built-at` attribute.
- **Nothing loads from outside.** Each page passes the page-safety
  external-resource check (docs/page-safety.md).
- **The page rules.** Each page passes the page-safety page rules (allowed
  tags and attributes, one typed embedded-data block).
- **The hostile title shows as text.** The sample site `lighthouse` plans a
  leaf whose title holds markup (`Render <b>this</b> & "that" as text`). Each
  page whose visible text shows it, whole or cut short with an ellipsis,
  passes the page rules with that title planted in its embedded data; no page
  source holds its markup unescaped (`<b>this</b>`); and the lighthouse page
  shows it, so the rule cannot pass over nothing.

**What a static check cannot see.** It reads the files as written. Links and
text a page script builds at run time, the page in a browser, and the
deployed site are outside it: the page-script harness (#9) and the
post-merge check below cover those.

## The expected list

`EXPECTED` in `tools/check_demo.py` names every file or element the demo
should hold, with the issue that owns it:

| File or element | Issue | State |
| --- | --- | --- |
| `index.html` | #11 | `required` |
| `sites/*.html` | #11 | `required` |
| `sites/lighthouse.html`, element `trace` | #20 | `required` |
| `strategic.html` | #23 | `pending` |
| `daily.html` | #34 | `pending` |
| `week.html` | #38 | `pending` |
| `week-plan.json` | #61 | `required` |

`required` means the build at this commit writes it: missing is a finding.
`pending` means its issue is open: present is a finding, so a view cannot
appear in the demo unannounced. **How a view joins:** the change that makes
the build write it flips its entry to `required` (or adds the entry) in the
same commit, and edits this table and the test that pins it.

## The Pages artifact

    python tools/pages_artifact.py samples/out <stage folder> --repo-url <url>

The tool copies the demo to `<stage>/demo/` and writes `<stage>/index.html`,
a holding page that says the Gantry website is coming and links the demo
(`demo/`) and the source repository. The holding page is generated in CI and
never committed (commit check rule 1). It opens with the charset meta, then
the page CSP, as every Gantry page does. The stage folder must be absent or
empty, and `--repo-url` must be an https repository URL.

What comes later has owners: the website replaces the holding page at the
root (#89), the demo gains an "About Gantry" link (#90), and the sample
portfolio steps join `STEPS` (#12).

## The workflow

`.github/workflows/pages.yml`, its shape pinned by
`tests/test_pages_workflow.py`:

- **Triggers:** a push to `main` and every pull request. Never
  `pull_request_target`.
- **build** (every run, `contents: read`): check out the commit, build the
  demo, check it, stage the artifact, and check the staged copy of the demo.
  Only on a push to `main` does it upload the artifact.
- **deploy** (a push to `main` only, after build): `contents: read`,
  `pages: write`, `id-token: write`, in the `github-pages` environment. It
  reads the repository's Pages setting, stops unless the source is "GitHub
  Actions", then deploys.
- **Concurrency:** one workflow-level group per ref,
  `pages-${{ github.ref }}`, with `cancel-in-progress: false`. Pushes to
  `main` run in push order, so the later commit's deploy wins; a pull request
  waits only behind its own ref. While one run is going, a newer pending run
  replaces an older pending one (GitHub's documented behaviour, read
  2026-11-12); the newest commit still deploys.
- **Pinned actions:** every `uses:` names a full commit SHA with its release
  in a comment.
- **No bypass:** no `continue-on-error` anywhere, no `if` on the build job or
  on any step but the upload, and the deploy job's steps are exactly
  checkout, Python, the settings read, its check, and the deploy.
- **Exactly this file:** the test also compares the whole parsed workflow
  with one expected tree (`EXPECTED_TREE`; action SHAs compare by form). Any
  key, step, `run` line, `shell`, `env` or `working-directory` the tree does
  not hold is a finding, so changing the workflow means changing that tree in
  the same commit. The test checks the file; the `github-pages` environment's
  `main`-only branch policy and review of `main` are what stop a changed
  workflow from deploying.

A pull request's run therefore builds, checks and stages, and uploads and
deploys nothing.

## The one-time setting

Settings, Pages, Source: **GitHub Actions**. An operator sets it once; the
workflow never changes it (it uses no `configure-pages` step, which could).
Read on 2026-11-12, the repository's setting was already "GitHub Actions"
(`build_type: workflow`) and the `github-pages` environment existed, with a
deployment branch policy of `main` only. The deploy also leans on that policy:
a pull request that edits this workflow still cannot deploy from its branch.

The deploy job reads the setting through `GET repos/<owner>/<repo>/pages` and
`python tools/check_demo.py --pages-source <file>`, which passes only on
`build_type` `"workflow"` and prints nothing else of the response. A legacy
"deploy from a branch" source, a missing Pages site or an unreadable response
stops the deploy.

The stated residual: were the source switched to a branch, GitHub would serve
that branch's committed files at once, without this workflow. Only committed
files could be served, and the commit check keeps generated pages out of the
repository.

## After merge

The first push to `main` is the deploy's first live run. Its check is owned
by the issue's impact step: the deploy job reads `build_type: workflow` and
deploys; `<Pages URL>/demo/` serves the index and the four sample site
pages; the root serves the holding page and both its links work. A failed
first deploy reopens #45 with the run's link.

**Taking the site down.** Reverting this workflow stops new deploys but does
not unpublish: the last deployment stays public. To take it down, unpublish
the site under Settings, Pages (or `gh api -X DELETE repos/<owner>/<repo>/pages`).
