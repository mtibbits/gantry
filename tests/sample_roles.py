"""Role constants for gantry's fictional sample sites.

Each constant names a project in samples/devagent/config.toml by the role it
plays in tests and the demo; samples/README.md has the role table. Import
roles from here and never hard-code a site name.

Import contract: there is no conftest.py and no package. pytest's default
`prepend` import mode puts tests/ on sys.path, so `from sample_roles import
...` resolves inside the test run. Non-pytest consumers insert <root>/tests
into sys.path themselves.
"""

BUSY = "harbor"
OWN_UNIT = "orchard"
UNESTIMATED = "quarry"
SMALL = "lighthouse"
HOSTILE_SITE = SMALL
NO_WBS = "bakery"
UNREADABLE = "marsh"
# The sample WBS.md history (#28): samples/history/plan.toml.
HISTORY_RECENT = BUSY  # a glyph change inside the four-week window
HISTORY_STALE = OWN_UNIT  # a last glyph change older than the window
HISTORY_YOUNG = SMALL  # history that begins inside the window, no change since
HISTORY_LESS = UNESTIMATED  # a WBS.md and no `history` key
READABLE = frozenset({BUSY, OWN_UNIT, UNESTIMATED, SMALL})
NOT_BUILT = frozenset({NO_WBS, UNREADABLE})
HOSTILE_TITLE = 'Render <b>this</b> & "that" as text'
