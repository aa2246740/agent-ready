"""Read-only Git snapshots and validation of repository-local evidence.

Snapshots include the contents of tracked and non-ignored untracked files. They
never include Git credential/configuration files, and symlinks are hashed as
links rather than followed. Commands cited as evidence are not executed here.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
from collections import deque
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit


class RepositoryError(ValueError):
    """The repository or a supplied evidence/output path is invalid."""


def _git(root: Path, *args: str, optional: bool = False) -> bytes | None:
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    # Caller-level Git overrides must not redirect a snapshot into another repo.
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(key, None)
    try:
        result = subprocess.run(
            # A configured fsmonitor hook is executable code; inspection must
            # not run it or rely on an untrusted cached filesystem observation.
            ["git", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise RepositoryError("Git is required to inspect a repository") from exc
    if result.returncode:
        if optional:
            return None
        # Git diagnostics can contain credential-bearing remote URLs.
        raise RepositoryError(f"Unable to read Git repository ({args[0]})")
    return result.stdout


def _root(path: str | os.PathLike[str]) -> Path:
    try:
        candidate = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RepositoryError("Repository path does not exist or cannot be resolved") from exc
    if not candidate.is_dir():
        raise RepositoryError("Repository path must be a directory")
    output = _git(candidate, "rev-parse", "--show-toplevel")
    try:
        return Path(os.fsdecode(output.removesuffix(b"\n"))).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RepositoryError("Unable to resolve Git repository root") from exc


def canonicalize_origin(origin: str) -> str:
    """Remove credentials and normalize common HTTPS, SSH and SCP Git URLs.

    The transport does not define repository identity: ordinary HTTPS and SSH
    URLs for the same host/path share the canonical HTTPS representation.
    """
    value = origin.strip()
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise RepositoryError("Invalid Git remote URL")
    if "://" not in value:
        scp = re.fullmatch(r"(?:[^/@:]+@)?([^/:]+):(.+)", value)
        if scp:
            value = f"ssh://{scp.group(1)}/{scp.group(2).lstrip('/')}"
        else:
            # Local remotes can have query-like text; remove it conservatively
            # rather than ever persisting an embedded credential parameter.
            value = value.split("?", 1)[0].split("#", 1)[0].rstrip("/")
            return value[:-4] if value.endswith(".git") else value
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise RepositoryError("Invalid Git remote URL") from exc
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if parsed.scheme == "file":
        return urlunsplit(("file", host or "", path, "", ""))
    if not host:
        raise RepositoryError("Invalid Git remote URL")
    host = host.lower()
    if ":" in host:
        host = f"[{host}]"
    default_ports = {"http": 80, "https": 443, "ssh": 22, "git": 9418}
    if port is not None and port != default_ports.get(parsed.scheme):
        host = f"{host}:{port}"
    scheme = "https" if parsed.scheme in default_ports else parsed.scheme
    return urlunsplit((scheme, host, path, "", ""))


def _relative_parts(value: object, *, allow_dot: bool = False) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RepositoryError("Evidence path must be a nonempty relative path")
    path = PurePosixPath(value)
    # Reject Windows absolute paths too, even when running on POSIX.
    if path.is_absolute() or re.match(r"^[A-Za-z]:", value) or "\\" in value:
        raise RepositoryError("Evidence path must be relative to the repository")
    parts = path.parts
    if ".." in parts or ".git" in parts:
        raise RepositoryError("Evidence path must not traverse parents or Git metadata")
    if not parts and not allow_dot:
        raise RepositoryError("Evidence file path must name a file")
    return parts


def _inside(root: Path, path: Path) -> bool:
    return path == root or root in path.parents


def _evidence_path(root: Path, value: object, *, directory: bool = False) -> Path:
    parts = _relative_parts(value, allow_dot=directory)
    try:
        path = root.joinpath(*parts).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RepositoryError("Evidence path does not exist or cannot be resolved") from exc
    if not _inside(root, path):
        raise RepositoryError("Evidence path escapes the repository through a symlink")
    if ".git" in path.relative_to(root).parts:
        raise RepositoryError("Git metadata cannot be cited as file evidence")
    if directory and not path.is_dir():
        raise RepositoryError("Command evidence cwd must be an existing directory")
    if not directory and not path.is_file():
        raise RepositoryError("File evidence must name an existing regular file")
    return path


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _ensure_snapshot_member(root: Path, path: Path) -> None:
    """Require a file/link or nested repository to be covered by the snapshot."""
    owner = _root(path.parent)
    if not _inside(root, owner):
        raise RepositoryError("File evidence must belong to the evaluated repository")
    if owner != root:
        # A nested repository can itself be ignored by its containing worktree.
        _ensure_snapshot_member(root, owner)
    relative = path.relative_to(owner).as_posix()
    listed = _git(owner, "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", f":(literal){relative}")
    entries = set(listed.split(b"\x00"))
    raw = os.fsencode(relative)
    if raw not in entries and raw + b"/" not in entries:
        raise RepositoryError(
            "File evidence must be tracked or non-ignored so its contents are covered by the snapshot; "
            "use command evidence with recorded output for ignored files"
        )


def _ensure_evidence_snapshotted(root: Path, value: str) -> Path:
    # Cover every symlink in the reference as well as the final file. Otherwise
    # an ignored link could redirect a citation without changing the snapshot.
    pending = deque(PurePosixPath(value).parts)
    current = root
    links_followed = 0
    try:
        while pending:
            part = pending.popleft()
            if part == ".":
                continue
            if part == "..":
                current = current.parent
            else:
                current = current / part
            if not _inside(root, current) or ".git" in current.relative_to(root).parts:
                raise RepositoryError("Evidence symlink traverses outside the working tree")
            if not current.is_symlink():
                continue
            _ensure_snapshot_member(root, current)
            links_followed += 1
            if links_followed > 40:
                raise RepositoryError("Evidence symlink cannot be safely resolved")
            target = Path(os.readlink(current))
            current = current.parent
            if target.is_absolute():
                if not _inside(root, target):
                    raise RepositoryError("Evidence symlink traverses outside the working tree")
                current = root
                target = target.relative_to(root)
            pending.extendleft(reversed(target.parts))
    except OSError as exc:
        raise RepositoryError("Unable to inspect file evidence safely") from exc
    _ensure_snapshot_member(root, current)
    return current


def _validate_line(path: Path, requested_line: int) -> None:
    """Check a citation's line without decoding or exposing file contents."""
    lines = 0
    last_byte = b""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise RepositoryError("File evidence changed during validation")
            while chunk := stream.read(1024 * 1024):
                lines += chunk.count(b"\n")
                if lines >= requested_line:
                    return
                last_byte = chunk[-1:]
    except OSError as exc:
        raise RepositoryError("Unable to validate file evidence line") from exc
    if last_byte and last_byte != b"\n":
        lines += 1
    if requested_line > lines:
        raise RepositoryError("File evidence line does not exist")


