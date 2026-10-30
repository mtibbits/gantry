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
    values = {**BASE, **raw}
    lines = [f"{key} = {value}" for key, value in values.items() if value is not None]
    text = "\n".join(lines) + "\n" + tail
    path = folder / name
    path.write_bytes(text.encode("utf-8"))
    return path


def sample_projects():
    return list(load_config()["project"])


def copy_samples(tmp_path):
    copy = tmp_path / "samples"
    shutil.copytree(SAMPLES, copy, ignore=shutil.ignore_patterns("out"))
    return copy


def append_bytes(path, text):
    before = path.read_bytes()
    after = before + text.encode("utf-8")
    assert after != before
    path.write_bytes(after)
