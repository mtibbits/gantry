""".github/workflows/pages.yml has the shape the Pages deploy relies on (#45).

Stdlib only: the file is read as text, blank and whole-line comment lines are
dropped, and blocks are read by indentation (a small reader for the subset
the workflow uses: mappings, `- ` sequences, `|` block scalars), so a
commented-out or mis-nested key does not count. `workflow_problems` is the
one predicate: the committed file must give [], and each mutation row edits
a copy of the real file and must give the named problem.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "pages.yml"

ON_MAIN = "github.event_name == 'push' && github.ref == 'refs/heads/main'"
USES_RE = re.compile(r"^\s*(- )?uses: [\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")
USES_COUNT = 6
KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?:\s+(.*))?$")
STAGE = '"$RUNNER_TEMP/pages"'
STAGED_DEMO_CHECK = 'python tools/check_demo.py "$RUNNER_TEMP/pages/demo"'
SOURCE_FILE = '"$RUNNER_TEMP/pages-source.json"'
GH_API = f'gh api "repos/$GITHUB_REPOSITORY/pages" > {SOURCE_FILE}'
SOURCE_CHECK = f"python tools/check_demo.py --pages-source {SOURCE_FILE}"


class Unreadable(Exception):
    pass


def content_lines(text):
    """[(indent, content)] with blank and whole-line comment lines dropped."""
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise Unreadable("a tab in the indentation")
        lines.append((len(raw) - len(raw.lstrip(" ")), stripped))
    return lines


def _block(lines, i, indent):
    if lines[i][1].startswith("- ") or lines[i][1] == "-":
        return _sequence(lines, i, indent)
    return _mapping(lines, i, indent)


def _sequence(lines, i, indent):
    items = []
    while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
        lines[i] = (indent + 2, lines[i][1][2:].lstrip())
        item, i = _mapping(lines, i, indent + 2)
        items.append(item)
    return items, i


def _mapping(lines, i, indent):
    found = {}
    while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("- "):
        m = KEY_RE.match(lines[i][1])
        if m is None:
            raise Unreadable(f"not a key: {lines[i][1]!r}")
        key, value = m.group(1), m.group(2)
        if key in found:
            raise Unreadable(f"key {key!r} twice")
        i += 1
        if value in ("|", "|-", ">", ">-"):
            text = []
            while i < len(lines) and lines[i][0] > indent:
                text.append(lines[i][1])
                i += 1
            found[key] = "\n".join(text)
        elif value is None or value == "":
            if i < len(lines) and lines[i][0] > indent:
                found[key], i = _block(lines, i, lines[i][0])
            else:
                found[key] = None
        else:
            found[key] = value
    if i < len(lines) and lines[i][0] > indent:
        raise Unreadable(f"mis-nested line: {lines[i][1]!r}")
    return found, i


def read_workflow(text):
    lines = content_lines(text)
    if not lines:
        raise Unreadable("no content")
    tree, i = _block(lines, 0, lines[0][0])
    if i != len(lines):
        raise Unreadable(f"stray line: {lines[i][1]!r}")
    return tree


def _get(node, *keys):
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _steps(tree, job):
    steps = _get(tree, "jobs", job, "steps")
    return steps if isinstance(steps, list) else []


def _index(steps, test):
    return next((n for n, s in enumerate(steps) if isinstance(s, dict) and test(s)), None)


def _uses(prefix):
    return lambda s: str(s.get("uses", "")).startswith(prefix + "@")


def _run_is(command):
    return lambda s: s.get("run") == command


def _run_holds(command):
    return lambda s: command in str(s.get("run", ""))


def workflow_problems(text):
    """Every problem with the workflow's shape, each `<rule>: <what>`; [] when it has none."""
    try:
        tree = read_workflow(text)
    except Unreadable as e:
        return [f"readable: {e}"]
    flat = "\n".join(content for _, content in content_lines(text))
    problems = []

    def need(ok, rule, what):
        if not ok:
            problems.append(f"{rule}: {what}")

    jobs = _get(tree, "jobs")
    need(isinstance(jobs, dict) and set(jobs) == {"build", "deploy"}, "job-set", "the jobs are exactly build and deploy")
    need("write-all" not in text and "read-all" not in text, "no-blanket-permissions", "no write-all or read-all")

    need(_get(tree, "permissions") == {"contents": "read"}, "workflow-permissions",
         "the workflow's permissions are exactly contents: read")
    need(_get(tree, "jobs", "build", "permissions") == {"contents": "read"}, "build-permissions",
         "build's permissions are exactly contents: read")
    need(_get(tree, "jobs", "deploy", "permissions") == {"contents": "read", "pages": "write", "id-token": "write"},
         "deploy-permissions", "deploy's permissions are exactly contents: read, pages: write, id-token: write")
    for key in ("pages", "id-token"):
        count = len(re.findall(rf"(?m)^{key}:", flat))
        need(count == 1, "deploy-permissions", f"{key}: occurs once in the file, not {count} times")

    build, deploy = _steps(tree, "build"), _steps(tree, "deploy")
    upload = _index(build, _uses("actions/upload-pages-artifact"))
    need(_get(tree, "jobs", "deploy", "if") == ON_MAIN, "deploy-if", f"deploy's if is exactly {ON_MAIN}")
    need(upload is not None and build[upload].get("if") == ON_MAIN, "deploy-if", f"the upload step's if is exactly {ON_MAIN}")
    need(_get(tree, "jobs", "deploy", "needs") == "build", "deploy-if", "deploy needs build")
    need(_get(tree, "jobs", "deploy", "environment", "name") == "github-pages", "environment",
         "deploy's environment is github-pages")

    uses = [content for _, content in content_lines(text) if re.match(r"(- )?uses:", content)]
    need(len(uses) == USES_COUNT, "uses-pinned", f"{USES_COUNT} uses: lines, not {len(uses)}")
    for line in uses:
        need(USES_RE.match(line), "uses-pinned", f"not pinned to a full SHA with its version: {line}")

    need(_get(tree, "on") == {"push": {"branches": "[main]"}, "pull_request": None}, "triggers",
         "on is exactly push (branches [main]) and pull_request")
    need("pull_request_target" not in text, "triggers", "pull_request_target appears in the file")

    need(_get(tree, "concurrency") == {"group": "pages-${{ github.ref }}", "cancel-in-progress": "false"},
         "concurrency", "the workflow's concurrency is group pages-${{ github.ref }}, cancel-in-progress false")
    for name in jobs if isinstance(jobs, dict) else ():
        need(_get(jobs, name, "concurrency") is None, "concurrency", f"job {name} has its own concurrency")

    # Nothing may switch a step or a job off, or let it fail and carry on.
    need("continue-on-error" not in flat, "no-bypass", "continue-on-error appears in the file")
    need(_get(tree, "jobs", "build", "if") is None, "no-bypass", "the build job has an if")
    for name in ("build", "deploy"):
        for n, step in enumerate(_steps(tree, name)):
            need(not isinstance(step, dict) or "if" not in step or (name == "build" and n == upload), "no-bypass",
                 f"{name} step {n + 1} has an if")

    need([str(s.get("uses", s.get("run", ""))).split("@")[0] for s in deploy if isinstance(s, dict)]
         == ["actions/checkout", "actions/setup-python", GH_API, SOURCE_CHECK, "actions/deploy-pages"],
         "source-check", "deploy's steps are exactly checkout, setup-python, the API read, the source check, deploy-pages")
    api = _index(deploy, _run_holds("gh api"))
    check = _index(deploy, _run_is(SOURCE_CHECK))
    need(api is not None and deploy[api].get("run") == GH_API and deploy[api].get("shell") == "bash"
         and _get(deploy[api], "env", "GH_TOKEN") == "${{ github.token }}",
         "source-check", f"a bash step with GH_TOKEN runs exactly: {GH_API}")
    need(check is not None and deploy[check].get("shell") == "bash", "source-check", f"a bash step runs: {SOURCE_CHECK}")

    for name in ("build", "deploy"):
        steps = _steps(tree, name)
        need(_get(tree, "jobs", name, "runs-on") == "ubuntu-latest", "build-steps", f"{name} runs on ubuntu-latest")
        out = _index(steps, _uses("actions/checkout"))
        need(out is not None and _get(steps[out], "with") == {"fetch-depth": "1", "persist-credentials": "false"},
             "build-steps", f"{name}'s checkout has fetch-depth 1 and persist-credentials false")
        py = _index(steps, _uses("actions/setup-python"))
        need(py is not None and _get(steps[py], "with") == {"python-version": '"3.11"'}, "build-steps",
             f'{name} sets up python-version "3.11"')
    order = [
        _index(build, _run_holds("python tools/demo_build.py")),
        _index(build, _run_is('python tools/check_demo.py "$DEMO"')),
        _index(build, _run_holds("python tools/pages_artifact.py")),
        _index(build, _run_is(STAGED_DEMO_CHECK)),
        upload,
    ]
    # Exact, like deploy's list: a step between the staged check and the upload could rewrite what ships.
    need(len(build) == len(order) + 2, "build-steps", "build's steps are exactly checkout, setup-python and these five")
    need(None not in order and order == sorted(set(order)), "build-steps",
         "build runs demo_build.py, check_demo.py $DEMO, pages_artifact.py, check_demo.py on the stage, then the upload")
    demo = order[0]
    need(demo is not None and "set -o pipefail" in build[demo].get("run", "").split("\n")[0], "build-steps",
         "the demo step starts with set -o pipefail")

    stage = order[2]
    need(stage is not None and f'"$DEMO" {STAGE} --repo-url' in build[stage].get("run", ""), "stage-path",
         f"pages_artifact.py stages into {STAGE}")
    need(order[3] is not None, "stage-path", f"the staged demo is checked: {STAGED_DEMO_CHECK}")
    need(upload is not None and _get(build[upload], "with") == {"path": "${{ runner.temp }}/pages"}, "stage-path",
         "the upload's path is ${{ runner.temp }}/pages")
    return problems


