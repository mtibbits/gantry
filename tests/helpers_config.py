"""Config fixture helpers shared by the config, site and CLI tests (#7).

A plain module, not a test module, so no test module imports another; it
resolves like sample_roles (see its import contract).
"""

import shutil
from html.parser import HTMLParser
from pathlib import Path

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
        tables.append(f'[project."{name}"]\ndevdoc_dir = "{index}"\n')
    (folder / "devagent.toml").write_bytes("\n".join(tables).encode("utf-8"))
    return _write_toml(folder / "gantry.toml", tail, raw)


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

