"""A data root the refresh job pulls (#35): a bare repository, a work clone
that pushes, and the data clone the installation's update list pulls.

A plain module, imported as `from helpers.refresh_repo import ...` (as
tests/page_script/ is: no __init__.py). Built with helpers_history's git,
make_repo and commit, so every repository has no global or system config
and pinned identities.

The installation reads devagent's portfolio JSON (#12): `<da_home>/portfolio/portfolio.json`,
where devagent's aggregator writes it. The helper pre-places the committed sample
document's reading of the site there, so a test that never runs the aggregator
builds from it. With `aggregator=` a devagent checkout, the update list is the pull
then the aggregator (tests/helpers/run_aggregator.py), whose home holds its own copy
of the devagent config: break_aggregator removes it and the build is not affected.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

import helpers_portfolio as hp
from helpers.run_aggregator import command as aggregator_command
from helpers_config import literal, toml_commands, write_config
from helpers_history import commit, git, make_repo
from sample_roles import SMALL
from test_samples import SAMPLES

WHEN = "2026-11-19"


@dataclass(frozen=True)
class RefreshRepo:
    bare: Path  # the remote: a bare repository
    work: Path  # where commits are made and pushed
    data: Path  # a full clone holding the planning directory: the data root
    config: Path  # the Gantry config, with [refresh] dir and the pull as its update list
    refresh_dir: Path
    site: str
    da_home: Path  # the aggregator's devagent home; the document is <da_home>/portfolio/portfolio.json
    document: Path

    def break_aggregator(self):
        """Remove the aggregator's own devagent config: its next run fails; Gantry's config stays."""
        (self.da_home / "config.toml").unlink()

    def wbs(self, repo=None) -> Path:
        return (self.data if repo is None else repo) / self.site / "WBS.md"

    def push_title(self, old, new):
        """Rename one leaf title in the work clone, commit and push it."""
        text = self.wbs(self.work).read_bytes().decode("utf-8")
        assert old in text, old
        commit(self.work, f"{self.site}/WBS.md", text.replace(old, new), WHEN)
        git(self.work, "push", "-q", "origin", "main")

    def break_remote(self):
        """Rename the bare repository away: the next pull cannot reach it."""
        self.bare.rename(self.bare.with_name(self.bare.name + ".gone"))


def make_refresh_repo(tmp_path, aggregator=None) -> RefreshRepo:
    """The data root and its installation; `aggregator` a devagent checkout adds the aggregator step."""
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    site = SMALL
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(bare))
    work = make_repo(tmp_path / "work")
    shutil.copytree(SAMPLES / site, work / site)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "the planning directory", when=WHEN)
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "-q", "origin", "main")
    data = tmp_path / "data"
    git(tmp_path, "clone", "-q", str(bare), str(data))

    inst = tmp_path / "inst"
    refresh_dir = inst / "refresh"
    da_home = tmp_path / "da"
    document = da_home.joinpath(*hp.sp.DOCUMENT)
    update = [["git", "-C", str(data), "pull", "--ff-only"]]
    if aggregator is not None:
        update.append(aggregator_command(aggregator, inst, da_home))
    config = write_config(inst, tail=f"\n[refresh]\ndir = {literal(refresh_dir)}\nupdate = {toml_commands(update)}\n",
                          portfolio_json=literal(document))
    # write_config's devagent.toml is the samples' copy: this one names the data clone's site alone.
    tables = f"[project.{site}]\ndevdoc_dir = {literal(data / site)}\n".encode("utf-8")
    (inst / "devagent.toml").write_bytes(tables)
    (da_home / "home").mkdir(parents=True)
    (da_home / "config.toml").write_bytes(tables)
    document.parent.mkdir(parents=True)
    document.write_bytes(hp.sp.dumps(hp.site_document({site: hp.sample_source(site)})))
    return RefreshRepo(bare, work, data, config, refresh_dir, site, da_home, document)
