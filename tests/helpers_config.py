"""Fixture helpers shared by the config, site, CLI and build tests (#7, #11).

A plain module, not a test module; it resolves like sample_roles (see its
import contract).
"""

import io
import json
import os
import posixpath
import re
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
    """Write a devagent config naming `sites`, each site's WBS.md, the portfolio
    document beside the devagent config, and a config.

    `sites` maps a site name to an hp.Source, in order: its text is the site's
    WBS.md and its nodes are devagent's reading of it in portfolio.json (#195);
    `None` leaves the site's planning directory with no WBS.md (devagent's
    no_wbs project). Site i's planning directory is the folder `<i>` beside the
    devagent config. Each Source's document is checked against the stopgap's
    reading of its text (_stopgap_agreement). `tail` and `raw` are as
    write_config's.
    """
    import helpers_portfolio as hp  # hp imports copy_samples from here at load

    for name, wbs in sites.items():
        if wbs is not None and not isinstance(wbs, hp.Source):
            raise TypeError(f"write_site_tree: site {name!r} is {type(wbs).__name__}; pass hp.source(text, *nodes) "
                            "or hp.fixture_source(a FIXTURE_SOURCES key), so the site carries its portfolio document")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    tables = []
    for index, (name, wbs) in enumerate(sites.items()):
        planning = folder / str(index)
        planning.mkdir()
        if wbs is not None:
            (planning / "WBS.md").write_bytes(wbs.text)
        tables.append(f'[project.{toml_key(name)}]\ndevdoc_dir = "{index}"\n')
    (folder / "devagent.toml").write_bytes("\n".join(tables).encode("utf-8"))
    (folder / "portfolio.json").write_bytes(hp.sp.dumps(hp.site_document(sites)))
    for name, wbs in sites.items():
        if wbs is not None:
            _stopgap_agreement(folder, folder / "portfolio.json", name, wbs.text)
    return _write_toml(folder / "gantry.toml", tail, raw)


# The agreement check (#195): while gantry/wbs_stopgap.py lives, a site's portfolio
# document must read its WBS.md as the stopgap does, on these snapshot fields only.
COMPARED = re.compile(r"nodes\[\d+\](\.(key|issue|outline|title|state|depends_on|meta))?|links|held_out"
                      r"|report\[(\d+|null), [a-z_]+\]")
# (class, the side holding the entry): known_differences.json's one-sided entries on
# COMPARED fields, pinned so a new entry cannot widen the check unseen.
ONE_SIDED = {("report[*, skipped_line]", "stopgap"), ("report[*, skipped_line]", "document"),
             ("report[*, no_issue]", "stopgap")}


def _row_class(where):
    return re.sub(r"\[(\d+|null)", "[*", where, count=1)


def _holder(stopgap, document):
    return "stopgap" if document is None else "document"


def one_sided_classes():
    """ONE_SIDED derived from known_differences.json: its entries on COMPARED fields with one side null."""
    import update_expected_leaves as ue

    entries = json.loads(ue.KNOWN.read_bytes().decode("utf-8"))
    return {(_row_class(e["where"]), _holder(e["stopgap"], e["snapshot"])) for e in entries
            if COMPARED.fullmatch(e["where"]) and (e["stopgap"] is None) != (e["snapshot"] is None)}


def _kept(where, stopgap, document):
    """Whether a diff_snapshots row counts: meta only by key order, a report entry only when one side lacks it."""
    if where.endswith(".meta"):
        return list(stopgap or {}) != list(document or {})
    if where.startswith("report["):
        return (stopgap is None) != (document is None)
    return True


