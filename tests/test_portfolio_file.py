"""The portfolio file (#22).

docs/portfolio-file.md states the rules these tests pin. Every line number
asserted here is a literal read off its fixture, never computed from it.
"""

import os
import shutil
import time
from datetime import date
from pathlib import Path

import pytest

import gantry.portfolio_file as pf
from gantry.config import load_config as load_gantry_config
from gantry.portfolio_file import (
    CARRIED_KINDS,
    DEFAULTS,
    PORTFOLIO_LABEL,
    REPORT_KINDS,
    SETTINGS_KEYS,
    check_portfolio,
    format_entry,
    intended_shares,
    read_portfolio_file,
    report_entries,
)
from gantry.cli import MILESTONE_NOTE as MILESTONE_NOTE_TEXT
from gantry.requirements import FENCE_RE, ReqId, fence_spans
from gantry.roots import directory_roots
from gantry.sites import config_roots
from sample_roles import BUSY, NOT_BUILT, OWN_UNIT, READABLE, SMALL, UNESTIMATED
from test_samples import GANTRY_SAMPLE, ROOT, decode_bytes, git, git_work_tree, issue_leaves, md_tables, needs_git, wbs
from helpers_config import roots_around

FIXTURES = Path(__file__).parent / "fixtures" / "portfolio_file"
NOT_UTF8 = FIXTURES / "not_utf8.md"
NOT_UTF8_AT = NOT_UTF8.read_bytes().index(b"\xe9")
NS = "life"
BRS = "business requirements"
SETTINGS = "settings"


def read(name):
    return read_portfolio_file(directory_roots([FIXTURES]), FIXTURES / name, br_namespace=NS)


def entries(p):
    return [(e.kind, e.section, e.line, e.key_path) for e in p.report]


def fields_text(entry):
    return [str(v) for v in vars(entry).values()]


# --- the file, its sections and the BR section ---------------------------------


def test_br_section_call(monkeypatch):
    calls = []
    real = pf.parse_requirements

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(pf, "parse_requirements", spy)
    path = FIXTURES / "no_settings.md"
    p = read_portfolio_file(roots_around(path), path, br_namespace="life")
    ((args, kwargs),) = calls
    assert args[1:] == ("life",)
    assert kwargs == {"source": str(path), "first_line": 4, "layers": {"BR"}, "br_namespace": "life"}
    assert list(p.brs.requirements) == ["life:BR-1", "life:BR-2", "life:BR-3"]
    assert p.brs.requirements["life:BR-3"].line == 7


def test_br_duplicate_file_line():
    p = read("br_duplicate.md")
    (e,) = p.report
    assert (e.kind, e.section, e.line, e.key_path, e.source) == ("duplicate_id", BRS, 8, None, PORTFOLIO_LABEL)
    assert e.source == "the portfolio file"
    (carried,) = p.brs.report
    assert (carried.kind, carried.line, carried.source) == ("duplicate_id", 8, "the portfolio file")
    path = str(FIXTURES / "br_duplicate.md")
    for entry in [*p.report, *p.brs.report]:
        assert not [v for v in fields_text(entry) if path in v]


@pytest.mark.parametrize(
    ("name", "kind", "line"),
    [
        ("br_malformed.md", "malformed_item", 8),
        ("br_duplicate.md", "duplicate_id", 8),
        ("br_misplaced.md", "misplaced", 8),
        ("br_undefined.md", "undefined_link", 7),
        ("br_cycle.md", "refines_cycle", 7),
    ],
)
def test_br_carried_kinds(name, kind, line):
    p = read(name)
    assert entries(p) == [(kind, BRS, line, None)]


def test_br_section_unclosed_comment(tmp_path):
    # The comment also hides the Settings heading, as on the page.
    p = write(tmp_path, fixture_with("br_malformed.md", "- BR-01: A leading zero.\n", "<!--\n- BR-2: Hidden.\n"))
    assert entries(p) == [("malformed_comment", BRS, 8, None), ("no_settings", SETTINGS, None, None)]
    assert p.report[0].message == "an HTML comment with no closing '-->' hides every line after it"
    assert list(p.brs.requirements) == ["life:BR-1"]
    assert p.settings_read is False


def commented(old, new):
    """br_malformed.md without its malformed item, with `old` replaced by `new`."""
    return fixture_with("br_malformed.md", "- BR-01: A leading zero.\n", "").replace(old, new)


def test_heading_in_a_comment_is_not_a_section(tmp_path):
    p = write(tmp_path, commented("## Business requirements\n", "<!--\n## Business requirements\n"
                                  "- BR-9: Old draft.\n-->\n## Business requirements\n"))
    assert entries(p) == []
    assert list(p.brs.requirements) == ["life:BR-1"]
    assert p.settings_read is True


def test_settings_in_a_comment_are_not_read(tmp_path):
    p = write(tmp_path, commented("## Settings\n", "<!--\n## Settings\n") + "-->\n")
    assert entries(p) == [("no_settings", SETTINGS, None, None)]
    assert list(p.brs.requirements) == ["life:BR-1"]
    assert p.settings_read is False


UNCLOSED = "an HTML comment with no closing '-->' hides every line after it"


def test_unclosed_comment_in_the_preamble_is_reported(tmp_path):
    p = write(tmp_path, commented("A fictional portfolio file for tests.\n", "<!-- note\n"))
    assert entries(p) == [("malformed_comment", None, 3, None), ("no_br_section", BRS, None, None),
                          ("no_settings", SETTINGS, None, None)]
    assert p.report[0].message == UNCLOSED
    assert p.brs.requirements == {} and p.settings_read is False


def test_unclosed_comment_in_settings_is_reported(tmp_path):
    p = write(tmp_path, commented("```toml\n", "<!--\n```toml\n"))
    assert entries(p) == [("no_settings", SETTINGS, None, None), ("malformed_comment", SETTINGS, 11, None)]
    assert p.report[1].message == UNCLOSED


def test_thematic_break_in_the_br_section_encloses_nothing(tmp_path):
    p = write(tmp_path, commented("- BR-1: Keep the household running.\n", "- BR-1: Keep the household running.\n"
                                  "\n* * *\n\n  - BR-2: Two.\n")
              .replace("agent_lanes = 1\n", 'agent_lanes = 1\n\n[site.harbor]\nserves = ["BR-2"]\n'))
    assert entries(p) == []
    assert list(p.brs.requirements) == ["life:BR-1", "life:BR-2"]


def test_item_start_comment_in_the_br_section_hides_nothing(tmp_path):
    # Indented 6 inside BR-1 the line is code on a page; nested in an item it is still reported
    # (a stated simplification). Either way nothing after it is hidden.
    p = write(tmp_path, commented("- BR-1: Keep the household running.\n", "- BR-1: Keep the household running.\n"
                                  "\n      - <!-- old wording\n\n- BR-3: Three.\n"))
    assert entries(p) == [("malformed_comment", BRS, 9, None)]
    assert list(p.brs.requirements) == ["life:BR-1", "life:BR-3"]
    assert p.settings_read is True


