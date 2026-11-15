"""The fires list: an installation's small private Markdown file of fires
that are not leaves yet (#30). docs/fires-list.md states the rules.

The module owns its report kinds and its report entry, never
gantry.model's: the fire collector (#32) merges the two reports. Every
`line` is a 1-based file line. No message holds a path: the file is always
"the fires list", named by its config key.
"""

from dataclasses import dataclass

REPORT_KINDS = ("fires_list_unreadable", "fires_bad_entry", "fires_unclosed_fence", "fires_unknown_site",
                "fires_future_date")
_KIND_ORDER = {kind: i for i, kind in enumerate(REPORT_KINDS)}


@dataclass(frozen=True)
class FiresReportEntry:  # the fires list's own; never gantry.model's
    site: None  # always None: the file is not a site
    line: int | None  # None only for fires_list_unreadable
    kind: str
    message: str


def report_order(entries) -> list:
    """The entries in report order: the whole-file entry first, then by line,
    then by REPORT_KINDS order."""
    return sorted(entries, key=lambda e: (e.line is not None, e.line or 0, _KIND_ORDER[e.kind]))
