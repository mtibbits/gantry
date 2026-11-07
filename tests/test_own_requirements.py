"""Gantry's own requirements file (#16) reads clean in its own namespace.

docs/requirements-format.md states the format.
"""

import re
from collections import Counter

from gantry.requirements import DEFAULT_BR_NAMESPACE, LAYERS, read_requirements
from test_samples import ROOT

DOC = ROOT / "docs" / "requirements.md"
FORMAT_DOC = ROOT / "docs" / "requirements-format.md"
OWN_LAYERS = set(LAYERS) - {"BR"}
# A business-requirement ID, bare or qualified, anywhere in the text (fences included).
BR_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])BR-[0-9]")
# Any qualified ID: a namespace token, a colon, a layer code, a dash, a digit.
QUALIFIED_RE = re.compile(
    r"(?P<namespace>[A-Za-z0-9_.-]+):(?:" + "|".join(sorted(LAYERS, key=len, reverse=True)) + r")-[0-9]"
)
LINK_RE = re.compile(r"\[[^\]]*\]\((?P<target>[^()\s#]+)(?:#[^()\s]*)?\)")
# Meta-block keys the format defines; a space-less `{refines:SR-2}` must not read as a namespace.
META_KEYS = {"needs", "refines"}
# The source-of-truth claims (AC5), one anchor phrase each, matched on the normalised header.
SOT_PHRASES = ("derived once", "changes with the code", "not re-derived", "never renumbered or reused")

PLANTED = (
    "See life:BR-3 and harbor:FR-1.\n"
    "```text\n- BR-2: inside a fence is still found\n```\n"
    "Not found: gantry:FR-1, BRule-1, devagent:Issue-726, https://example.org/FR-1.\n"
    "Meta keys are not namespaces: {refines:SR-2} {needs:[FR-1]}\n"
)


def own_set():
    return read_requirements(DOC, "gantry", layers=OWN_LAYERS, br_namespace=DEFAULT_BR_NAMESPACE)


def own_text():
    assert DOC.is_file(), "docs/requirements.md is missing"
    return DOC.read_bytes().decode("utf-8")


def br_tokens(text):
    return BR_TOKEN_RE.findall(text)


def foreign_namespaces(text):
    return {m["namespace"] for m in QUALIFIED_RE.finditer(text)} - {"gantry"} - META_KEYS


def header(text):
    return re.split(r"(?m)^## ", text, maxsplit=1)[0]


def header_targets(text):
    return [m["target"] for m in LINK_RE.finditer(header(text))]


def normalised(text):
    return " ".join(text.split()).casefold()


def test_own_requirements_read_clean():
    rs = own_set()
    assert rs.report == [], "\n".join(f"{e.kind}:{e.line}:{e.message}" for e in rs.report)


def test_layer_floors():
    counts = Counter(r.layer for r in own_set().requirements.values())
    for layer in ("SR", "FR", "QA", "EI"):
        assert counts[layer] >= 1, f"{layer}: {counts[layer]}"
    assert counts["UC"] >= 6, f"UC: {counts['UC']}"
    assert counts["CON"] == 5, f"CON: {counts['CON']}"


def test_every_use_case_needs_something():
    ucs = [r for r in own_set().requirements.values() if r.layer == "UC"]
    assert len(ucs) >= 6, f"only {len(ucs)} use cases"
    assert [str(r.id) for r in ucs if not r.needs] == []


def test_no_business_requirement_ids():
    assert br_tokens(own_text()) == []


def test_qualified_ids_are_gantry_only():
    assert foreign_namespaces(own_text()) == set()


def test_header_links_the_format_doc():
    targets = [(DOC.parent / t).resolve() for t in header_targets(own_text())]
    assert FORMAT_DOC.is_file()
    assert FORMAT_DOC.resolve() in targets, targets


def test_no_html_comments():
    # The reader reads an ID item inside a comment as live, so the rendered
    # page and the read set would disagree.
    assert "<!--" not in own_text()


def test_header_states_source_of_truth():
    text = normalised(header(own_text()))
    assert [p for p in SOT_PHRASES if p not in text] == []


def test_scanners_find_planted_tokens():
    assert br_tokens(PLANTED) == ["BR-3", "BR-2"]
    assert foreign_namespaces(PLANTED) == {"life", "harbor"}
    assert header_targets("# T\n[f](requirements-format.md#ids) [g](x.md)\n## S\n[h](y.md)") == [
        "requirements-format.md",
        "x.md",
    ]
