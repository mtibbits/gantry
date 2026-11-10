"""The one module that touches the file system: every read, listing, stat,
write, rename, mkdir and delete Gantry makes goes through here (#59).

A run has a set of roots, a `Roots` value built from its config: each root
is a resolved file or folder the config names (or reaches through the
devagent config), plus the package directory. Each function takes the run's
`Roots` first and a path second, resolves the path at the moment of use
(links and junctions followed, `..` collapsed), refuses it unless the result
lies under a root (for a write, a writable one), and then acts on the
resolved path it checked, never on the path as given. A refusal raises
`OutsideRoots`, whose message names the config key or file kind (and the
site) and never an absolute path. There is no module state, and every value
handed out that carries a path is a `PurePath`.

This module imports only the standard library. Under gantry/, roots come
only from `bootstrap_roots`, `with_file`, `add_root`, `config_roots` (in
gantry/sites.py, which builds through `add_root`) and `package_roots`;
`directory_roots` is for tests and tools. docs/config.md, "Where a build may
read and write", states the rules.
"""

import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import PurePath

CONFIG_KEY = "--config"
PACKAGE_KEY = "the gantry package"
NUL_MESSAGE = "embedded null character"  # what open() itself says
KINDS = ("file", "directory", "package")


# --- values ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Root:
    """One root: `path` resolved (links followed, `..` collapsed), `given` as configured."""

    path: PurePath
    given: PurePath
    key: str
    kind: str  # one of KINDS
    writable: bool
    site: str | None = None


@dataclass(frozen=True)
class Roots:
    """A run's roots, in the order they were added. Which are writable is
    the single record of the run's purpose (build, refresh or serve)."""

    roots: tuple[Root, ...]


@dataclass(frozen=True)
class Entry:
    """One `list_confined` entry: plain values, never an os.DirEntry.

    For a link, `is_dir` and `is_file` describe its target only when the
    target lies inside the roots; for a link out of them both are false and
    `leaves_roots` is true (provisional for #31: no gantry/ caller reads it
    yet but estimates discovery).
    """

    name: str
    is_dir: bool
    is_file: bool
    is_link: bool
    leaves_roots: bool


class OutsideRoots(Exception):
    """A path outside the run's roots. `kind` is the access refused, "read"
    or "write", not the root's kind. Not an OSError."""

    def __init__(self, key, kind, site=None):
        self.key, self.kind, self.site = key, kind, site
        super().__init__(f"{_subject(key, site)} is outside the configured roots")


class LinkRefused(OutsideRoots):
    """A link refused by `follow_links=False` (provisional for #31)."""

    def __init__(self, key, kind="read", site=None):
        super().__init__(key, kind, site)
        self.args = (f"{_subject(key, site)} is a link",)


def _subject(key, site):
    return key if site is None else f"{key} for site {site}"


# --- resolution -----------------------------------------------------------------------


def _text(path) -> str:
    """The path's text; an embedded NUL raises ValueError before anything else."""
    text = os.fspath(path)
    if "\x00" in text:
        raise ValueError(NUL_MESSAGE)
    return text


def _canon(text) -> str:
    return os.path.normcase(os.path.realpath(text))


def _within(inner: str, outer: str) -> bool:
    """Whether canonical `inner` is `outer` or under it."""
    try:
        return os.path.commonpath([outer, inner]) == outer
    except ValueError:
        # Another drive, or one path relative: never inside.
        return False


def _admits(root: Root, canon: str) -> bool:
    mine = os.path.normcase(str(root.path))
    return canon == mine if root.kind == "file" else _within(canon, mine)


def _admitting(roots, canon, write):
    for root in roots.roots:
        if (root.writable or not write) and _admits(root, canon):
            return root
    return None


def _check(roots, path, *, key, site, write=False):
    """The resolved path text, or raise OutsideRoots."""
    resolved = os.path.realpath(_text(path))
    root = _admitting(roots, os.path.normcase(resolved), write)
    if root is None:
        raise OutsideRoots(key, "write" if write else "read", site)
    return resolved


def _check_entry(roots, path, *, key, site):
    """The resolved folder joined with the path's last part, unresolved, under
    a writable root, or raise OutsideRoots. A write, rename or removal then
    acts on a link at `path` itself, as os.replace and os.remove do, never on
    what it points at."""
    text = _text(path)
    head, leaf = os.path.split(text)
    if leaf in ("", ".", ".."):
        return _check(roots, path, key=key, site=site, write=True)
    resolved = os.path.join(os.path.realpath(head or os.curdir), leaf)
    if _admitting(roots, os.path.normcase(resolved), True) is None:
        raise OutsideRoots(key, "write", site)
    return resolved


def _is_link_stat(st) -> bool:
    """A symlink, or a junction (which S_ISLNK misses)."""
    tag = getattr(st, "st_reparse_tag", 0)
    return stat.S_ISLNK(st.st_mode) or tag == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1)


