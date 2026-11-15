# Security policy

Gantry is a Python standard-library program, a Claude Code plugin command
and a launcher script. It reads an operator's private planning records and
writes static HTML pages from them. It holds no credentials and, with a
supported git (see the README's Support section), makes no network
connection. The data it guards is the operator's own: what is in
their records, and which projects they run. That is the surface this policy
covers.

## Supported versions

Only the latest release is supported. Releases are semver tags cut from
`main` (`claude plugin marketplace update gantry`, then
`claude plugin update gantry@gantry`, picks up the next one). There are no
release branches, and fixes are not backported. A report against an older
release or commit is welcome, but the fix lands on `main` and ships in the
next release, and the remedy is to update.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: open the repository's
**Security** tab and choose **Report a vulnerability**. Only you and the
maintainer see the report until its advisory is published, and GitHub
notifies the maintainer directly.

Please do not open a public issue for anything exploitable. Do not put real
planning records, configs, pages, project names or other private data in a
report. A description of the path (which input, which command, what it
reaches) is enough, and a reproduction built from made-up data, like the
samples under `samples/`, is ideal.

There is no security email address. If the Security tab's report button is
not available to you, open an issue that says only "security report, please
enable private reporting", and the maintainer will make contact through
GitHub.

## What is in scope

- **Record text becoming markup.** Anything that lets text from a record
  (a `WBS.md` line, a per-issue record, a fires-list entry, a requirements
  file or the portfolio file) run as script or markup in a page Gantry
  writes, or lets such a page load anything from outside itself
  ([docs/page-safety.md](docs/page-safety.md)).
- **File access outside the configured roots.** Anything that lets a config
  value, a link or a record make Gantry read or write outside the roots an
  installation config names, or read a git repository the config does not
  point it at ([docs/config.md](docs/config.md#where-a-build-may-read-and-write)).
- **Private data reaching the public.** Anything that lets real data into
  this repository or its Pages site: the demo build reading outside the
  samples, or a gap in the commit check
  ([docs/commit-check.md](docs/commit-check.md)). Finding personal data
  already in the repository or on the Pages site counts too: report it
  privately, and do not quote it in a public issue.
- **Data crossing installations.** Anything that makes one installation's
  pages show another installation's data.
- **The plugin running the wrong code.** Anything that makes `/gantry:build`
  run a `gantry` package other than the installed plugin's, or widens the
  `allowed-tools` grant in `commands/build.md`.
- **Network use.** Anything that makes a Gantry command open a network
  connection when it runs with supported tools.

## What is out of scope

- Claude Code itself, the Claude model and the plugin marketplace
  mechanism. Report those to Anthropic through its own security channel.
- devagent, whose files Gantry reads. It has its own security policy.
- Python, git, browsers, GitHub and GitHub Pages.
- How an operator serves their own pages, and choices they make in their
  own installation config, such as pointing a root at a shared folder.
- What an operator's own records say.

## What to expect

Gantry has one maintainer. A report is acknowledged within seven days.
After that, the fix is best effort, ranked by whether the issue can expose
private data or run code. The fix ships in a release, with a GitHub
security advisory and a `Security` entry in [CHANGELOG.md](CHANGELOG.md).
You will be told when the fix is released and, if you want, credited in
the advisory.

## Good-faith research

Testing against your own installation and made-up data is welcome and will
not be treated as a violation. Please do not test against other people's
installations, pages or repositories.
