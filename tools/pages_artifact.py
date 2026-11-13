"""Stage the GitHub Pages artifact: the demo plus a holding page (#45).

    python tools/pages_artifact.py <demo folder> <stage folder> --repo-url <url>

Copies the demo folder to <stage>/demo/ and writes <stage>/index.html, a
generated holding page (UTF-8, LF) that links the demo and the source
repository. The holding page is never committed; #89 replaces it with the
website. Exit 1 with one `pages_artifact: <reason>` line, writing nothing,
when the stage folder is not empty, the demo folder holds no index.html, or
--repo-url is not an https repository URL; a usage error exits 2.
docs/demo.md states the layout.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# page_safety lives in tests/; its import contract is that a non-pytest
# consumer puts <root>/tests on sys.path.
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from page_safety import CSP_META  # noqa: E402

PROG = "pages_artifact"
REPO_URL_RE = re.compile(r"https://[A-Za-z0-9.-]+/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
HOLDING_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
{csp}
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gantry</title>
</head>
<body>
<main>
<h1>Gantry</h1>
<p>The Gantry website is coming.</p>
<p>Planning pages for many projects, built from devagent WBS files.</p>
<ul>
<li><a href="demo/">The demo, built from sample data</a></li>
<li><a href="{repo_url}">The source repository</a></li>
</ul>
</main>
</body>
</html>
"""


class Refused(Exception):
    """A reason to exit 1."""


def holding_page(repo_url) -> str:
    """The root page of the artifact; `repo_url` must already be checked."""
    return HOLDING_PAGE.format(csp=CSP_META, repo_url=repo_url)


def stage(demo, dest, repo_url):
    """Copy `demo` to <dest>/demo and write <dest>/index.html, or Refused."""
    demo, dest = Path(demo), Path(dest)
    if not REPO_URL_RE.fullmatch(repo_url):
        raise Refused("--repo-url is not an https repository URL")
    if not (demo / "index.html").is_file():
        raise Refused("the demo folder holds no index.html")
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise Refused("the stage folder is not empty")
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(demo, dest / "demo")
    (dest / "index.html").write_bytes(holding_page(repo_url).encode("utf-8"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__.split("\n\n")[0])
    parser.add_argument("demo", help="the demo output folder")
    parser.add_argument("stage", help="an absent or empty folder to stage the artifact in")
    parser.add_argument("--repo-url", required=True, help="the source repository's https URL")
    args = parser.parse_args(argv)
    try:
        stage(args.demo, args.stage, args.repo_url)
    except Refused as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"{PROG}: cannot stage the artifact: {e.strerror or type(e).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
