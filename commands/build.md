---
description: List the worksites a Gantry installation config names (pages come in later releases)
argument-hint: "<installation config>"
allowed-tools: Bash(bash "${CLAUDE_PLUGIN_ROOT}/tools/gantry-build.sh"), Bash(bash "${CLAUDE_PLUGIN_ROOT}/tools/gantry-build.sh" *)
---

# /gantry:build

**Usage:** `/gantry:build <installation config>`

Loads the installation config, checks it, and prints one line per site that
devagent's config names: the site, its planning directory, and whether a
`WBS.md` is there or why the site cannot be read. It writes nothing yet.

The argument the user gave, verbatim, is between the two marker lines:

-----BEGIN ARGUMENT-----
$ARGUMENTS
-----END ARGUMENT-----

## Run the launcher

<!-- Editor note: the launcher's grammar (gantry-build.sh [<config>]) is in
tools/gantry-build.sh, and docs/config.md documents it. -->

Make exactly one Bash tool call:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/tools/gantry-build.sh" '<ARG>'
```

`<ARG>` is the argument above, verbatim, as ONE single-quoted word: write
each `'` inside it as `'\''`, and change nothing else.

- Never pass the argument unquoted or inside double quotes: the shell must
  not expand anything in it.
- If the argument is empty, run the launcher with no argument:
  `bash "${CLAUDE_PLUGIN_ROOT}/tools/gantry-build.sh"`. Gantry then reports
  the missing `--config` itself.
- Do not `cd` first, so a relative path resolves against this session's
  working directory.
- Do not chain other commands before or after it.

Relay the output (stdout and stderr) and the exit status to the user
unchanged: exit 0 is success (site problems and warnings do not change it),
1 is a config or data error, 2 is a usage error.