def validate_evidence(root: str | os.PathLike[str], evidence: object) -> None:
    """Validate one evidence object (or a list) without executing commands."""
    repository_root = _root(root)
    items = evidence if isinstance(evidence, list) else [evidence]
    for item in items:
        if not isinstance(item, Mapping):
            raise RepositoryError("Evidence must be an object")
        if item.get("kind") == "file":
            path = _evidence_path(repository_root, item.get("path"))
            if not _nonempty(item.get("note")):
                raise RepositoryError("File evidence requires a nonempty note")
            if "line" in item and (type(item["line"]) is not int or item["line"] < 1):
                raise RepositoryError("File evidence line must be a positive integer")
            if _ensure_evidence_snapshotted(repository_root, item["path"]) != path:
                raise RepositoryError("File evidence changed during validation")
            if "line" in item:
                _validate_line(path, item["line"])
        elif item.get("kind") == "command":
            if not _nonempty(item.get("command")):
                raise RepositoryError("Command evidence requires a command")
            _evidence_path(repository_root, item.get("cwd"), directory=True)
            if type(item.get("exitCode")) is not int:
                raise RepositoryError("Command evidence exitCode must be an integer")
            if not isinstance(item.get("output"), str):
                raise RepositoryError("Command evidence requires recorded output as a string")
        else:
            raise RepositoryError("Evidence kind must be 'file' or 'command'")


def ensure_external_output(
    root: str | os.PathLike[str], path: str | os.PathLike[str]
) -> Path:
    """Resolve an output location and reject writes anywhere inside the target."""
    repository_root = _root(root)
    try:
        destination = Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise RepositoryError("Output path cannot be resolved") from exc
    if _inside(repository_root, destination):
        raise RepositoryError("Output must be outside the repository being evaluated")
    return destination


def _frame(digest, tag: bytes, content: bytes) -> None:
    digest.update(tag)
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)