def workflow_text():
    return WORKFLOW.read_bytes().decode("utf-8").replace("\r\n", "\n")


def test_workflow_has_no_problems():
    assert workflow_problems(workflow_text()) == []


def test_reader_drops_comments_and_reads_nesting():
    text = "top:\n  # gone: 1\n  kept: 2\n  list:\n    - one: 3\n      run: |\n        x\n        y\n"
    assert read_workflow(text) == {"top": {"kept": "2", "list": [{"one": "3", "run": "x\ny"}]}}


SHA_LINE = re.compile(r"(uses: actions/checkout@)[0-9a-f]{40} # v[\d.]+")
DEPLOY_STEP = re.compile(r"      - id: deployment\n        uses: actions/deploy-pages@[^\n]+\n")
CHECK_STEP = '      - shell: bash\n        env:\n          DEMO: ${{ steps.demo.outputs.folder }}\n        run: python tools/check_demo.py "$DEMO"\n'
BASH = "      - shell: bash\n"
SOURCE_STEP = BASH + f"        run: {SOURCE_CHECK}\n"
BUILD_HEAD = "  build:\n    runs-on: ubuntu-latest\n"
TOP_CONCURRENCY = "concurrency:\n  group: pages-${{ github.ref }}\n  cancel-in-progress: false\n"