def test_toml_block_in_a_comment_is_not_the_settings_block(tmp_path):
    p = write(tmp_path, commented("```toml\n", "<!--\n```toml\nportfolio_version = 99\n```\n-->\n```toml\n"))
    assert entries(p) == []
    assert p.settings_read is True


def _unreadable(tmp_path, case):
    if case == "missing":
        return tmp_path / "nope.md"
    if case == "directory":
        folder = tmp_path / "folder.md"
        folder.mkdir()
        return folder
    copy = tmp_path / "bad.md"
    shutil.copyfile(NOT_UTF8, copy)
    return copy


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("missing", "the portfolio file does not exist"),
        ("directory", "the portfolio file is not a file"),
        ("not_utf8", f"the portfolio file is not valid UTF-8 at byte {NOT_UTF8_AT}"),
    ],
)
def test_unreadable(tmp_path, case, message):
    p = read_portfolio_file(directory_roots([tmp_path]), _unreadable(tmp_path, case), br_namespace=NS)
    (e,) = p.report
    assert (e.kind, e.section, e.line, e.key_path, e.message) == ("portfolio_unreadable", None, None, None, message)
    assert p.brs.requirements == {} and p.brs.report == []
    assert p.settings_read is False
    assert not [v for v in fields_text(e) if str(tmp_path) in v]


def test_not_utf8_fixture_stays_invalid():
    data = NOT_UTF8.read_bytes()
    with pytest.raises(UnicodeDecodeError) as exc:
        data.decode("utf-8")
    assert exc.value.start == data.index(b"\xe9")


@needs_git
def test_not_utf8_fixture_is_not_text():
    if not git_work_tree(ROOT):
        pytest.skip("the gantry root is not a git work tree")
    result = git(ROOT, "check-attr", "text", "--", "tests/fixtures/portfolio_file/not_utf8.md")
    assert result.returncode == 0, decode_bytes(result.stderr)
    assert decode_bytes(result.stdout).strip().endswith("text: unset")


def test_no_br_section():
    p = read("no_br_section.md")
    (e,) = [e for e in p.report if e.section == BRS]
    assert (e.kind, e.line, e.key_path) == ("no_br_section", None, None)
    assert e.message == ("no '## Business requirements' heading (level 2, outside a fence or comment); "
                         "no business requirements are read")
    assert p.brs.namespace == "life" and p.brs.requirements == {} and p.brs.report == []


def test_duplicate_br_heading():
    p = read("duplicate_section.md")
    (e,) = [e for e in p.report if e.section == BRS]
    assert (e.kind, e.line, e.key_path) == ("duplicate_section", 20, None)
    assert e.message == "a second '## Business requirements' heading; only the first (line 5) is read"
    assert list(p.brs.requirements) == ["life:BR-1", "life:BR-2"]


def test_bom_and_crlf(tmp_path):
    data = (FIXTURES / "no_settings.md").read_bytes()
    plain = read("no_settings.md")
    for name, variant in (("bom.md", b"\xef\xbb\xbf" + data), ("crlf.md", data.replace(b"\n", b"\r\n"))):
        (tmp_path / name).write_bytes(variant)
        p = read_portfolio_file(directory_roots([tmp_path]), tmp_path / name, br_namespace=NS)
        assert {k: (r.text, r.line) for k, r in p.brs.requirements.items()} == {
            k: (r.text, r.line) for k, r in plain.brs.requirements.items()
        }
        assert entries(p) == entries(plain)


def test_hostile_input_is_fast(tmp_path):
    hostile = {
        "heading.md": "## " + " " * 20_000 + "x\n" + "##" + " " * 20_000 + "\n",
        "key.md": "## Settings\n\n```toml\n" + "a" * 20_000 + "\n" + "a" * 20_000 + " = 1\n" + "[" + "a" * 20_000 + "\n```\n",
        "fences.md": "```a\n" * 20_000,
    }
    start = time.perf_counter()
    for name, text in hostile.items():
        (tmp_path / name).write_bytes(text.encode("utf-8"))
        read_portfolio_file(directory_roots([tmp_path]), tmp_path / name, br_namespace=NS)
    assert time.perf_counter() - start < 2


# --- the settings block: TOML, its line, the version, unknown keys -------------


def not_set(p):
    """Every setting is not set: the fields a consumer reads are None or empty."""
    return (
        p.allocations == []
        and p.serves == {}
        and p.milestone is None
        and all(v in (None, ()) for v in vars(p.capacity).values())
        and all(v is None for v in vars(p.thresholds).values())
    )


def test_br_only_file():
    p = read("no_settings.md")
    assert entries(p) == [("no_settings", SETTINGS, None, None)]
    assert p.report[0].message == (
        "no ```toml block under a '## Settings' heading (level 2, outside a fence or comment); every setting is not set"
    )
    assert list(p.brs.requirements) == ["life:BR-1", "life:BR-2", "life:BR-3"]
    assert p.settings_read is False and not_set(p)


def test_settings_invalid_line():
    # Runs on every CI Python: 3.11 gives the line only in the message, 3.14 also as lineno.
    p = read("settings_invalid.md")
    assert entries(p) == [("settings_invalid", SETTINGS, 18, None)]
    assert p.report[0].message == "the settings block is not valid TOML: Invalid value; every setting is not set"
    assert p.settings_read is False and not_set(p)
    assert list(p.brs.requirements) == ["life:BR-1", "life:BR-2"]


class _FakeTomlError(ValueError):
    def __init__(self, text, lineno=None):
        super().__init__(text)
        if lineno is not None:
            self.lineno = lineno


def test_toml_error_line_paths():
    opener, closer = 10, 20  # 0-based; nine inner lines, file lines 12 to 20
    line = pf._toml_error_line
    assert line(_FakeTomlError("Invalid value", lineno=2), opener, closer) == opener + 3
    assert line(ValueError("Invalid value (at line 2, column 5)"), opener, closer) == opener + 3
    assert line(ValueError("Unterminated string (at end of document)"), opener, closer) == 20
    assert line(_FakeTomlError("Unterminated string", lineno=10), opener, closer) == 20
    assert line(ValueError("Exceeds the limit"), opener, closer) is None


@pytest.mark.parametrize("value", ["1" * 5_000, "[" * 5_000], ids=["long_int", "deep_nesting"])
def test_long_int_and_deep_nesting(tmp_path, value):
    text = "## Settings\n\n```toml\nportfolio_version = 1\nx = " + value + "\n```\n"
    (tmp_path / "p.md").write_bytes(text.encode("utf-8"))
    p = read_portfolio_file(directory_roots([tmp_path]), tmp_path / "p.md", br_namespace=NS)
    assert [e.kind for e in p.report if e.section == SETTINGS] == ["settings_invalid"]
    assert p.settings_read is False


