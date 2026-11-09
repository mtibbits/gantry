"""Fixture helpers shared by the config, site, CLI and build tests (#7, #11).

A plain module, not a test module; it resolves like sample_roles (see its
import contract).
"""

import io
import os
import posixpath
import shutil
from contextlib import redirect_stderr, redirect_stdout
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

from test_samples import CONFIG, SAMPLES, load_config

BASE = {
    "config_version": "1",
    "devagent_config": '"devagent.toml"',
    "output_dir": '"out"',
}


def write_config(folder, tail="", name="gantry.toml", **raw):
    """Write a valid installation config from raw TOML literals.

    `None` drops a key; `tail` is appended after the top-level keys (for
    tables). devagent_config points at a copy of the sample devagent config
    beside the file.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CONFIG, folder / "devagent.toml")
    return _write_toml(folder / name, tail, raw)


def _write_toml(path, tail, raw):
    """BASE updated by `raw` (`None` drops a key), then `tail`, written to path."""
    values = {**BASE, **raw}
    lines = [f"{key} = {value}" for key, value in values.items() if value is not None]
    path.write_bytes(("\n".join(lines) + "\n" + tail).encode("utf-8"))
    return path


def sample_projects():
    return list(load_config()["project"])


def write_site_tree(folder, sites, tail="", **raw):
    """Write a devagent config naming `sites`, each site's WBS.md and a config.

    `sites` maps a site name to its WBS.md bytes, in order; `None` leaves the
    site's planning directory with no WBS.md. Site i's planning directory is
    the folder `<i>` beside the devagent config. `tail` and `raw` are as
    write_config's.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    tables = []
    for index, (name, wbs) in enumerate(sites.items()):
        planning = folder / str(index)
        planning.mkdir()
        if wbs is not None:
            (planning / "WBS.md").write_bytes(wbs)
        tables.append(f'[project.{toml_key(name)}]\ndevdoc_dir = "{index}"\n')
    (folder / "devagent.toml").write_bytes("\n".join(tables).encode("utf-8"))
    return _write_toml(folder / "gantry.toml", tail, raw)


def toml_key(name):
    """A quoted TOML key for any name: backslash and double quote escaped."""
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def roots_around(*paths):
    """Directory roots at each path's folder, for a test with no installation config (#59)."""
    from gantry.roots import directory_roots

    return directory_roots([os.path.dirname(os.path.abspath(p)) for p in paths])


def copy_samples(tmp_path):
    copy = tmp_path / "samples"
    shutil.copytree(SAMPLES, copy, ignore=shutil.ignore_patterns("out"))
    return copy


def append_bytes(path, text):
    before = path.read_bytes()
    after = before + text.encode("utf-8")
    assert after != before
    path.write_bytes(after)


def add_top_level(path, text):
    """Insert top-level TOML keys before the first table header (or append
    when there is none), so they never land inside a table."""
    before = path.read_bytes().decode("utf-8")
    lines = before.splitlines(keepends=True)
    at = next((i for i, line in enumerate(lines) if line.startswith("[")), len(lines))
    after = "".join(lines[:at]) + text + "".join(lines[at:])
    assert after != before
    path.write_bytes(after.encode("utf-8"))


class _Ids(HTMLParser):
    """Every element id and every a's href, in HTML and SVG, in document order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids, self.links, self.foreign = [], [], 0

    def set_cdata_mode(self, elem, *args, **kwargs):
        # Inside svg nothing is raw text, as in a browser.
        if not self.foreign:
            super().set_cdata_mode(elem, *args, **kwargs)

    def handle_starttag(self, tag, attrs):
        self.foreign += tag == "svg"
        attrs = dict(attrs)
        if attrs.get("id") is not None:
            self.ids.append(attrs["id"])
        if tag == "a" and attrs.get("href") is not None:
            self.links.append(attrs["href"])

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        self.foreign -= tag == "svg" and self.foreign > 0


def _scan_ids(html):
    parser = _Ids()
    parser.feed(html)
    parser.close()
    return parser


def page_ids(html):
    """Every id on the page, in document order (a repeat is kept)."""
    return _scan_ids(html).ids


def page_links(html):
    """Every a[href] on the page, in document order."""
    return _scan_ids(html).links


def snapshot(root):
    """Each file and folder under root, with its size and modification time."""
    return {
        p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
    }


def written_bytes(out):
    """{relative path: bytes} for every file under out."""
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


def run_main(*args):
    """`gantry build ARGS...` in process: (exit code, stdout, stderr)."""
    from gantry.cli import main

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["build", *args])
    return code, out.getvalue(), err.getvalue()


def sample_build(tmp_path, **kw):
    """build_all on a samples copy that is its own sample directory."""
    from gantry.build import build_all
    from gantry.config import load_config as load_gantry_config

    copy = copy_samples(tmp_path)
    return build_all(load_gantry_config(copy / "gantry.sample.toml"), samples=copy, **kw)


def demo_build(tmp_path, monkeypatch, *args, copy=None):
    """`gantry build` on a samples copy in sample mode: (exit code, out folder, stdout, stderr).

    gantry.stamp.sample_dir is patched to the copy, so the copy is the
    sample directory and the stamp carries no build time.
    """
    from gantry import stamp

    if copy is None:
        copy = copy_samples(tmp_path)
    monkeypatch.setattr(stamp, "sample_dir", lambda package_dir=None: copy)
    code, out, err = run_main("--config", str(copy / "gantry.sample.toml"), *args)
    return code, copy / "out", out, err


def lands(out_dir, page_rel, href):
    """Whether a relative href on the page at `page_rel` lands on a written file
    and, with a fragment, on an id in it. The raw fragment is matched (U1)."""
    path, _, fragment = href.partition("#")
    target = posixpath.normpath(posixpath.join(posixpath.dirname(page_rel), unquote(path)))
    if target == ".." or target.startswith("../"):
        return False
    file = Path(out_dir) / target
    if not file.is_file():
        return False
    return not fragment or fragment in page_ids(file.read_bytes().decode("utf-8"))
