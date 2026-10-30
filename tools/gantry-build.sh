#!/usr/bin/env bash
# Run gantry from this plugin clone with no pip install (#7); see docs/config.md.
#
# Usage: gantry-build.sh [<config>]
#   0 arguments: runs `gantry build`, which reports the missing --config (exit 2).
#   1 argument:  runs `gantry build --config <config>`; a relative path resolves
#                against the caller's working directory.
#   more:        a usage error (exit 2).
#
# It runs `python`, or `python3` when there is no `python`, which must be
# Python 3.11 or later, with -P so a gantry folder in the working directory
# cannot shadow this clone's package. Bash builtins only: no dirname or other
# external command, so the tests can run it with a PATH of stand-in pythons.
set -euo pipefail

case $# in
  0) ;;
  1) set -- --config "$1" ;;
  *) printf 'usage: gantry-build.sh [<config>]\n' >&2; exit 2 ;;
esac

src="${BASH_SOURCE[0]}"
case "$src" in
  *[/\\]*) here="${src%[/\\]*}" ;;
  *) here=. ;;
esac
root="$(CDPATH= cd -- "$here/.." && pwd)" || root=""
if [ -z "$root" ] || [ ! -f "$root/gantry/__init__.py" ]; then
  printf 'gantry: cannot find the plugin root from %s\n' "$src" >&2
  exit 1
fi

if command -v python >/dev/null 2>&1; then py=python
elif command -v python3 >/dev/null 2>&1; then py=python3
else printf 'gantry: neither python nor python3 is on PATH\n' >&2; exit 127; fi

out="$("$py" -c 'import sys; print(sys.version.split()[0]); sys.exit(sys.version_info < (3, 11))' 2>&1)" || {
  printf 'gantry: needs Python 3.11 or later; %s reported: %s\n' "$py" "${out:-nothing (did it run as Python?)}" >&2
  exit 1
}

PYTHONPATH="$root" exec "$py" -P -m gantry build "$@"