@pytest.mark.parametrize(
    ("line", "at", "message"),
    [
        (None, None, "no portfolio_version; this Gantry reads portfolio_version 1, so the settings block is not read"),
        ("portfolio_version = 2", 13,
         "portfolio_version 2 is not 1, the version this Gantry reads, so the settings block is not read"),
        ('portfolio_version = "1"', 13,
         "portfolio_version a string is not 1, the version this Gantry reads, so the settings block is not read"),
        ("portfolio_version = true", 13,
         "portfolio_version a boolean is not 1, the version this Gantry reads, so the settings block is not read"),
        ("portfolio_version = 1.0", 13,
         "portfolio_version 1.0 is not 1, the version this Gantry reads, so the settings block is not read"),
    ],
)
def test_bad_version(tmp_path, line, at, message):
    text = (FIXTURES / "bad_version.md").read_bytes().decode("utf-8")
    text = text.replace("portfolio_version = 2\n", "" if line is None else line + "\n")
    (tmp_path / "p.md").write_bytes(text.encode("utf-8"))
    p = read_portfolio_file(directory_roots([tmp_path]), tmp_path / "p.md", br_namespace=NS)
    # The file also holds an unknown key and shares summing to 99: neither is reported.
    assert entries(p) == [("bad_version", SETTINGS, at, "portfolio_version")]
    assert p.report[0].message == message
    assert list(p.brs.requirements) == ["life:BR-1", "life:BR-2"]
    assert p.settings_read is False and not_set(p)


def test_unknown_key():
    p = read("unknown_key.md")
    assert entries(p) == [
        ("unknown_key", SETTINGS, 14, "colour"),
        ("unknown_key", SETTINGS, 19, "capacity.agent_lane"),
        ("unknown_key", SETTINGS, 23, "site.harbor.serve"),
    ]
    assert {e.message for e in p.report} == {"unknown key; it is ignored"}
    assert p.settings_read is True


def test_fences_do_not_count():
    p = read("fences.md")
    assert p.report == []
    assert list(p.brs.requirements) == ["life:BR-1", "life:BR-2"]
    assert p.settings_read is True


def test_duplicate_settings(tmp_path):
    p = read("duplicate_block.md")
    assert entries(p) == [("duplicate_section", SETTINGS, 19, None)]
    assert p.report[0].message == "a second toml block under '## Settings'; only the first (line 12) is read"
    assert p.key_lines["capacity.agent_lanes"] == 17
    text = (FIXTURES / "fences.md").read_bytes() + b"\n## Settings\n\n```toml\nportfolio_version = 2\n```\n"
    (tmp_path / "p.md").write_bytes(text)
    p = read_portfolio_file(directory_roots([tmp_path]), tmp_path / "p.md", br_namespace=NS)
    assert entries(p) == [("duplicate_section", SETTINGS, 41, None)]
    assert p.report[0].message == "a second '## Settings' heading; only the first (line 17) is read"
    assert p.settings_read is True


def test_key_paths_are_one_line():
    assert pf._seg("a\nb") == '"a\\nb"'
    assert pf._seg("a.b") == '"a.b"'
    assert pf._seg("harbor") == "harbor"
    assert pf._path(("allocation", 2, "shares", "beta")) == "allocation[2].shares.beta"
    assert pf._path(("shares", "my.site")) == 'shares."my.site"'


def test_key_lines_locator():
    p = read("fences.md")
    assert {k: p.key_lines[k] for k in ("portfolio_version", "capacity", "capacity.agent_lanes", "allocation[1]",
                                        "allocation[1].from", "allocation[1].shares", "site.harbor.serves",
                                        "milestone.issue")} == {
        "portfolio_version": 22, "capacity": 24, "capacity.agent_lanes": 26, "allocation[1]": 29,
        "allocation[1].from": 30, "allocation[1].shares": 31, "site.harbor.serves": 34, "milestone.issue": 38,
    }
    assert pf._line(p.key_lines, ("allocation", 1, "shares", "harbor")) == 31
    assert pf._line(p.key_lines, ("thresholds", "wip_max")) is None


# --- the registry and its values -------------------------------------------------


def write(tmp_path, text, name="p.md"):
    (tmp_path / name).write_bytes(text.encode("utf-8"))
    return read_portfolio_file(directory_roots([tmp_path]), tmp_path / name, br_namespace=NS)


def fixture_with(name, old, new):
    text = (FIXTURES / name).read_bytes().decode("utf-8")
    assert old in text
    return text.replace(old, new)


def bad_values(p):
    return {e.key_path: (e.line, e.message) for e in p.report if e.kind == "bad_value"}


def test_every_default():
    p = read("capacity_missing.md")
    assert entries(p) == [
        ("capacity_missing", SETTINGS, None, "capacity.attention_hours"),
        ("capacity_missing", SETTINGS, None, "capacity.agent_lanes"),
    ]
    assert p.report[1].message == "no capacity.agent_lanes, so the week planner has no capacity to plan into"
    assert p.settings_read is True
    c = p.capacity
    assert (c.attention_hours, c.agent_lanes, c.reserved, c.attention_mapping) == (None, None, (), None)
    assert (c.block_min_hours, c.block_default_hours, c.block_start_cost_hours) == (1, 2, 0.25)
    t = p.thresholds
    assert (t.wip_max, t.unknown_deps_max, t.unplanned_max, t.share_gap_points, t.neglect_weeks,
            t.effort_window_weeks) == (3, 0, 0, 10, 4, 4)
    assert p.milestone is None and p.serves == {} and p.allocations == []


def test_bad_value_type():
    p = read("bad_value_type.md")
    expected = {
        "site.harbor.serves": (16, "an array of business requirement IDs, not a string; it is empty"),
        "capacity.attention_hours": (19, "a table of weekday (mon to sun) to hours, not a string; it is not set"),
        "capacity.agent_lanes": (20, "an integer, 0 or more, not a boolean; it is not set"),
        "capacity.block_min_hours": (21, "a finite number above 0, not a boolean; the default 1 is used"),
        "capacity.block_default_hours": (22, "a finite number, at least block_min_hours, not a boolean; "
                                             "the default 2 is used"),
        "capacity.block_start_cost_hours": (23, "a finite number, 0 or more, not a boolean; the default 0.25 is used"),
        "capacity.attention_mapping": (24, "an array of exactly five numbers, each 0 or more, not decreasing, "
                                           "not a boolean; it is not set"),
        "capacity.reserved[1].day": (27, "a weekday (mon to sun), not 1; the reserved block is set aside"),
        "capacity.reserved[1].start": (28, 'a time "HH:MM" from 00:00 to 23:59, not 9; the reserved block is set aside'),
        "capacity.reserved[1].hours": (29, "a finite number above 0, not a boolean; the reserved block is set aside"),
        "capacity.reserved[1].label": (30, "a string, not 1; the reserved block is set aside"),
        "milestone.project": (33, "a non-empty string, not 1; the milestone is not set"),
        "milestone.issue": (34, "a non-empty string, not 1; the milestone is not set"),
        "milestone.label": (35, 'a string, not 1; the default "autonomy milestone" is used'),
        "thresholds.wip_max": (38, "an integer, 0 or more, not a boolean; the default 3 is used"),
        "thresholds.unknown_deps_max": (39, "an integer, 0 or more, not a boolean; the default 0 is used"),
        "thresholds.unplanned_max": (40, "an integer, 0 or more, not a boolean; the default 0 is used"),
        "thresholds.share_gap_points": (41, "a finite number above 0, not a boolean; the default 10 is used"),
        "thresholds.neglect_weeks": (42, "an integer above 0, not a boolean; the default 4 is used"),
        "thresholds.effort_window_weeks": (43, "an integer above 0, not a boolean; the default 4 is used"),
    }
    assert bad_values(p) == {k: (line, "must be " + text) for k, (line, text) in expected.items()}
    assert [e.key_path for e in p.report if e.kind != "bad_value"] == [
        "capacity.attention_hours", "capacity.agent_lanes"]  # capacity_missing, advisory
    c = p.capacity
    assert (c.attention_hours, c.agent_lanes, c.reserved, c.attention_mapping) == (None, None, (), None)
    assert (c.block_min_hours, c.block_default_hours, c.block_start_cost_hours) == (1, 2, 0.25)
    assert p.thresholds == pf.Thresholds(3, 0, 0, 10, 4, 4)
    assert p.milestone is None
    assert p.serves == {"harbor": []}


