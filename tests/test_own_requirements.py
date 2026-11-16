"""Gantry's own requirements file (#16) reads clean in its own namespace.

docs/requirements-format.md states the format.
"""

import functools
import re
from collections import Counter

from gantry.config import ID_TOKEN_RE
from gantry.requirements import DEFAULT_BR_NAMESPACE, LAYERS, PROJECT_LAYERS, read_requirements
from test_samples import ROOT
from helpers_config import roots_around

DOC = ROOT / "docs" / "requirements.md"
FORMAT_DOC = ROOT / "docs" / "requirements-format.md"
NAMESPACE = "gantry"
# A business-requirement ID, bare or qualified, anywhere in the text (fences included).
BR_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])BR-[0-9]")
# Longest code first, so BRule is never read as BR.
QUALIFIED_RE = re.compile(
    rf"(?P<namespace>{ID_TOKEN_RE.pattern}):(?:{'|'.join(sorted(LAYERS, key=len, reverse=True))})-[0-9]"
)
# Meta-block keys the format defines; a space-less `{refines:SR-2}` must not read as a namespace.
META_KEYS = {"needs", "refines"}
# The source-of-truth claims (AC5), one anchor phrase each, matched on the normalised header.
SOT_PHRASES = ("derived once", "changes with the code", "not re-derived", "never renumbered or reused")
# The ID rule (#161), one anchor phrase per claim: the tool; the claim in the issue;
# who renumbers; what they update; the qualifier; run once; the claim's form; the author
# filter is on who opened the issue or pull request, and what that leaves uncounted.
RULE_PHRASES = ("tools/next_requirement_id.py", "in the issue's body", "merges second renumbers",
                "every reference on its branch", "once they are on `main`", "run the tool once",
                "set off by a space or punctuation", "a member or a collaborator opened them",
                "anyone else opened is not counted")

PLANTED = (
    "See life:BR-3 and harbor:FR-1.\n"
    "```text\n- BR-2: inside a fence is still found\n```\n"
    "Not found: gantry:FR-1, BRule-1, devagent:Issue-726, https://example.org/FR-1.\n"
    "Meta keys are not namespaces: {refines:SR-2} {needs:[FR-1]}\n"
)


@functools.cache
def own_set():
    return read_requirements(roots_around(DOC), DOC, NAMESPACE, layers=PROJECT_LAYERS, br_namespace=DEFAULT_BR_NAMESPACE)


@functools.cache
def own_text():
    assert DOC.is_file(), "docs/requirements.md is missing"
    return DOC.read_bytes().decode("utf-8")


def br_tokens(text):
    return BR_TOKEN_RE.findall(text)


def foreign_namespaces(text):
    return {m["namespace"] for m in QUALIFIED_RE.finditer(text)} - {NAMESPACE} - META_KEYS


def header(text):
    return re.split(r"(?m)^## ", text, maxsplit=1)[0]


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
    # Exactly the five anti-goals (#16); a change that adds a constraint updates this count.
    assert counts["CON"] == 5, f"CON: {counts['CON']}"


def test_every_use_case_needs_something():
    ucs = [r for r in own_set().requirements.values() if r.layer == "UC"]
    assert ucs, "no use cases"
    assert [str(r.id) for r in ucs if not r.needs] == []


def test_no_business_requirement_ids():
    assert br_tokens(own_text()) == []


def test_qualified_ids_are_gantry_only():
    assert foreign_namespaces(own_text()) == set()


def test_header_links_the_format_doc():
    assert FORMAT_DOC.is_file()
    assert re.search(rf"\]\({re.escape(FORMAT_DOC.name)}[#)]", header(own_text()))


def test_header_states_source_of_truth():
    text = normalised(header(own_text()))
    assert [p for p in SOT_PHRASES if p not in text] == []


def test_header_states_the_id_rule():
    text = normalised(header(own_text()))
    assert [p for p in RULE_PHRASES if p not in text] == []


def test_header_holds_no_id():
    # tools/next_requirement_id.py counts every mention in this file, so an
    # example number in the header would be skipped for good.
    from next_requirement_id import CODES, mentions

    head = header(own_text())
    assert [(code, n) for code in CODES for n in mentions(head, code)] == []


def test_scanners_find_planted_tokens():
    assert br_tokens(PLANTED) == ["BR-3", "BR-2"]
    assert foreign_namespaces(PLANTED) == {"life", "harbor"}
    assert header("# T\n[f](requirements-format.md)\n## S\n[g](x.md)\n") == "# T\n[f](requirements-format.md)\n"
