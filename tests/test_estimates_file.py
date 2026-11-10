"""The estimates file: found per site and read (#10)."""

import shutil
from datetime import date
from types import SimpleNamespace

import pytest

from gantry.config import SiteOverride
from gantry.estimates_file import find_estimates_file, read_estimates_file
from gantry.roots import directory_roots
from gantry.sites import SiteEntry
from sample_roles import BUSY
from test_samples import ROOT, SAMPLES, decode_bytes, git, git_work_tree, needs_git
from helpers_config import roots_around

INPUTS = ROOT / "tests" / "fixtures" / "inputs"
NOT_UTF8 = INPUTS / "estimates-not-utf8.md"
ESTIMATES = INPUTS / "estimates.md"


def site_dir(tmp_path, *names, source=ESTIMATES):
    """A planning directory holding a copy of `source` under each name."""
    folder = tmp_path / "plan"
    folder.mkdir(exist_ok=True)
    for name in names:
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return folder


def find(folder, estimates=None):
    config = SimpleNamespace(sites={} if estimates is None else {"alpha": SiteOverride(None, estimates)})
    return find_estimates_file(directory_roots([folder]), SiteEntry("alpha", folder, None), config)


def kinds(report):
    return [e.kind for e in report]


def read(path, name=None):
    return read_estimates_file(roots_around(path), path, "alpha", name or path.name)


# --- discovery ---------------------------------------------------------------


def test_later_dated_file_read_other_ignored(tmp_path):
    folder = site_dir(tmp_path, "estimates-2026-01-10.md", "estimates-2026-01-20-late.md")
    found = find(folder)
    assert (found.name, found.date) == ("estimates-2026-01-20-late.md", date(2026, 1, 20))
    assert found.path == folder / "estimates-2026-01-20-late.md"
    assert [(e.kind, e.message) for e in found.report] == [(
        "estimates_ignored",
        "estimates file 'estimates-2026-01-10.md' is ignored: 'estimates-2026-01-20-late.md' is later",
    )]


def test_same_date_last_name_read(tmp_path):
    folder = site_dir(tmp_path, "estimates-2026-01-10-b.md", "estimates-2026-01-10-a.md")
    found = find(folder)
    assert found.name == "estimates-2026-01-10-b.md"
    assert [e.message for e in found.report] == [
        "estimates file 'estimates-2026-01-10-a.md' is ignored: "
        "'estimates-2026-01-10-b.md' has the same date and sorts later",
    ]


def test_undated_listed_ignored(tmp_path):
    folder = site_dir(tmp_path, "estimates-latest.md", "estimates-2026-02-30.md", "estimates-2026-01-01.md")
    found = find(folder)
    assert found.name == "estimates-2026-01-01.md"
    assert [e.message for e in found.report] == [
        "estimates file 'estimates-2026-02-30.md' is ignored: its name has no date",
        "estimates file 'estimates-latest.md' is ignored: its name has no date",
    ]


def test_only_undated_is_no_file(tmp_path):
    found = find(site_dir(tmp_path, "estimates-latest.md"))
    assert found.path is None
    assert kinds(found.report) == ["estimates_ignored", "no_estimates_file"]


def test_names_compared_case_sensitively(tmp_path):
    found = find(site_dir(tmp_path, "Estimates-2026-01-01.md", "estimates-2026-01-01.MD"))
    assert found.path is None
    assert [e.message for e in found.report] == ["no estimates file"]


def test_top_level_only(tmp_path):
    folder = site_dir(tmp_path, "estimates-2026-01-10.md", "old/estimates-2026-09-09.md")
    (folder / "estimates-2026-12-12.md").mkdir()
    found = find(folder)
    assert found.name == "estimates-2026-01-10.md"
    assert found.report == ()


def test_no_file(tmp_path):
    found = find(site_dir(tmp_path))
    assert (found.path, found.name, found.date) == (None, None, None)
    assert [(e.kind, e.line, e.message) for e in found.report] == [("no_estimates_file", None, "no estimates file")]


def test_configured_overrides_discovery(tmp_path):
    folder = site_dir(tmp_path, "my-estimates.md", "estimates-2026-01-10.md")
    found = find(folder, folder / "my-estimates.md")
    assert (found.path, found.name, found.date) == (folder / "my-estimates.md", "my-estimates.md", None)
    assert found.report == ()


def test_configured_dated_name_keeps_its_date(tmp_path):
    folder = site_dir(tmp_path, "estimates-2026-01-10.md")
    assert find(folder, folder / "estimates-2026-01-10.md").date == date(2026, 1, 10)


def test_false_turns_discovery_off(tmp_path):
    found = find(site_dir(tmp_path, "estimates-2026-01-10.md"), False)
    assert found.path is None
    assert [(e.kind, e.message) for e in found.report] == [
        ("no_estimates_file", "no estimates file: site.alpha.estimates is false"),
    ]


def test_configured_missing_is_unreadable_no_absolute_path(tmp_path):
    folder = site_dir(tmp_path, "estimates-2026-01-10.md")
    found = find(folder, folder / "gone.md")
    assert found.path is None
    assert [(e.kind, e.message) for e in found.report] == [
        ("estimates_unreadable", "site.alpha.estimates: the file does not exist"),
    ]
    for entry in found.report:
        assert str(tmp_path) not in entry.message
        assert str(tmp_path.anchor) not in entry.message


# --- reading -----------------------------------------------------------------


def test_not_utf8_is_unreadable(tmp_path):
    got = read(NOT_UTF8)
    at = NOT_UTF8.read_bytes().index(b"\xe9")
    assert got.rows == {}
    assert [(e.kind, e.message) for e in got.report] == [
        ("estimates_unreadable", f"estimates file 'estimates-not-utf8.md' is not valid UTF-8 at byte {at}"),
    ]


