"""Placement checks over a written strategic.html (#23), shared by the tests
and the operator's run on a real installation (AC11).

It imports only the standard library and page_safety, so the one-liner in
docs/strategic-page.md runs it from a checkout with tests/ on sys.path.
placement_line and thieves_line print counts only: no project name, group,
share, issue, reason or text.
"""

from pathlib import Path

from page_safety import assert_page_safe


def page_data(page):
    """The page's embedded data, after the page-safety checks."""
    return assert_page_safe(page, [])


def _problems_by_project(data):
    """{project: [problem, ...]} for every project with a placement problem."""
    br, other = {}, {}
    for group in data["groups"]:
        for site in group["sites"]:
            (br if group["kind"] == "br" else other).setdefault(site, []).append(group["kind"])
    found = {}
    for project in data["projects"]:
        in_br, in_other = br.get(project, []), other.get(project, [])
        if in_br and in_other:
            found.setdefault(project, []).append(f"{project!r} is in a requirement group and in {in_other[0]}")
        elif not in_br and len(in_other) != 1:
            found.setdefault(project, []).append(f"{project!r} is in {len(in_other)} non-requirement groups")
    for panel in data["panels"]:
        if panel["status"] == "not_built" and not panel["reason"]:
            found.setdefault(panel["site"], []).append(f"{panel['site']!r} is not built and gives no reason")
    return found


def placement_problems(data) -> list[str]:
    """Each project is in one or more requirement groups and no other, or in
    exactly one other group; each not-built panel gives a reason."""
    return [problem for problems in _problems_by_project(data).values() for problem in problems]


def placement_line(path) -> str:
    """`N configured projects, M placed` for a written strategic.html."""
    data = page_data(Path(path).read_bytes().decode("utf-8"))
    projects = data["projects"]
    unplaced = set(_problems_by_project(data))
    return f"{len(projects)} configured projects, {sum(p not in unplaced for p in projects)} placed"


def _count(values):
    return sum(v for v in values if v is not None)


def thieves_line(path) -> str:
    """The time thieves (#29) of a written strategic.html, counts only, from
    the embedded data's structured fields (never its prose)."""
    data = page_data(Path(path).read_bytes().decode("utf-8"))
    built = [p for p in data["panels"] if p["status"] == "built" and p["thieves"] is not None]
    neglect = [p["thieves"]["neglect"] for p in built]
    wip = [dict(p["thieves"]["wip"]["counts"]) for p in built]
    flagged = ", ".join(f"{name} {sum(p['thieves'][kind]['flagged'] for p in built)}" for kind, name in (
        ("wip", "wip"), ("unknown_deps", "unknown-deps"), ("unplanned", "unplanned"), ("priorities", "priorities"),
        ("neglect", "neglect")))
    return (f"{len(built)} built sites; neglect from history: {sum(n['no_data'] is None for n in neglect)}; "
            f"neglect no data: {sum(n['no_data'] is not None for n in neglect)}; flagged: {flagged}; "
            f"parked not counted: {_count(c.get('parked') for c in wip)}; "
            f"started with a pending glyph: {_count(c.get('started') for c in wip)}; "
            f"parked/started no data: {sum(c.get('parked') is None for c in wip)}; "
            f"uncommitted WBS.md: {sum(p['uncommitted'] is True for p in built)}")