def _is_link(text) -> bool:
    try:
        return _is_link_stat(os.lstat(text))
    except OSError:
        return False


def _refuse_links(roots, path, *, key, site):
    """LinkRefused when a component of `path` that sits inside a root is a link.

    The components are walked as written (`..` not collapsed first), and a
    link counts by where it sits, not where it points: a link whose parent
    folder lies inside a root is refused, even one pointing back at a root. A
    link whose parent lies outside every root is where the path enters a
    root (a link at the root itself moved the root) and is allowed.
    """
    text = _text(path)
    if not os.path.isabs(text):
        text = os.path.join(os.getcwd(), text)
    parts = PurePath(text).parts
    prefix = parts[0]
    for part in parts[1:]:
        parent, prefix = prefix, os.path.join(prefix, part)
        if part == ".." or not _is_link(prefix):
            continue
        if _admitting(roots, _canon(parent), False) is not None:
            raise LinkRefused(key, "read", site)


# --- building roots -------------------------------------------------------------------


def _resolved_root(given, *, key, kind, writable=False, site=None) -> Root:
    """A root at `given` resolved; ValueError for a NUL, OSError from resolution."""
    if kind not in KINDS:
        raise ValueError(f"unknown root kind {kind!r}")
    text = _text(given)
    return Root(PurePath(os.path.realpath(text)), PurePath(text), key, kind, writable, site)


def _file_root(given, *, key) -> Root:
    """A read-only file root, built lexically when it cannot be resolved, so
    the read then fails into the reader's own message."""
    try:
        return _resolved_root(given, key=key, kind="file")
    except (OSError, ValueError):
        text = os.fspath(given)
        return Root(PurePath(text), PurePath(text), key, "file", False)


def bootstrap_roots(config_path) -> Roots:
    """The roots before the config is read: the --config file alone."""
    text = os.fspath(config_path)
    if "\x00" not in text:
        text = os.path.abspath(text)
    return Roots((_file_root(text, key=CONFIG_KEY),))


def with_file(roots, path, *, key) -> Roots:
    """`roots` plus one read-only file root."""
    return Roots(roots.roots + (_file_root(path, key=key),))


def add_root(roots, path, *, key, kind, writable=False, site=None) -> Roots:
    """`roots` plus one root; ValueError for a NUL, OSError if it cannot be resolved."""
    return Roots(roots.roots + (_resolved_root(path, key=key, kind=kind, writable=writable, site=site),))


def directory_roots(paths, *, writable=()) -> Roots:
    """Directory roots from paths, each keyed by its base name (keys may
    collide). Every `writable` path is a root too, and writable. For tests
    and tools only: under gantry/, roots come from the config."""
    canon_writable = {_canon(_text(p)) for p in writable}
    found, seen = [], set()
    for p in [*paths, *writable]:
        text = os.path.abspath(_text(p))
        canon = _canon(text)
        if canon in seen:
            continue
        seen.add(canon)
        found.append(_resolved_root(text, key=PurePath(text).name, kind="directory",
                                    writable=canon in canon_writable))
    return Roots(tuple(found))


def gantry_root() -> PurePath:
    """The resolved folder holding the running gantry package, from this file."""
    return PurePath(os.path.realpath(__file__)).parent.parent


def sample_dir() -> PurePath:
    """samples/ under the gantry root: the demo's only inputs."""
    return gantry_root() / "samples"


def package_root() -> Root:
    path = gantry_root() / "gantry"
    return Root(path, path, PACKAGE_KEY, "package", False)


def package_roots() -> Roots:
    """The package directory alone, read-only: templates and static files."""
    return Roots((package_root(),))


def with_package(roots) -> Roots:
    return Roots(roots.roots + (package_root(),))


def relation(outer: Root, inner: Root) -> str | None:
    """"equals" or "contains" when `outer` is or holds `inner`, else None."""
    a, b = os.path.normcase(str(outer.path)), os.path.normcase(str(inner.path))
    if a == b:
        return "equals"
    return "contains" if _within(b, a) else None


def is_within(inner, outer) -> bool:
    """Whether `inner` is `outer` or under it, compared as given (normalised and
    case-normalised, not resolved): the caller passes resolved paths (#28)."""
    def canon(path):
        return os.path.normcase(os.path.normpath(_text(path)))

    return _within(canon(inner), canon(outer))


def program_on_path(name, environ) -> bool:
    """Whether `name` is an executable on `environ`'s PATH, as a child process
    started with that environment would find it (#28)."""
    return shutil.which(name, path=environ.get("PATH", os.defpath)) is not None


def outside(roots, base) -> list[Root]:
    """The roots that are not `base` or under it, both sides resolved and
    case-normalised; the package root is never listed."""
    inner = _canon(_text(base))
    return [r for r in roots.roots
            if r.kind != "package" and not _within(_canon(str(r.path)), inner)]


# --- reads ----------------------------------------------------------------------------


def _nofollow(path, flags):
    return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o666)


