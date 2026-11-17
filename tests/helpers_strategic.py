"""Placement checks over a written strategic.html (#23), shared by the tests
and the operator's run on a real installation (AC11).

It imports only the standard library and page_safety, so the one-liner in
docs/strategic-page.md runs it from a checkout with tests/ on sys.path.
placement_line prints counts only: no project name, group, share or text.
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