def _status(root: Path) -> bytes:
    return _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none")


def _head(root: Path) -> str | None:
    output = _git(root, "rev-parse", "--verify", "HEAD", optional=True)
    return output.decode("ascii").strip() if output else None


def _snapshot(root: Path, head: str | None, status_before: bytes) -> str:
    digest = hashlib.sha256()
    _frame(digest, b"version", b"agent-readiness-working-tree-v1")
    _frame(digest, b"head", (head or "").encode("ascii"))
    _frame(digest, b"status", status_before)
    listed = _git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    for raw in sorted(set(listed.split(b"\x00")) - {b""}):
        relative = os.fsdecode(raw)
        # Git paths are POSIX-relative, but a backslash is a legal filename on
        # POSIX and must not be interpreted as a separator during snapshots.
        parts = PurePosixPath(relative).parts
        if PurePosixPath(relative).is_absolute() or ".." in parts or ".git" in parts:
            raise RepositoryError("Git returned an unsafe working-tree path")
        path = root.joinpath(*parts)
        try:
            parent = path.parent.resolve()
            if not _inside(root, parent):
                raise RepositoryError("A tracked path escapes the repository through a symlink")
            info = path.lstat()
        except FileNotFoundError:
            _frame(digest, b"path", raw)
            _frame(digest, b"kind", b"missing")
            continue
        except OSError as exc:
            raise RepositoryError("Unable to inspect a repository file") from exc
        _frame(digest, b"path", raw)
        if stat.S_ISLNK(info.st_mode):
            _frame(digest, b"kind", b"symlink")
            _frame(digest, b"target", os.fsencode(os.readlink(path)))
        elif stat.S_ISREG(info.st_mode):
            _frame(digest, b"kind", b"regular")
            _frame(digest, b"executable", b"yes" if info.st_mode & 0o111 else b"no")
            content_hash = hashlib.sha256()
            try:
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(descriptor, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if not stat.S_ISREG(opened.st_mode):
                        raise RepositoryError("Repository file changed during inspection")
                    while chunk := stream.read(1024 * 1024):
                        content_hash.update(chunk)
                    finished = os.fstat(stream.fileno())
                current = path.lstat()
            except OSError as exc:
                raise RepositoryError("Unable to read a repository file safely") from exc
            signature = lambda entry: (entry.st_dev, entry.st_ino, entry.st_size, entry.st_mtime_ns, entry.st_ctime_ns)
            if not signature(info) == signature(opened) == signature(finished) == signature(current):
                raise RepositoryError("Repository file changed during inspection; retry the snapshot")
            _frame(digest, b"content-sha256", content_hash.digest())
        elif stat.S_ISDIR(info.st_mode):
            # git ls-files emits directory entries for gitlinks. Include checked
            # out submodules, including their working files and local changes.
            _frame(digest, b"kind", b"directory")
            nested = _git(path, "rev-parse", "--show-toplevel", optional=True)
            if nested and Path(os.fsdecode(nested.removesuffix(b"\n"))).resolve() == path.resolve():
                nested_head = _head(path)
                _frame(digest, b"submodule", _snapshot(path, nested_head, _status(path)).encode("ascii"))
        else:
            # Never open devices, FIFOs or sockets.
            _frame(digest, b"kind", f"special:{stat.S_IFMT(info.st_mode)}".encode("ascii"))
    if _status(root) != status_before or _head(root) != head:
        raise RepositoryError("Repository changed during inspection; retry the snapshot")
    return digest.hexdigest()


def inspect_repository(path: str | os.PathLike[str]) -> dict:
    """Describe a Git worktree and fingerprint its current on-disk contents."""
    root = _root(path)
    head = _head(root)
    branch_bytes = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", optional=True)
    branch = os.fsdecode(branch_bytes.rstrip(b"\n")) if branch_bytes else None
    remote = _git(root, "config", "--get", "remote.origin.url", optional=True)
    if not remote:
        remotes = _git(root, "remote").decode("utf-8", "surrogateescape").splitlines()
        if remotes:
            remote = _git(root, "config", "--get", f"remote.{sorted(remotes)[0]}.url", optional=True)
    origin = canonicalize_origin(os.fsdecode(remote).strip()) if remote else None
    status = _status(root)
    return {
        "root": str(root),
        "head": head,
        "branch": branch,
        "origin": origin,
        "identity": origin or f"local:{root}",
        "dirty": bool(status),
        "fingerprint": _snapshot(root, head, status),
    }
