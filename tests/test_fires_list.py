"""The fires list (#30): its format, its reader, ages and the flag.

docs/fires-list.md states the rules. Control characters are built with chr()
at run time, never typed as escapes in source or held in a fixture file.
"""

import random

from gantry import fires_list as fl
from gantry import model


# --- report kinds and the report entry (Task 2) ---------------------------------


KINDS = ("fires_list_unreadable", "fires_bad_entry", "fires_unclosed_fence", "fires_unknown_site",
         "fires_future_date")


def test_report_kinds_and_order():
    assert fl.REPORT_KINDS == KINDS
    for kind in KINDS:
        line = None if kind == "fires_list_unreadable" else 4
        e = fl.FiresReportEntry(None, line, kind, "m")
        assert (e.site, e.line, e.kind, e.message) == (None, line, kind, "m")
    whole = fl.FiresReportEntry(None, None, "fires_list_unreadable", "a")
    early = fl.FiresReportEntry(None, 2, "fires_unknown_site", "b")
    bad = fl.FiresReportEntry(None, 7, "fires_bad_entry", "c")
    future = fl.FiresReportEntry(None, 7, "fires_future_date", "d")
    fence = fl.FiresReportEntry(None, 7, "fires_unclosed_fence", "e")
    expected = [whole, early, bad, fence, future]
    shuffled = expected[:]
    random.Random(30).shuffle(shuffled)
    assert shuffled != expected
    assert fl.report_order(shuffled) == expected
    assert fl.FiresReportEntry is not model.ReportEntry
