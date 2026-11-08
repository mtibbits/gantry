"""The portfolio file (#22).

docs/portfolio-file.md states the rules these tests pin. Every line number
asserted here is a literal read off its fixture, never computed from it.
"""

import shutil
import time
from pathlib import Path

import pytest

import gantry.portfolio_file as pf
from gantry.portfolio_file import PORTFOLIO_LABEL, read_portfolio_file
from test_samples import ROOT, decode_bytes, git, git_work_tree, needs_git

FIXTURES = Path(__file__).parent / "fixtures" / "portfolio_file"
NOT_UTF8 = FIXTURES / "not_utf8.md"
NOT_UTF8_AT = NOT_UTF8.read_bytes().index(b"\xe9")
NS = "life"
BRS = "business requirements"
SETTINGS = "settings"


def read(name):
    return read_portfolio_file(FIXTURES / name, br_namespace=NS)


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
    p = read_portfolio_file(path, br_namespace="life")
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
        ("not_utf8", f"the portfolio file is not valid UTF-8 at byte {NOT_UTF8.read_bytes().index(b'\xe9')}"),
    ],
)
def test_unreadable(tmp_path, case, message):
    p = read_portfolio_file(_unreadable(tmp_path, case), br_namespace=NS)
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
    assert e.message == "no '## Business requirements' heading; no business requirements are read"
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
        p = read_portfolio_file(tmp_path / name, br_namespace=NS)
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
        read_portfolio_file(tmp_path / name, br_namespace=NS)
    assert time.perf_counter() - start < 2