def test_bad_value_range(tmp_path):
    p = read("bad_value_range.md")
    expected = {
        "capacity.attention_hours.mon": (16, "a finite number from 0 to 24, not 25; the day reads 0"),
        "capacity.agent_lanes": (17, "an integer, 0 or more, not -1; it is not set"),
        "capacity.block_min_hours": (18, "a finite number above 0, not 0; the default 1 is used"),
        "capacity.block_default_hours": (19, "a finite number, at least block_min_hours, not 0.5; the default 2 is used"),
        "capacity.block_start_cost_hours": (20, "a finite number, 0 or more, not -0.5; the default 0.25 is used"),
        "capacity.reserved[1].day": (23, "a weekday (mon to sun), not a string; the reserved block is set aside"),
        "capacity.reserved[1].start": (24, 'a time "HH:MM" from 00:00 to 23:59, not a string; '
                                           "the reserved block is set aside"),
        "capacity.reserved[1].hours": (25, "a finite number above 0, not 0; the reserved block is set aside"),
        "thresholds.wip_max": (28, "an integer, 0 or more, not -1; the default 3 is used"),
        "thresholds.unknown_deps_max": (29, "an integer, 0 or more, not -1; the default 0 is used"),
        "thresholds.unplanned_max": (30, "an integer, 0 or more, not -2; the default 0 is used"),
        "thresholds.share_gap_points": (31, "a finite number above 0, not 0; the default 10 is used"),
        "thresholds.neglect_weeks": (32, "an integer above 0, not 0; the default 4 is used"),
        "thresholds.effort_window_weeks": (33, "an integer above 0, not 0; the default 4 is used"),
    }
    assert bad_values(p) == {k: (line, "must be " + text) for k, (line, text) in expected.items()}
    assert p.capacity.attention_hours == {"mon": 0, "tue": 4, "wed": 0, "thu": 0, "fri": 0, "sat": 0, "sun": 0}
    assert p.capacity.reserved == ()
    # Not finite (Q1): each is bad_value and its default is used.
    p = write(tmp_path, fixture_with("bad_value_range.md", "attention_hours = { mon = 25, tue = 4 }\nagent_lanes = -1\n"
                                     "block_min_hours = 0\nblock_default_hours = 0.5\nblock_start_cost_hours = -0.5\n",
                                     "attention_hours = { mon = nan }\nagent_lanes = 1\nblock_min_hours = inf\n"
                                     "block_default_hours = inf\nblock_start_cost_hours = inf\n")
              .replace("share_gap_points = 0", "share_gap_points = inf"))
    bad = bad_values(p)
    for key in ("capacity.attention_hours.mon", "capacity.block_min_hours", "capacity.block_default_hours",
                "capacity.block_start_cost_hours", "thresholds.share_gap_points"):
        assert "a finite number" in bad[key][1], key
    assert bad["capacity.attention_hours.mon"][1].endswith(" not nan; the day reads 0")
    assert bad["capacity.block_min_hours"][1].endswith(" not inf; the default 1 is used")
    c = p.capacity
    assert (c.block_min_hours, c.block_default_hours, c.block_start_cost_hours) == (1, 2, 0.25)
    assert p.thresholds.share_gap_points == 10
    assert c.attention_hours["mon"] == 0


def test_block_default_follows_a_larger_minimum(tmp_path):
    body = "## Settings\n\n```toml\nportfolio_version = 1\n[capacity]\nblock_min_hours = 3\n{}```\n"
    p = write(tmp_path, body.format(""))
    assert (p.capacity.block_min_hours, p.capacity.block_default_hours) == (3, 3)
    p = write(tmp_path, body.format("block_default_hours = 2.5\n"))
    assert bad_values(p)["capacity.block_default_hours"][1].endswith("not 2.5; the default 3 is used")
    assert p.capacity.block_default_hours == 3


@pytest.mark.parametrize(
    ("value", "read"),
    [
        ("[0, 0.15, 0.3, 0.6, 1.0]", (0, 0.15, 0.3, 0.6, 1.0)),
        ("[0, 0, 0, 0, 0]", (0, 0, 0, 0, 0)),
        ("[0, 0.1, 0.25, 0.5, inf]", (0, 0.1, 0.25, 0.5, float("inf"))),
        ("[0, 0.1, 0.25, 0.5]", None),
        ("[0, 0.1, 0.25, 0.5, 1, 2]", None),
        ("[0, 0.5, 0.25, 0.5, 1]", None),
        ("[0, 0.1, nan, 0.5, 1]", None),
        ("[0, 0.1, true, 0.5, 1]", None),
        ("[-1, 0.1, 0.25, 0.5, 1]", None),
    ],
)
def test_attention_mapping_shape(tmp_path, value, read):
    p = write(tmp_path, fixture_with("capacity_missing.md", "portfolio_version = 1\n",
                                     f"portfolio_version = 1\n[capacity]\nattention_mapping = {value}\n"))
    assert p.capacity.attention_mapping == read
    bad = [e for e in p.report if e.kind == "bad_value"]
    if read is None:
        assert [(e.key_path, e.line) for e in bad] == [("capacity.attention_mapping", 15)]
    else:
        assert bad == []


def test_reserved_exceeds_capacity(tmp_path):
    p = read("reserved_exceeds_capacity.md")
    assert entries(p) == [("reserved_exceeds_capacity", SETTINGS, 19, "capacity.reserved")]
    assert p.report[0].message == "reserved blocks on mon total 5 hours, more than its 4 attention hours"
    assert [(r.day, r.start, r.hours, r.label, r.line) for r in p.capacity.reserved] == [
        ("mon", "09:00", 3, None, 19), ("mon", "13:00", 2, "Standing meeting", 24), ("tue", "09:00", 2, None, 30)]
    # A 400-digit hours value is a valid number: reported, never raised.
    p = write(tmp_path, fixture_with("reserved_exceeds_capacity.md", "hours = 3\n", "hours = 1" + "0" * 400 + "\n"))
    assert [e.message for e in p.report] == [
        "reserved blocks on mon total a very large number of hours, more than its 4 attention hours"]