def _stopgap_agreement(root, document, name, wbs):
    """Assert that the portfolio document at `document`, read under `root`, reads project
    `name` as the stopgap reads `wbs`, that site's WBS.md bytes."""
    import update_expected_leaves as ue
    from gantry.portfolio_json import ProjectInput, read_portfolio
    from gantry.roots import directory_roots
    from gantry.wbs_stopgap import parse_wbs

    found = [p for p in read_portfolio(directory_roots([root]), document).projects if p.name == name]
    assert len(found) == 1, f"write_site_tree: site {name!r}: the portfolio document holds {len(found)} such projects"
    (project,) = found
    try:
        text = wbs.decode("utf-8")
    except UnicodeDecodeError:
        assert project.status == "unreadable", f"write_site_tree: site {name!r}: not UTF-8 but {project.status}"
        return
    assert project.status == "read", f"write_site_tree: site {name!r}: the document says {project.status}"

    def snap(data):
        return ue.snapshot_of(ProjectInput(name, "read", None, data), (), pin="agreement", schema_version="1.1",
                              namespaces={name})

    assert one_sided_classes() == ONE_SIDED
    rows = [row for row in ue.diff_snapshots(snap(parse_wbs(text, name)), snap(project.data))
            if COMPARED.fullmatch(row[0]) and _kept(*row)]
    remaining = [row for row in rows
                 if not (row[0].startswith("report[") and (_row_class(row[0]), _holder(*row[1:])) in ONE_SIDED)]
    if remaining:
        raise AssertionError(
            f"write_site_tree: site {name!r}: the portfolio document disagrees with the stopgap's reading: {remaining}")


def toml_key(name):
    """A quoted TOML key for any name: backslash and double quote escaped."""
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def roots_around(*paths):
    """Directory roots at each path's folder, for a test with no installation config (#59)."""
    from gantry.roots import directory_roots

    return directory_roots([os.path.dirname(os.path.abspath(p)) for p in paths])


def copy_samples(tmp_path, document=True):
    """A copy of samples/ at <tmp_path>/samples, every folder named out left behind.

    With `document` (#195) the copy holds the committed sample document where
    devagent's aggregator writes it (install_sample_document). A caller that
    runs the aggregator on the copy passes document=False: sp.make_sample
    refuses to delete an out/ it cannot prove git ignores.
    """
    copy = tmp_path / "samples"
    shutil.copytree(SAMPLES, copy, ignore=shutil.ignore_patterns("out"))
    if document:
        install_sample_document(copy)
    return copy


def install_sample_document(samples):
    """Write tests/fixtures/portfolio/samples.json to <samples>/devagent/out/portfolio/portfolio.json,
    the aggregator's path in a samples folder (#195); the path. A link at any folder on the way
    or at the file raises sp.Refused, before anything is written."""
    import sample_portfolio as sp
    from helpers_links import is_link

    home = Path(samples) / "devagent"
    target = home.joinpath("out", *sp.DOCUMENT)
    for path in (home, home / "out", target.parent, target):
        if os.path.lexists(path) and is_link(path):
            raise sp.Refused(f"{path.relative_to(samples).as_posix()} is a link; the sample document is not written")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(sp.SAMPLES_WRITTEN.read_bytes())
    return target


def append_bytes(path, text):
    before = path.read_bytes()
    after = before + text.encode("utf-8")
    assert after != before
    path.write_bytes(after)


def literal(path):
    """A TOML literal string for a path: no escapes, so a Windows path needs none."""
    text = str(path)
    assert "'" not in text
    return f"'{text}'"


def replace_bytes(path, old, new):
    """Replace `old` with `new` in a UTF-8 file, asserting something changed."""
    before = path.read_bytes().decode("utf-8")
    after = before.replace(old, new)
    assert after != before
    path.write_bytes(after.encode("utf-8"))


def add_site_key(path, site, line):
    """Add `line` to the config's `[site.<site>]` table, appending the table
    when there is none (the sample already has some, #28)."""
    header = f"[site.{site}]\n"
    before = path.read_bytes().decode("utf-8")
    if header in before:
        after = before.replace(header, header + line, 1)
    else:
        after = before + ("" if before.endswith("\n") else "\n") + header + line
    assert after != before
    path.write_bytes(after.encode("utf-8"))


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
    monkeypatch.setattr(stamp, "sample_dir", lambda: copy)
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