def test_cannot_open_is_unreadable(tmp_path):
    got = read(tmp_path, "estimates-2026-01-01.md")  # a directory
    assert got.rows == {}
    assert [e.kind for e in got.report] == ["estimates_unreadable"]
    message = got.report[0].message
    assert message.startswith("estimates file 'estimates-2026-01-01.md' cannot be read: ")
    assert str(tmp_path) not in message


def test_row_joins_issue():
    rows = read(ESTIMATES).rows
    assert {"Issue-1", "Issue-2", "Issue-4", "Issue-13", "Issue-99"} <= set(rows)
    assert "#2" not in rows and "2" not in rows


def test_range_and_single_number_hours():
    rows = read(ESTIMATES).rows
    assert (rows["Issue-5"].lo_h, rows["Issue-5"].hi_h) == (2, 4)
    assert (rows["Issue-20"].lo_h, rows["Issue-20"].hi_h) == (5, 5)


def test_p_and_tier_columns_not_read():
    rows = read(ESTIMATES).rows
    assert (rows["Issue-20"].thinking, rows["Issue-20"].checking) == ("opus", "opus")
    assert (rows["Issue-22"].effort, rows["Issue-22"].flags) == ("S", "spike")
    assert rows["Issue-23"].flags is None
    for row in rows.values():
        assert not hasattr(row, "P") and not hasattr(row, "tier") and not hasattr(row, "priority")


def test_duplicate_row_reported_first_used():
    got = read(ESTIMATES)
    assert (got.rows["Issue-2"].lo_h, got.rows["Issue-2"].effort) == (10, "M")
    dup = [e for e in got.report if e.kind == "estimates_row_duplicate"]
    lines = ESTIMATES.read_text(encoding="utf-8").split("\n")
    first, second = [i for i, line in enumerate(lines, 1) if line.startswith("| #2 |")]
    assert [e.message for e in dup] == [
        f"estimates file 'estimates.md' line {second}: a second row for #2; the row on line {first} is used",
    ]
    assert kinds(got.report) == ["estimates_row_duplicate"]


def test_missing_column_and_bad_cell_reported():
    path = INPUTS / "estimates-missing-column.md"
    got = read(path)
    assert kinds(got.report) == ["estimates_column_missing", "estimates_cell_bad"]
    assert got.report[0].message == (
        "estimates file 'estimates-missing-column.md': a table has no 'checking' column (first on line 3)"
    )
    assert got.report[1].message == (
        "estimates file 'estimates-missing-column.md' line 5: hours 'abc' is not a number or a range "
        "above 0; #2 gives no hours"
    )
    assert (got.rows["Issue-2"].lo_h, got.rows["Issue-2"].thinking) == (None, "opus")
    assert got.rows["Issue-4"].checking is None
    assert (got.rows["Issue-5"].lo_h, got.rows["Issue-5"].hi_h) == (2, 4)


@pytest.mark.parametrize("cell", ["", "0", "0-2", "4-2", "2-", "-2", "1e3", "2 - 4", "9" * 12, "nan"])
def test_bad_hours_cells(tmp_path, cell):
    path = tmp_path / "e.md"
    path.write_bytes(f"| # | effort | hours | thinking | checking | flags |\n| #3 | S | {cell} | | | |\n".encode())
    got = read(path)
    assert kinds(got.report) == ["estimates_cell_bad"]
    assert got.rows["Issue-3"].lo_h is None


def test_bom_crlf_and_short_row(tmp_path):
    path = tmp_path / "e.md"
    path.write_bytes(b"\xef\xbb\xbf| # | effort | hours | thinking | checking | flags |\r\n| #3 | S | 1.5-2.5 |\r\n")
    got = read(path)
    row = got.rows["Issue-3"]
    assert (row.lo_h, row.hi_h, row.effort, row.thinking, row.line) == (1.5, 2.5, "S", None, 2)
    assert got.report == ()


def test_header_ends_at_a_non_table_line(tmp_path):
    path = tmp_path / "e.md"
    path.write_bytes(b"| # | effort | hours | thinking | checking | flags |\n\n| #3 | S | 1 | | | |\n")
    assert read(path).rows == {}


def test_reads_harbor_sample_file():
    path = next((SAMPLES / BUSY).glob("estimates-*.md"))
    got = read(path)
    assert got.report == ()
    assert {"Issue-1", "Issue-12", "Issue-15"} <= set(got.rows)  # one row from each table
    assert (got.rows["Issue-15"].thinking, got.rows["Issue-15"].flags) == ("opus", "research spike")


def test_cell_keeps_line_separator(tmp_path):
    # U+2028 is a line break to str.splitlines(), not to the reader (U2).
    path = tmp_path / "e.md"
    text = "| # | effort | hours | thinking | checking | flags |\n| #3 | a\u2028b | 1 | x\u2029y | | |\n"
    path.write_bytes(text.encode("utf-8"))
    row = read(path).rows["Issue-3"]
    assert (row.effort, row.thinking) == ("a\u2028b", "x\u2029y")


def test_estimates_not_utf8_stays_invalid():
    data = NOT_UTF8.read_bytes()
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")


@needs_git
def test_estimates_not_utf8_is_not_text():
    if not git_work_tree(ROOT):
        pytest.skip("the gantry root is not a git work tree")
    result = git(ROOT, "check-attr", "text", "--", "tests/fixtures/inputs/estimates-not-utf8.md")
    assert result.returncode == 0, decode_bytes(result.stderr)
    assert decode_bytes(result.stdout).strip().endswith("text: unset")