def test_priorities(tmp_path):
    p = read("undefined_priority.md")
    assert entries(p) == [("undefined_priority", SETTINGS, 20, "site.harbor.serves[2]")]
    assert p.report[0].message == "life:BR-7 names no business requirement in this file"
    assert p.serves == {"harbor": [ReqId("life", "BR", 1), ReqId("life", "BR", 2)]}
    p = read("misplaced_priority.md")
    assert entries(p) == [
        ("misplaced_priority", SETTINGS, 20, "site.harbor.serves[1]"),
        ("misplaced_priority", SETTINGS, 20, "site.harbor.serves[2]"),
    ]
    assert [e.message for e in p.report] == [
        "other:BR-1 is not in the installation namespace 'life'; it is not read",
        "life:FR-2 is a FR ID; serves names business requirements (BR)",
    ]
    assert p.serves == {"harbor": [ReqId("life", "BR", 1)]}
    # Not an ID, not a string, and a repeat kept once.
    p = write(tmp_path, fixture_with("misplaced_priority.md", '"other:BR-1", "FR-2", "BR-1"', '"BR-01", 3, "BR-1", "life:BR-1"'))
    assert bad_values(p) == {
        "site.harbor.serves[1]": (20, "must be a business requirement ID, not 'BR-01'; it is not read"),
        "site.harbor.serves[2]": (20, "must be a business requirement ID, not 3; it is not read"),
    }
    assert p.serves == {"harbor": [ReqId("life", "BR", 1)]}


def test_priorities_without_a_br_section(tmp_path):
    # One cause, one entry: no_br_section, and no priority entry.
    text = "## Settings\n\n```toml\nportfolio_version = 1\n[capacity]\nattention_hours = { mon = 4 }\nagent_lanes = 1\n" \
           '[site.harbor]\nserves = ["BR-9", "other:BR-1", "FR-2"]\n```\n'
    p = write(tmp_path, text)
    assert [e.kind for e in p.report] == ["no_br_section"]
    assert p.serves == {"harbor": []}


@pytest.mark.parametrize(
    ("toml", "key_path", "line"),
    [("site = 1\n", "site", 14), ("[site]\nharbor = 1\n", "site.harbor", 15),
     ("capacity = 1\n", "capacity", 14), ("thresholds = []\n", "thresholds", 14)],
)
def test_non_table_is_bad_value(tmp_path, toml, key_path, line):
    p = write(tmp_path, fixture_with("capacity_missing.md", "portfolio_version = 1\n", "portfolio_version = 1\n" + toml))
    assert [(e.key_path, e.line) for e in p.report if e.kind == "bad_value"] == [(key_path, line)]
    assert bad_values(p)[key_path][1].endswith("; it is ignored")


@pytest.mark.parametrize(
    ("issue", "kind", "message"),
    [
        ('"3.2.1"', "bad_milestone",
         "'3.2.1' is an outline position: name the leaf by its issue id; outline positions move at wbs update"),
        ('"3.2"', "bad_milestone",
         "'3.2' is an outline position: name the leaf by its issue id; outline positions move at wbs update"),
        ('"a b"', "bad_milestone", "'a b' is not an issue id (it must match [A-Za-z0-9][A-Za-z0-9_.-]*)"),
        ('"3"', None, None),
        ('"Issue-5"', None, None),
        ("3", "bad_value", "must be a non-empty string, not 3; the milestone is not set"),
    ],
)
def test_bad_milestone(tmp_path, issue, kind, message):
    p = write(tmp_path, fixture_with("bad_milestone.md", 'issue = "3.2.1"', f"issue = {issue}"))
    if kind is None:
        assert p.report == []
        m = p.milestone
        assert (m.project, m.issue, m.label, m.line) == ("lighthouse", issue.strip('"'), "autonomy milestone", 19)
    else:
        assert entries(p) == [(kind, SETTINGS, 21, "milestone.issue")]
        assert p.report[0].message == message
        assert p.milestone is None


def test_bad_milestone_fixture():
    p = read("bad_milestone.md")
    assert entries(p) == [("bad_milestone", SETTINGS, 21, "milestone.issue")]


def test_registry_shape():
    assert len(SETTINGS_KEYS) == len({k.path for k in SETTINGS_KEYS}) > 0
    assert DEFAULTS == {
        "milestone.label": "autonomy milestone", "capacity.block_min_hours": 1, "capacity.block_default_hours": 2,
        "capacity.block_start_cost_hours": 0.25, "thresholds.wip_max": 3, "thresholds.unknown_deps_max": 0,
        "thresholds.unplanned_max": 0, "thresholds.share_gap_points": 10, "thresholds.neglect_weeks": 4,
        "thresholds.effort_window_weeks": 4,
    }
    assert all(k.rule.isascii() for k in SETTINGS_KEYS)


# --- allocations and intended_shares --------------------------------------------


def test_undated_allocation():
    p = read("undated_allocation.md")
    assert entries(p) == [
        ("undated_allocation", SETTINGS, 18, "allocation[1].from"),
        ("undated_allocation", SETTINGS, 22, "allocation[2].from"),
        ("undated_allocation", SETTINGS, 26, "allocation[3].from"),
    ]
    assert [e.message for e in p.report] == [
        "no from date; this allocation is not used",
        "from must be a TOML local date (YYYY-MM-DD), not a string; this allocation is not used",
        "from must be a TOML local date (YYYY-MM-DD), not a date-time; this allocation is not used",
    ]
    assert p.allocations == []


def test_bad_allocation(tmp_path):
    p = read("bad_allocation.md")
    assert entries(p) == [
        ("bad_allocation", SETTINGS, 20, "allocation[1].shares"),
        ("bad_allocation", SETTINGS, 26, f"allocation[2].shares.{OWN_UNIT}"),
        ("bad_allocation", SETTINGS, 33, f"allocation[3].shares.{UNESTIMATED}"),
        ("bad_allocation", SETTINGS, 38, f"allocation[4].shares.{BUSY}"),
    ]
    assert [e.message for e in p.report] == [
        "the shares sum to 99, not 100; this allocation is not used",
        f"share {OWN_UNIT} must be a number, 0 or more, not -10; this allocation is not used",
        f"share {UNESTIMATED} must be a number, 0 or more, not a string; this allocation is not used",
        f"share {BUSY} must be a number, 0 or more, not a boolean; this allocation is not used",
    ]
    assert p.allocations == []
    # A 400-digit share is reported, never raised; a missing shares table is
    # reported on its allocation's header line.
    text = fixture_with("bad_allocation.md", "orchard = 49", "orchard = 1" + "0" * 400)
    text = text.replace("[[allocation]]\nfrom = 2026-04-06\n[allocation.shares]\nharbor = true\norchard = 100\n",
                        "[[allocation]]\nfrom = 2026-04-06\n")
    p = write(tmp_path, text)
    assert [(e.line, e.key_path, e.message) for e in p.report][0] == (
        20, "allocation[1].shares", "the shares sum to a very large number, not 100; this allocation is not used")
    assert [(e.line, e.key_path, e.message) for e in p.report][-1] == (
        35, "allocation[4].shares", "no shares table; this allocation is not used")


