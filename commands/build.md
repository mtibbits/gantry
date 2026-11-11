---
description: Build every worksite page a Gantry installation config names, and the index that links them
argument-hint: "<installation config>"
allowed-tools: Bash(bash "${CLAUDE_PLUGIN_ROOT}/tools/gantry-build.sh"), Bash(bash "${CLAUDE_PLUGIN_ROOT}/tools/gantry-build.sh" *)
---

# /gantry:build

**Usage:** `/gantry:build <installation config>`

Loads the installation config, checks it, and builds every site that
devagent's config names: each site's worksite page under
`<output_dir>/sites/`, then `<output_dir>/index.html`, which links them. It
prints one absolute path per file written, then one `trace <site>:` line
per written page whose site is in the trace or whose trace report has an
item. A site that cannot be built is
listed on the index and on stderr with its reason, and does not stop the
others. Building one site's page alone is `gantry build --site`, which this
command does not pass.

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
unchanged: exit 0 is success, the index and at least one site page were
written (sites not built and warnings do not change it); 1 is a config
error, no site page was written, or the index or the output folder cannot
be written;
2 is a usage error.
