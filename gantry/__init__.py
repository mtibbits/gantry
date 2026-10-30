"""Gantry: planning pages for many projects, built from devagent WBS files."""

import sys

# Old-syntax guard: an older Python must reach this line to explain itself.
if sys.version_info < (3, 11):
    sys.exit("gantry needs Python 3.11 or later; this is Python %d.%d" % sys.version_info[:2])