def move_deploy_first(text):
    step = DEPLOY_STEP.search(text).group(0)
    text = text.replace(step, "")
    anchor = "      url: ${{ steps.deployment.outputs.page_url }}\n    steps:\n"
    return text.replace(anchor, anchor + step)


def move_concurrency(text):
    job = TOP_CONCURRENCY.replace("\n", "\n    ").replace("concurrency", "    concurrency", 1).rstrip(" ")
    return text.replace(TOP_CONCURRENCY, "").replace("  deploy:\n", "  deploy:\n" + job)


def sub(old, new, count=1):
    return lambda text: text.replace(old, new, count)


# id: (edit, the rule whose problem it must produce)
MUTATIONS = {
    "workflow-pages": (sub("permissions:\n  contents: read\n", "permissions:\n  contents: read\n  pages: write\n"),
                       "workflow-permissions"),
    "build-write": (sub("  build:\n    runs-on: ubuntu-latest\n    permissions:\n      contents: read\n",
                        "  build:\n    runs-on: ubuntu-latest\n    permissions:\n      contents: write\n"),
                    "build-permissions"),
    "deploy-no-id-token": (sub("      id-token: write\n", ""), "deploy-permissions"),
    "build-pages-write": (sub("  build:\n    runs-on: ubuntu-latest\n    permissions:\n      contents: read\n",
                              "  build:\n    runs-on: ubuntu-latest\n    permissions:\n      contents: read\n"
                              "      pages: write\n"), "deploy-permissions"),
    "deploy-no-if": (sub(f"    if: {ON_MAIN}\n", ""), "deploy-if"),
    "upload-no-if": (sub(f"- if: {ON_MAIN}\n        uses: actions/upload", "- uses: actions/upload"), "deploy-if"),
    "deploy-if-event-only": (sub(f"    if: {ON_MAIN}\n", "    if: github.event_name == 'push'\n"), "deploy-if"),
    "deploy-no-needs": (sub("    needs: build\n", ""), "deploy-if"),
    "environment": (sub("name: github-pages", "name: pages"), "environment"),
    "uses-floating": (lambda t: SHA_LINE.sub(r"\g<1>v4", t, 1), "uses-pinned"),
    "uses-no-version": (lambda t: SHA_LINE.sub(lambda m: m.group(0).split(" #")[0], t, 1), "uses-pinned"),
    "triggers-target": (sub("  pull_request:\n", "  pull_request:\n  pull_request_target:\n"), "triggers"),
    "triggers-target-comment": (sub("on:\n", "on:\n  # pull_request_target\n"), "triggers"),
    "triggers-branches": (sub("branches: [main]", "branches: [main, dev]"), "triggers"),
    "concurrency-cancel": (sub("cancel-in-progress: false", "cancel-in-progress: true"), "concurrency"),
    "concurrency-group": (sub("group: pages-${{ github.ref }}", "group: pages"), "concurrency"),
    "concurrency-in-deploy": (move_concurrency, "concurrency"),
    "source-no-shell": (sub("      - shell: bash\n        env:\n          GH_TOKEN", "      - env:\n          GH_TOKEN"),
                        "source-check"),
    "source-piped": (sub(f"> {SOURCE_FILE}\n", "| python tools/check_demo.py --pages-source -\n"), "source-check"),
    "source-deploy-first": (move_deploy_first, "source-check"),
    "build-no-pipefail": (sub("          set -o pipefail\n", ""), "build-steps"),
    "build-depth": (sub("fetch-depth: 1", "fetch-depth: 0"), "build-steps"),
    "build-credentials": (sub("persist-credentials: false", "persist-credentials: true"), "build-steps"),
    "build-python": (sub('python-version: "3.11"', 'python-version: "3.12"'), "build-steps"),
    "build-no-demo-check": (sub(CHECK_STEP, ""), "build-steps"),
    "deploy-windows": (sub("    needs: build\n    runs-on: ubuntu-latest", "    needs: build\n    runs-on: windows-latest"),
                       "build-steps"),
    "job-set": (lambda t: t.rstrip("\n") + "\n\n  extra:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n",
                "job-set"),
    "write-all": (sub("  build:\n    runs-on: ubuntu-latest\n    permissions:\n      contents: read\n",
                      "  build:\n    runs-on: ubuntu-latest\n    permissions: write-all\n"), "no-blanket-permissions"),
    "stage-path-upload": (sub("path: ${{ runner.temp }}/pages", "path: ${{ runner.temp }}/other"), "stage-path"),
    "stage-path-staged-check": (sub(STAGED_DEMO_CHECK, 'python tools/check_demo.py "$DEMO"'), "stage-path"),
    "mis-nested-if": (sub(f"    if: {ON_MAIN}\n", f"      if: {ON_MAIN}\n"), "readable"),
    "check-continue-on-error": (sub(SOURCE_STEP, SOURCE_STEP.replace(BASH, BASH + "        continue-on-error: true\n")),
                                "no-bypass"),
    "check-if-false": (sub(SOURCE_STEP, SOURCE_STEP.replace(BASH, "      - if: false\n        shell: bash\n")), "no-bypass"),
    "source-overwritten": (sub(SOURCE_STEP, BASH + "        run: echo '{\"build_type\":\"workflow\"}' > " + SOURCE_FILE + "\n"
                               + SOURCE_STEP), "source-check"),
    "demo-check-continue": (sub(CHECK_STEP, CHECK_STEP.replace(BASH, BASH + "        continue-on-error: true\n")),
                            "no-bypass"),
    "build-job-continue": (sub(BUILD_HEAD, "  build:\n    continue-on-error: true\n    runs-on: ubuntu-latest\n"),
                           "no-bypass"),
    "build-job-if": (sub(BUILD_HEAD, "  build:\n    if: false\n    runs-on: ubuntu-latest\n"), "no-bypass"),
    "commented-out-if": (sub(f"    if: {ON_MAIN}\n", f"    # if: {ON_MAIN}\n"), "deploy-if"),
    "stage-overwritten": (sub(f"      - if: {ON_MAIN}\n        uses: actions/upload",
                              BASH + '        run: echo x > "$RUNNER_TEMP/pages/demo/index.html"\n'
                              + f"      - if: {ON_MAIN}\n        uses: actions/upload"), "build-steps"),
}


@pytest.mark.parametrize("case", list(MUTATIONS))
def test_mutation_is_caught(case):
    edit, rule = MUTATIONS[case]
    text = workflow_text()
    mutated = edit(text)
    assert mutated != text, "the edit changed nothing"
    problems = workflow_problems(mutated)
    assert any(p.startswith(rule + ": ") for p in problems), problems