def test_shares_within_tolerance(tmp_path):
    text = fixture_with("duplicate_allocation_date.md", "harbor = 50, orchard = 30, quarry = 20",
                        "harbor = 33.33, orchard = 33.33, quarry = 33.33")
    p = write(tmp_path, text.replace("from = 2026-01-05\nshares = { harbor = 100 }", "from = 2026-02-02\nshares = []"))
    assert entries(p) == [("bad_allocation", SETTINGS, 24, "allocation[2].shares")]
    assert p.report[0].message == "shares must be a table, not an array; this allocation is not used"
    assert [a.shares for a in p.allocations] == [{BUSY: 33.33, OWN_UNIT: 33.33, UNESTIMATED: 33.33}]


def test_bad_between_good():
    p = read("allocations.md")
    assert entries(p) == [
        ("bad_allocation", SETTINGS, 24, "allocation[2].shares"),
        ("bad_allocation", SETTINGS, 32, "allocation[4].shares"),
    ]
    a = pf.intended_shares(p, date(2026, 2, 15))
    assert (a.from_date, a.shares, a.line) == (date(2026, 1, 5), {BUSY: 50, OWN_UNIT: 30, UNESTIMATED: 20}, 18)


def test_same_from_invalid_and_valid():
    p = read("allocations.md")
    assert "duplicate_allocation_date" not in [e.kind for e in p.report]
    a = pf.intended_shares(p, date(2026, 4, 6))
    assert (a.from_date, a.shares, a.line) == (date(2026, 4, 6), {BUSY: 100}, 34)
    assert [a.from_date for a in p.allocations] == [date(2026, 1, 5), date(2026, 3, 2), date(2026, 4, 6)]


def test_duplicate_allocation_date():
    p = read("duplicate_allocation_date.md")
    assert entries(p) == [("duplicate_allocation_date", SETTINGS, 23, "allocation[2].from")]
    assert p.report[0].message == "another allocation (line 18) has from 2026-01-05; the first is used"
    assert pf.intended_shares(p, date(2026, 1, 5)).shares == {BUSY: 50, OWN_UNIT: 30, UNESTIMATED: 20}


def test_duplicate_allocation_date_with_no_known_line(tmp_path):
    # A quoted key is not located: the message names no line rather than "line None".
    p = write(tmp_path, "## Settings\n\n```toml\nportfolio_version = 1\n"
                        '"allocation" = [{from = 2026-01-01, shares = {a = 100}}, {from = 2026-01-01, shares = {a = 100}}]\n'
                        "```\n")
    (e,) = [e for e in p.report if e.kind == "duplicate_allocation_date"]
    assert e.message == "another allocation has from 2026-01-01; the first is used"


def test_empty_milestone_project_is_one_entry(tmp_path):
    p = write(tmp_path, fixture_with("bad_milestone.md", 'project = "lighthouse"\nissue = "3.2.1"',
                                     'project = ""\nissue = "Issue-5"'))
    assert [(e.kind, e.key_path) for e in p.report] == [("bad_value", "milestone.project")]
    assert check_portfolio(p, SITES) == []


def test_before_every_allocation():
    assert pf.intended_shares(read("allocations.md"), date(2025, 1, 1)) is None


def test_allocation_not_an_array(tmp_path):
    p = write(tmp_path, fixture_with("capacity_missing.md", "portfolio_version = 1\n",
                                     "portfolio_version = 1\nallocation = 3\n"))
    assert bad_values(p) == {"allocation": (14, "must be an array of tables ([[allocation]]), not 3; it is empty")}
    assert p.allocations == []


# --- check_portfolio, kind coverage and the no-path guard -------------------------

SITES = READABLE | NOT_BUILT


def test_unknown_project():
    p = read("unknown_project.md")
    assert p.report == []
    got = check_portfolio(p, SITES)
    assert [(e.kind, e.section, e.line, e.key_path) for e in got] == [
        ("unknown_project", SETTINGS, 21, "allocation[1].shares.nowhere"),
        ("unknown_project", SETTINGS, 21, "allocation[1].shares.Harbor"),
        ("unknown_project", SETTINGS, 23, "site.nowhere"),
        ("unknown_project", SETTINGS, 27, "milestone.project"),
    ]
    assert [e.message for e in got] == [
        "'nowhere' is not a site in the devagent config; its share still counts toward the allocation's 100",
        "'Harbor' is not a site in the devagent config; its share still counts toward the allocation's 100",
        "'nowhere' is not a site in the devagent config",
        "'nowhere' is not a site in the devagent config",
    ]
    assert intended_shares(p, date(2026, 1, 5)).shares == {BUSY: 50, "nowhere": 30, "Harbor": 20}


def test_report_entries_merge_in_line_order(tmp_path):
    # A reader entry on a later line than check_portfolio's prints after them.
    p = write(tmp_path, fixture_with("unknown_project.md", 'issue = "Issue-1"\n', 'issue = "Issue-1"\nlabel = 3\n'))
    assert [(e.kind, e.line) for e in report_entries(p, SITES)] == [
        ("unknown_project", 21),
        ("unknown_project", 21),
        ("unknown_project", 23),
        ("unknown_project", 27),
        ("bad_value", 29),
    ]


@pytest.mark.parametrize(
    ("issues", "message"),
    [
        ({BUSY: {"Issue-1", "Issue-2"}}, "no leaf on harbor has issue 'Issue-99'"),
        ({BUSY: None}, "site harbor could not be read, so the milestone leaf cannot be found"),
        ({}, "site harbor could not be read, so the milestone leaf cannot be found"),
    ],
)
def test_milestone_no_leaf(issues, message):
    p = read("milestone_no_leaf.md")
    got = check_portfolio(p, SITES, issues)
    assert [(e.kind, e.section, e.line, e.key_path, e.message) for e in got] == [
        ("milestone_no_leaf", SETTINGS, 21, "milestone.issue", message)]
    assert check_portfolio(p, SITES) == []
    assert check_portfolio(p, SITES, {BUSY: {"Issue-99"}}) == []


@pytest.mark.parametrize("name", ["no_settings.md", "settings_invalid.md", "bad_version.md"])
def test_no_settings_derived_without_settings(name):
    assert check_portfolio(read(name), ["x"], {}) == []


