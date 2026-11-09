"""Make a symlink or a directory junction for a link test (#59).

A symlink the test cannot create (no privilege, as on a Windows box without
Developer Mode) skips the test when CI is unset and fails it when CI is set,
as tests/page_script/gate.py does for Node. A junction needs no privilege
and is never skipped on Windows. A plain module, not a test module; #43's
link test uses it too.
"""

import os
import stat

import pytest

SYMLINK_REASON = "cannot create a symlink here (no privilege)"
CI_SUFFIX = " (CI is set, so a link test fails instead of skipping)"
_NO_PRIVILEGE = 1314  # ERROR_PRIVILEGE_NOT_HELD


def symlink(target, link, *, directory=False, env=None):
    """os.symlink(target, link); skip without the privilege, or fail under CI."""
    env = os.environ if env is None else env
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (PermissionError, NotImplementedError):
        _cannot(env)
    except OSError as e:
        if getattr(e, "winerror", None) != _NO_PRIVILEGE:
            raise
        _cannot(env)


def _cannot(env):
    if env.get("CI"):
        pytest.fail(SYMLINK_REASON + CI_SUFFIX, pytrace=False)
    pytest.skip(SYMLINK_REASON)


def junction(target, link):
    """A directory junction at `link` to `target`; Windows only."""
    if os.name != "nt":
        pytest.skip("a junction needs Windows")
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


def link_dir(target, link):
    """A link to a folder: a junction on Windows, a symlink elsewhere."""
    if os.name == "nt":
        junction(target, link)
    else:
        symlink(target, link, directory=True)


def is_link(path) -> bool:
    """A symlink, or a junction (which os.path.islink misses before 3.12)."""
    st = os.lstat(path)
    tag = getattr(st, "st_reparse_tag", 0)
    return stat.S_ISLNK(st.st_mode) or tag == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1)
