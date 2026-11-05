"""Config fixture helpers shared by the config, site and CLI tests (#7).

A plain module, not a test module, so no test module imports another; it
resolves like sample_roles (see its import contract).
"""

import shutil
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
