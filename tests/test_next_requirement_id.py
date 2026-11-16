"""tools/next_requirement_id.py: the next free requirement ID for a layer (#161).

IDs are built at run time from a number `k` the test picks, so no test pins
the live file's highest number. Every subprocess the tool would run goes
through a fake runner that raises on an argv it was not given, so no test
reaches the network; the read-only tests run real git against a local bare
remote they build, with a fake gh.
"""

import re
import string
import time

import pytest

import next_requirement_id as nri
from gantry.requirements import DEFAULT_BR_NAMESPACE, ID_RE, PROJECT_LAYERS, parse_id, read_requirements
from helpers_config import roots_around
from test_samples import ROOT

K = 731
KS = (1, K)
NOT_COUNTED = ("CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "MANNEQUIN", "NONE")


def main_src(text):
    return nri.Source("main", None, None, text)


def tree_src(text):
    return nri.Source("tree", None, None, text)


def issue(number, association, text):
    return nri.Source("issue", number, association, text)


def pull(number, association, text):
    return nri.Source("pull", number, association, text)


def next_id(code, sources):
    n, source = nri.choose(code, sources)
    return nri.format_id(code, n + 1), source


# --- counting (AC Counting) ---------------------------------------------------------


@pytest.mark.parametrize("k", KS)
def test_counting_ac(k):
    pr = 12
    sources = [
        main_src(f"- FR-{k}: main's highest\n"),
        issue(3, "OWNER", f"Claims FR-{k + 1}.\n"),
        issue(5, "CONTRIBUTOR", f"Claims FR-{k + 50}.\n"),
        pull(pr, "MEMBER", f"- gantry:FR-{k + 2}: the pull request's\n"),
        issue(6, "OWNER", f"harbor:FR-{k + 9} FR-01 NFR-{k + 5} FR-{10**9 + k}\n"),
    ]
    got, source = next_id("FR", sources)
    assert got == f"FR-{k + 3}"
    assert source.kind == "pull"
    assert source.label == f"open pull request #{pr}"


NON_COUNTING = [
    pytest.param(issue(4, "OWNER", f"harbor:FR-{K + 1}"), id="other-namespace"),
    pytest.param(issue(4, "OWNER", "FR-01"), id="leading-zero"),
    pytest.param(issue(4, "OWNER", f"NFR-{K + 5}"), id="inside-NFR"),
    pytest.param(issue(4, "OWNER", f"FR-{10**9 + K}"), id="ten-digit-run"),
] + [pytest.param(issue(4, assoc, f"FR-{K + 1}"), id=assoc) for assoc in NOT_COUNTED]


@pytest.mark.parametrize("noise", NON_COUNTING)
def test_non_counting_mention_alone(noise):
    got, source = next_id("FR", [main_src(f"- FR-{K}: x\n"), noise])
    assert got == f"FR-{K + 1}"
    assert source.label == "origin/main"


@pytest.mark.parametrize("assoc", ["OWNER", "MEMBER", "COLLABORATOR"])
def test_counted_associations(assoc):
    got, source = next_id("FR", [main_src(f"- FR-{K}: x\n"), issue(4, assoc, f"FR-{K + 1}")])
    assert got == f"FR-{K + 2}"
    assert source.label == "open issue #4"


N = 731
SHAPES = [
    (f"- FR-{N}: x", [N]),
    (f"(FR-{N})", [N]),
    (f"[FR-{N},", [N]),
    (f"**FR-{N}**", [N]),
    (f"`FR-{N}`", [N]),
    (f"FR-{N}.", [N]),
    (f"FR-{N}:", [N]),
    (f"gantry:FR-{N}", [N]),
    (f"FR-{N}\r\n", [N]),
    (f"x-FR-{N}", [N]),
    (f"_FR-{N}_", [N]),
    (f"__FR-{N}__", [N]),
    (f"e.g.FR-{N}", [N]),
    (f"_gantry:FR-{N}_", [N]),
    ("FR-3-FR-5", [3, 5]),
    (f"FR-{N}a", []),
    (f"xgantry:FR-{N}", []),
    (f"x-gantry:FR-{N}", []),
    ("{refines:FR-2}", []),
]


@pytest.mark.parametrize("text, counted", SHAPES)
def test_counting_shapes(text, counted):
    assert nri.mentions(text, "FR") == counted


def test_left_boundary_counts_punctuation():
    for c in map(chr, range(32, 127)):
        assert bool(nri.mentions(c + "FR-5", "FR")) == (c != ":" and not c.isalnum()), repr(c)


# B1's one-regex form (imPlan Improvements): the tool's scan must agree with it
# on every shape, while staying linear where this form is not.
GRAMMAR_FORM = re.compile(rf"(?<![A-Za-z0-9:])(?:{ID_RE.pattern})(?![A-Za-z0-9])")


@pytest.mark.parametrize("text", [text for text, _ in SHAPES] + [
    "FR-1-gantry:FR-5", "a.b:FR-2 gantry:FR-3", "-gantry:FR-4", ".gantry:FR-6", "gantry:gantry:FR-7",
])
def test_scan_agrees_with_the_grammar_form(text):
    want = [int(m["number"]) for m in GRAMMAR_FORM.finditer(text)
            if m["code"] == "FR" and m["namespace"] in (None, "gantry")]
    assert nri.mentions(text, "FR") == want


TIES = [
    pytest.param(main_src, tree_src, "origin/main", id="main-over-tree"),
    pytest.param(tree_src, lambda t: issue(1, "OWNER", t), "the working tree", id="tree-over-issue"),
    pytest.param(lambda t: issue(9, "OWNER", t), lambda t: pull(1, "OWNER", t), "open issue #9", id="issue-over-pull"),
    pytest.param(lambda t: issue(4, "OWNER", t), lambda t: issue(9, "OWNER", t), "open issue #4", id="issue-4-over-9"),
    pytest.param(lambda t: pull(3, "MEMBER", t), lambda t: pull(8, "MEMBER", t), "open pull request #3",
                 id="pull-3-over-8"),
]


@pytest.mark.parametrize("winner, loser, label", TIES)
def test_tie_order(winner, loser, label):
    text = f"FR-{K}"
    got, source = next_id("FR", [loser(text), winner(text)])  # given in reverse
    assert got == f"FR-{K + 1}"
    assert source.label == label


def test_no_number_gives_one():
    sources = [main_src("- UC-3: x\n"), tree_src("")]
    assert nri.choose("FR", sources) == (0, None)
    assert nri.format_id("FR", 1) == "FR-1"


def test_nine_digit_limit():
    assert nri.format_id("FR", 999999999) == "FR-999999999"
    with pytest.raises(nri.TooLong):
        nri.format_id("FR", 10**9)


@pytest.mark.parametrize("code", nri.CODES)
def test_result_is_an_id(code):
    for n in (1, 999999999):
        assert parse_id(nri.format_id(code, n), nri.NAMESPACE) is not None


def test_scanner_finds_every_defined_id():
    doc = ROOT / "docs" / "requirements.md"
    defined = read_requirements(roots_around(doc), doc, nri.NAMESPACE, layers=PROJECT_LAYERS,
                                br_namespace=DEFAULT_BR_NAMESPACE).requirements.values()
    assert len(defined) >= 20
    text = doc.read_bytes().decode("utf-8")
    found = {(code, n) for code in nri.CODES for n in nri.mentions(text, code)}
    assert {(r.id.code, r.id.number) for r in defined} <= found


def test_scan_is_linear_on_hostile_text():
    size = 1_000_000
    hostile = [
        string.ascii_letters * (size // 52),
        "a:" * (size // 2),
        "FR-" * (size // 3),
        "FR-" + "1" * size,
        "a-" * (size // 2),
        "_a." * (size // 3),
    ]
    start = time.perf_counter()
    for text in hostile:
        nri.mentions(text, "FR")
    assert time.perf_counter() - start < 2.0