def open_confined(roots, path, mode="r", *, key, site=None, follow_links=True, encoding=None):
    """open() on the resolved path, under a root (for w, a, x or +, a writable one).

    `follow_links=False` refuses a path with a link component below its root
    with LinkRefused, without opening the target (provisional for #31). A
    write mode is provisional for #35's lock file.
    """
    write = any(c in mode for c in "wax+")
    resolved = _check(roots, path, key=key, site=site, write=write)
    if not follow_links:
        _refuse_links(roots, path, key=key, site=site)
    return open(resolved, mode, encoding=encoding, opener=_nofollow)


def read_bytes_confined(roots, path, *, key, site=None, follow_links=True) -> bytes:
    with open_confined(roots, path, "rb", key=key, site=site, follow_links=follow_links) as handle:
        return handle.read()


def read_text_confined(roots, path, *, key, site=None, follow_links=True) -> str:
    """The file decoded as strict UTF-8."""
    return read_bytes_confined(roots, path, key=key, site=site, follow_links=follow_links).decode("utf-8")


def exists_confined(roots, path, *, key, site=None) -> bool:
    """Whether the path exists; outside every root it raises, never answers false."""
    resolved = _check(roots, path, key=key, site=site)
    return os.path.exists(resolved)


def stat_confined(roots, path, *, key, site=None) -> os.stat_result:
    resolved = _check(roots, path, key=key, site=site)
    return os.stat(resolved)


def list_confined(roots, path, *, key, site=None) -> list[Entry]:
    """The folder's entries; outside every root it raises, never answers empty."""
    resolved = _check(roots, path, key=key, site=site)
    entries = []
    with os.scandir(resolved) as it:
        for e in it:
            child = os.path.join(resolved, e.name)
            lst = os.lstat(child)
            if not _is_link_stat(lst):
                entries.append(Entry(e.name, stat.S_ISDIR(lst.st_mode), stat.S_ISREG(lst.st_mode), False, False))
                continue
            target = os.path.realpath(child)
            if _admitting(roots, os.path.normcase(target), False) is None:
                # Out of the roots: the target is never stat'd.
                entries.append(Entry(e.name, False, False, True, True))
                continue
            try:
                st = os.stat(target)
            except OSError:
                entries.append(Entry(e.name, False, False, True, False))  # dangling
                continue
            entries.append(Entry(e.name, stat.S_ISDIR(st.st_mode), stat.S_ISREG(st.st_mode), True, False))
    return entries


def resolve_confined(roots, path, *, key, site=None) -> PurePath:
    """The checked, resolved path, for a path handed to a child process
    (provisional for #43)."""
    resolved = _check(roots, path, key=key, site=site)
    return PurePath(resolved)


# --- writes ---------------------------------------------------------------------------


def write_confined(roots, path, data: bytes, *, key, site=None):
    """Write `data` through a temporary file in the same folder and a rename:
    no partial file. The file gets the mode any new file gets; a link at
    `path` is replaced, not followed."""
    resolved = _check_entry(roots, path, key=key, site=site)
    folder = os.path.dirname(resolved)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{PurePath(resolved).stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        # mkstemp makes the file 0600; a page gets the mode any new file
        # gets, so a server on the private network can read it.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp, 0o666 & ~umask)
        os.replace(tmp, resolved)
    except BaseException:
        # fdopen closed the descriptor, on success or failure.
        os.unlink(tmp)
        raise


def replace_confined(roots, src, dst, *, key, site=None):
    """os.replace under a writable root (provisional for #35)."""
    source = _check_entry(roots, src, key=key, site=site)
    target = _check_entry(roots, dst, key=key, site=site)
    os.replace(source, target)


def remove_confined(roots, path, *, key, site=None):
    """os.remove under a writable root (provisional for #35)."""
    resolved = _check_entry(roots, path, key=key, site=site)
    os.remove(resolved)


def mkdir_confined(roots, path, *, key, site=None):
    """Make the folder and its missing parents. The folder must lie under a
    writable root; the root's own missing ancestors (a new output_dir's
    parents) are created too, the one write outside a writable root."""
    resolved = _check(roots, path, key=key, site=site, write=True)
    _makedirs(resolved)


def _makedirs(path):
    """Path.mkdir(parents=True, exist_ok=True)'s algorithm, so a failure raises
    the error it raised (os.makedirs fails on the leaf instead of the parent)."""
    try:
        os.mkdir(path)
    except FileNotFoundError:
        parent = os.path.dirname(path)
        if parent == path:
            raise
        _makedirs(parent)
        try:
            os.mkdir(path)
        except OSError:
            if not os.path.isdir(path):
                raise
    except OSError:
        if not os.path.isdir(path):
            raise


def rmtree_confined(roots, path, *, key, site=None):
    """shutil.rmtree under a writable root (provisional for #35); like
    shutil.rmtree, it refuses a link."""
    resolved = _check_entry(roots, path, key=key, site=site)
    shutil.rmtree(resolved)