S = SETTINGS
EVERY_FIXTURE = {
    "allocations.md": [("bad_allocation", S, 24, "allocation[2].shares"), ("bad_allocation", S, 32, "allocation[4].shares")],
    "bad_allocation.md": [
        ("bad_allocation", S, 20, "allocation[1].shares"), ("bad_allocation", S, 26, "allocation[2].shares.orchard"),
        ("bad_allocation", S, 33, "allocation[3].shares.quarry"), ("bad_allocation", S, 38, "allocation[4].shares.harbor")],
    "bad_milestone.md": [("bad_milestone", S, 21, "milestone.issue")],
    "bad_value_range.md": [
        ("bad_value", S, 16, "capacity.attention_hours.mon"), ("bad_value", S, 17, "capacity.agent_lanes"),
        ("capacity_missing", S, 17, "capacity.agent_lanes"), ("bad_value", S, 18, "capacity.block_min_hours"),
        ("bad_value", S, 19, "capacity.block_default_hours"), ("bad_value", S, 20, "capacity.block_start_cost_hours"),
        ("bad_value", S, 23, "capacity.reserved[1].day"), ("bad_value", S, 24, "capacity.reserved[1].start"),
        ("bad_value", S, 25, "capacity.reserved[1].hours"), ("bad_value", S, 28, "thresholds.wip_max"),
        ("bad_value", S, 29, "thresholds.unknown_deps_max"), ("bad_value", S, 30, "thresholds.unplanned_max"),
        ("bad_value", S, 31, "thresholds.share_gap_points"), ("bad_value", S, 32, "thresholds.neglect_weeks"),
        ("bad_value", S, 33, "thresholds.effort_window_weeks")],
    "bad_value_type.md": [
        ("bad_value", S, 16, "site.harbor.serves"), ("bad_value", S, 19, "capacity.attention_hours"),
        ("capacity_missing", S, 19, "capacity.attention_hours"), ("bad_value", S, 20, "capacity.agent_lanes"),
        ("capacity_missing", S, 20, "capacity.agent_lanes"), ("bad_value", S, 21, "capacity.block_min_hours"),
        ("bad_value", S, 22, "capacity.block_default_hours"), ("bad_value", S, 23, "capacity.block_start_cost_hours"),
        ("bad_value", S, 24, "capacity.attention_mapping"), ("bad_value", S, 27, "capacity.reserved[1].day"),
        ("bad_value", S, 28, "capacity.reserved[1].start"), ("bad_value", S, 29, "capacity.reserved[1].hours"),
        ("bad_value", S, 30, "capacity.reserved[1].label"), ("bad_value", S, 33, "milestone.project"),
        ("bad_value", S, 34, "milestone.issue"), ("bad_value", S, 35, "milestone.label"),
        ("bad_value", S, 38, "thresholds.wip_max"), ("bad_value", S, 39, "thresholds.unknown_deps_max"),
        ("bad_value", S, 40, "thresholds.unplanned_max"), ("bad_value", S, 41, "thresholds.share_gap_points"),
        ("bad_value", S, 42, "thresholds.neglect_weeks"), ("bad_value", S, 43, "thresholds.effort_window_weeks")],
    "bad_version.md": [("bad_version", S, 13, "portfolio_version")],
    "br_cycle.md": [("refines_cycle", BRS, 7, None)],
    "br_duplicate.md": [("duplicate_id", BRS, 8, None)],
    "br_malformed.md": [("malformed_item", BRS, 8, None)],
    "br_misplaced.md": [("misplaced", BRS, 8, None)],
    "br_undefined.md": [("undefined_link", BRS, 7, None)],
    "capacity_missing.md": [("capacity_missing", S, None, "capacity.attention_hours"),
                            ("capacity_missing", S, None, "capacity.agent_lanes")],
    "duplicate_allocation_date.md": [("duplicate_allocation_date", S, 23, "allocation[2].from")],
    "duplicate_block.md": [("duplicate_section", S, 19, None)],
    "duplicate_section.md": [("duplicate_section", BRS, 20, None)],
    "fences.md": [],
    "milestone_no_leaf.md": [("milestone_no_leaf", S, 21, "milestone.issue")],
    "misplaced_priority.md": [("misplaced_priority", S, 20, "site.harbor.serves[1]"),
                              ("misplaced_priority", S, 20, "site.harbor.serves[2]")],
    "no_br_section.md": [("no_br_section", BRS, None, None)],
    "no_settings.md": [("no_settings", S, None, None)],
    "not_utf8.md": [("portfolio_unreadable", None, None, None)],
    "reserved_exceeds_capacity.md": [("reserved_exceeds_capacity", S, 19, "capacity.reserved")],
    "settings_invalid.md": [("settings_invalid", S, 18, None)],
    "undated_allocation.md": [("undated_allocation", S, 18, "allocation[1].from"),
                              ("undated_allocation", S, 22, "allocation[2].from"),
                              ("undated_allocation", S, 26, "allocation[3].from")],
    "undefined_priority.md": [("undefined_priority", S, 20, "site.harbor.serves[2]")],
    "unknown_key.md": [("unknown_key", S, 14, "colour"), ("unknown_key", S, 19, "capacity.agent_lane"),
                       ("unknown_key", S, 23, "site.harbor.serve")],
    "unknown_project.md": [("unknown_project", S, 21, "allocation[1].shares.nowhere"),
                           ("unknown_project", S, 21, "allocation[1].shares.Harbor"),
                           ("unknown_project", S, 23, "site.nowhere"), ("unknown_project", S, 27, "milestone.project")],
}
# The two check_portfolio kinds need the installation: the sample sites, and for
# the milestone each site's issue: values.
CHECKED = {"unknown_project.md": None, "milestone_no_leaf.md": {BUSY: {"Issue-1", "Issue-2"}}}
CARRIED_FIXTURE_KINDS = {"malformed_item", "duplicate_id", "misplaced", "undefined_link", "refines_cycle"}


def every_entry(folder, name):
    p = read_portfolio_file(directory_roots([folder]), folder / name, br_namespace=NS)
    checked = check_portfolio(p, SITES, CHECKED[name]) if name in CHECKED else []
    return p, [*p.report, *checked]


def test_every_kind_has_a_fixture():
    assert sorted(EVERY_FIXTURE) == sorted(os.listdir(FIXTURES))
    covered = set()
    for name, expected in EVERY_FIXTURE.items():
        _, got = every_entry(FIXTURES, name)
        assert [(e.kind, e.section, e.line, e.key_path) for e in got] == expected, name
        covered |= {e.kind for e in got}
    assert covered == set(REPORT_KINDS) | CARRIED_FIXTURE_KINDS
    assert CARRIED_FIXTURE_KINDS <= set(CARRIED_KINDS)


def test_no_entry_holds_a_path(tmp_path):
    copy = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, copy)
    for name in sorted(os.listdir(copy)):
        p, got = every_entry(copy, name)
        for entry in [*got, *p.brs.report]:
            for value in fields_text(entry):
                assert str(tmp_path) not in value and str(FIXTURES) not in value, (name, value)
                assert "\n" not in value and "\r" not in value, (name, value)


def test_format_entry():
    p = read("bad_value_type.md")
    lines = [format_entry(e) for e in p.report]
    assert lines[0] == "line 16: bad_value: site.harbor.serves: must be an array of business requirement IDs, " \
                       "not a string; it is empty"
    assert lines[2] == "line 19: capacity_missing (advisory): capacity.attention_hours: no capacity.attention_hours, " \
                       "so the week planner has no capacity to plan into"
    assert format_entry(read("no_settings.md").report[0]).startswith("no_settings: no ```toml block")


# --- the sample portfolio file -------------------------------------------------


def sample():
    config = load_gantry_config(GANTRY_SAMPLE)
    return config, read_portfolio_file(config_roots(config), config.portfolio_file, br_namespace=config.requirements.br_namespace)


def test_sample_reads_clean():
    config, p = sample()
    assert config.portfolio_file.name == "portfolio.md"
    assert p.report == [] and p.settings_read is True
    assert list(p.brs.requirements) == ["life:BR-1", "life:BR-2", "life:BR-3"]
    assert p.serves[BUSY] == [ReqId("life", "BR", 1), ReqId("life", "BR", 2)]
    assert p.serves[OWN_UNIT] == [ReqId("life", "BR", 3)]
    assert UNESTIMATED not in p.serves and any(UNESTIMATED in a.shares for a in p.allocations)
    assert SMALL not in p.serves and not any(SMALL in a.shares for a in p.allocations)
    c = p.capacity
    assert c.attention_mapping == (0, 0.15, 0.3, 0.6, 1.0)
    assert set(c.attention_hours) == {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
    assert (c.agent_lanes, c.block_min_hours, c.block_default_hours, c.block_start_cost_hours) == (2, 1, 2, 0.25)
    assert [(r.day, r.start, r.hours, r.label) for r in c.reserved] == [("wed", "09:00", 2, "Standing meeting")]
    assert p.thresholds == pf.Thresholds(3, 0, 0, 10, 4, 4)
    assert (p.milestone.project, p.milestone.issue, p.milestone.label) == (SMALL, "Issue-5", "keeper's door repainted")


def test_sample_checks_clean():
    # Each site's issue: values come from test_samples.wbs(), the sample tree's
    # own reader; #12's sweep of that helper should find this use.
    _, p = sample()
    issues = {site: {n.meta["issue"] for n in issue_leaves(wbs(site))} for site in READABLE}
    issues |= {site: None for site in NOT_BUILT}
    assert check_portfolio(p, SITES, issues) == []
    (leaf,) = [n for n in issue_leaves(wbs(p.milestone.project)) if n.meta["issue"] == p.milestone.issue]
    assert leaf.is_open


def test_sample_intended_shares():
    config, p = sample()
    assert config.as_of == date(2026, 1, 20)
    assert intended_shares(p, config.as_of).from_date == date(2026, 1, 5)
    assert intended_shares(p, date(2026, 3, 2)).from_date == date(2026, 3, 2)
    assert intended_shares(p, date(2026, 6, 1)).from_date == date(2026, 3, 2)
    assert intended_shares(p, date(2025, 11, 30)) is None


def test_sample_roles_on_readable_sites():
    _, p = sample()
    assert {name for name, _, _ in p.project_refs} == READABLE


# --- the documentation ---------------------------------------------------------

PORTFOLIO_DOC = ROOT / "docs" / "portfolio-file.md"
CONFIG_DOC = ROOT / "docs" / "config.md"
DOC_EXAMPLE_INFO = "markdown portfolio-file"


def _doc_default(default):
    """A registry default as the docs table writes it (typed here, not imported)."""
    if default == "required":
        return "required"
    if default is None:
        return "not set"
    if default == ():
        return "empty"
    if isinstance(default, str):
        return f'`"{default}"`'
    return f"`{default}`"


def doc_text(path):
    return path.read_bytes().decode("utf-8")


def test_doc_rows_match_registry():
    (table,) = [t for t in md_tables(doc_text(PORTFOLIO_DOC)) if t.heading == "Settings keys"]
    assert table.header == ["Key", "Type and rule", "Default", "Read by"]
    rows = {row[0].strip("`"): row for row in table.rows}
    assert len(rows) == len(table.rows) == len(SETTINGS_KEYS) > 0
    assert set(rows) == {k.path for k in SETTINGS_KEYS}
    for k in SETTINGS_KEYS:
        # A cell may go on after its value ("`2`, or `block_min_hours` when that is larger").
        assert rows[k.path][2].split(",")[0] == _doc_default(k.default), k.path
        assert rows[k.path][3] == k.read_by, k.path


def test_attention_mapping_row_points_to_week_plan():
    (table,) = [t for t in md_tables(doc_text(PORTFOLIO_DOC)) if t.heading == "Settings keys"]
    (row,) = [row for row in table.rows if row[0] == "`capacity.attention_mapping`"]
    assert "week-plan.md#attention-need" in row[1]
    assert row[2] == "not set"
    assert "week-plan.md says what Gantry does with one that is not finite" in " ".join(doc_text(PORTFOLIO_DOC).split())


def test_portfolio_doc_states_rules():
    doc = " ".join(doc_text(PORTFOLIO_DOC).split())
    wanted = [f"`{kind}`" for kind in REPORT_KINDS + CARRIED_KINDS] + [
        "never belongs in a public repo", "[site.<project>]", "[site.<name>]", "inside a fence",
        "or an HTML comment is never a heading", "outside the business-requirements section with no",
        "gantry portfolio --config", "python -m gantry portfolio --config", "## Default changes", "None yet.",
        "as-of date", "allocation[2].shares.beta", "capacity.attention_mapping", MILESTONE_NOTE_TEXT,
        "a finite number", "advisory", "DEFAULTS", "its capacity settings, for the week plan",
        "`block_min_hours` when that is larger",
    ]
    assert [w for w in wanted if w not in doc] == []


def doc_blocks(text):
    """(info, body) of every fenced block, paired by #15's rule."""
    lines = text.split("\n")
    return [(FENCE_RE.fullmatch(lines[i])["info"].strip(), "\n".join(lines[i + 1 : j]) + "\n")
            for i, j in fence_spans(lines)]


def test_portfolio_doc_example_reads_clean(tmp_path):
    examples = [body for info, body in doc_blocks(doc_text(PORTFOLIO_DOC)) if info == DOC_EXAMPLE_INFO]
    assert len(examples) >= 1
    for n, body in enumerate(examples):
        p = write(tmp_path, body, name=f"example-{n}.md")
        assert p.report == [], body
        assert p.settings_read and p.brs.requirements


def test_config_doc_portfolio_row():
    doc = doc_text(CONFIG_DOC)
    row = next(line for line in doc.splitlines() if line.startswith("| `portfolio_file` |"))
    assert "portfolio-file.md" in row and "gantry portfolio" in row
    assert "its capacity settings, for the week plan" in row and "#23 adds the rest" in row
    assert "#23 or " + "#61" not in doc and "only its business " + "requirements" not in doc
    assert "gantry portfolio --config <file>" in doc
