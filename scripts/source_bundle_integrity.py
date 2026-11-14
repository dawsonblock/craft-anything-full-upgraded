#!/usr/bin/env python3
"""Create and independently check a complete, bounded Craft Anything source manifest.

This is a checksum/inventory check, not a digital signature or a Rust build.
It uses only the Python standard library and never executes bundled source.
Creating a manifest inside a Git work tree lists files with `git ls-files`, so
the inventory describes what a clone can actually contain. Verification never
needs Git and works on an extracted archive.
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import BinaryIO
import unicodedata
import zipfile
import zlib

MANIFEST_NAME = "SOURCE_INTEGRITY.json"
SCHEMA_VERSION = 1
MAX_FILES = 20_000
MAX_SINGLE_FILE = 100_000_000
MAX_TOTAL_SIZE = 2_000_000_000
MAX_MANIFEST_SIZE = 5_000_000
GIT_TIMEOUT_SECONDS = 60
# Local tool caches never hold source and are never committed. Each one is
# recreated on demand by the tool that owns it, so recording one in a manifest
# would make the manifest impossible to reproduce from a clean clone.
CACHE_DIRECTORIES = frozenset({
    "__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".tox", ".nox",
})
# Root-level folders that are local scratch, not source. They match the
# repository's `.gitignore`, so a maintainer's private notes or build output
# cannot block verification or slip into a release ZIP.
LOCAL_SCRATCH_DIRECTORIES = frozenset({
    "target", "log", "plan", "plans", "screenshots", "corpus", "dist",
})
# Local secret material. Git refuses to commit it and `cargo xtask secrets`
# rejects it, so a stray copy beside the code must never enter a manifest or a
# published source ZIP.
SECRET_SUFFIXES = frozenset({".pem", ".p12", ".pfx", ".key"})
WINDOWS_DEVICES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
    | {f"com{i}" for i in "¹²³"}
    | {f"lpt{i}" for i in "¹²³"}
)


class IntegrityError(ValueError):
    """Source contents or manifest failed a bounded structural or hash check."""


def permitted_path(name: str) -> bool:
    """Reject escaping, Windows-invalid, non-NFC or ambiguous archive names."""
    if (not isinstance(name, str) or not name or len(name) > 4096
            or name.startswith("/") or name.endswith("//") or "\\" in name
            or unicodedata.normalize("NFC", name) != name):
        return False
    parts = name.rstrip("/").split("/")
    for part in parts:
        try:
            component_bytes = part.encode("utf-8")
        except UnicodeEncodeError:
            return False
        if (part in ("", ".", "..") or part.endswith((" ", "."))
                or len(component_bytes) > 255):
            return False
        if any(ord(c) < 32 or ord(c) == 127 or c in '<>:"|?*' for c in part):
            return False
        if part.split(".", 1)[0].rstrip(" ").casefold() in WINDOWS_DEVICES:
            return False
    return True


def portable_key(name: str) -> str:
    """Approximate a case-insensitive filename identity for archive conflicts."""
    return "/".join(part.casefold() for part in name.rstrip("/").split("/"))


def check_component_spelling(name: str, seen: dict[str, str]) -> None:
    """Refuse alternate spellings of any path component, including directories."""
    components = name.rstrip("/").split("/")
    for count in range(1, len(components) + 1):
        original = "/".join(components[:count])
        key = portable_key(original)
        prior = seen.setdefault(key, original)
        if prior != original:
            raise IntegrityError(f"Portable path component collision: {prior} and {original}")


def ignored(rel: Path) -> bool:
    # A freshly checked-out source tree carries Git internals not distributed
    # by the ZIP. Cargo build outputs and the local devlog are similarly not
    # part of the source. Ignore only the root-level folders, not nested files.
    # Tool caches are ignored at any depth because tools write them beside the
    # code they read: `ruff` leaves `.ruff_cache` at the root of a Python tree
    # and its own `.gitignore` keeps the directory out of version control, so a
    # manifest that recorded it could never be rebuilt from a clean clone.
    parts = rel.parts
    if (".git" in parts or rel.suffix == ".pyc" or rel.suffix.lower() in SECRET_SUFFIXES
            or rel.name == ".DS_Store" or rel.name.endswith("~")
            or rel.name == ".env" or rel.name.startswith(".env.")
            or any(part in CACHE_DIRECTORIES for part in parts)):
        return True
    if not parts:
        return False
    if parts[0] in LOCAL_SCRATCH_DIRECTORIES:
        return True
    # Tool settings Git ignores inside a directory the source does ship.
    return parts[0] == ".claude" and (
        parts[1:2] == ("worktrees",) or rel.as_posix() == ".claude/settings.local.json")


def sha256_stream(stream, limit: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    while chunk := stream.read(1024 * 1024):
        count += len(chunk)
        if count > limit:
            raise IntegrityError("A file exceeds the size limit")
        digest.update(chunk)
    return count, digest.hexdigest()


def git_tracked(root: Path) -> list[str] | None:
    """Return the paths Git tracks in `root`, or None when it is not a work tree root.

    A manifest has to describe what version control can distribute. A file Git
    ignores exists on one machine only, so recording it makes the manifest
    impossible to rebuild from a clone and every clone fail verification.
    """
    if not (root / ".git").exists():
        return None
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, check=False, timeout=GIT_TIMEOUT_SECONDS,
        )
        if top.returncode != 0:
            raise IntegrityError("Git could not describe this source root")
        if Path(top.stdout.decode("utf-8", "replace").strip()).resolve() != root.resolve():
            raise IntegrityError("Tracked mode needs the repository root, not a subdirectory")
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, check=False, timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise IntegrityError(f"Git could not list tracked files: {exc}") from exc
    if listed.returncode != 0:
        raise IntegrityError("Git could not list tracked files in this source root")
    try:
        names = listed.stdout.decode("utf-8").split("\0")
    except UnicodeDecodeError as exc:
        raise IntegrityError(f"Git listed a path that is not valid UTF-8: {exc}") from exc
    return [name for name in names if name]


def parent_work_tree(root: Path) -> Path | None:
    """Return the enclosing work tree whose tracked files include `root`, if any.

    A directory extracted from a ZIP can sit inside somebody's repository
    without belonging to it, so the parent only counts when it actually tracks
    files under `root`; otherwise `create` would refuse a legitimate extraction.
    """
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True, check=False, timeout=GIT_TIMEOUT_SECONDS,
        )
        if top.returncode != 0:
            return None
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, check=False, timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    top_level = Path(top.stdout.decode("utf-8", "replace").strip()).resolve()
    if listed.returncode != 0 or top_level == root.resolve() or not listed.stdout.strip(b"\0"):
        return None
    return top_level


def source_entries(root: Path, keep: frozenset[str] = frozenset()) -> tuple[list[tuple[Path, Path]], list[str]]:
    """List source files under `root` and the paths the inventory excludes.

    Directories are pruned as they are rejected, so `.git` and a Cargo build
    directory cost one check each instead of a full traversal. Nothing is
    hashed here, so a caller that only needs paths pays no I/O for contents.
    """
    if not root.is_dir() or root.is_symlink():
        raise IntegrityError("Source root must be a real directory")
    keep_parents = set()
    for name in keep:
        parts = name.split("/")
        for count in range(1, len(parts)):
            keep_parents.add("/".join(parts[:count]))
    entries = []
    excluded = []
    for current, directories, files in os.walk(root):
        base = Path(current).relative_to(root)
        kept = []
        for name in directories:
            rel = base / name
            key = rel.as_posix()
            if (Path(current) / name).is_symlink():
                entries.append((Path(current) / name, rel))
                continue
            if key in keep_parents:
                kept.append(name)
            elif name == ".git" or ignored(rel):
                if name != ".git":
                    excluded.append(key + "/")
            else:
                kept.append(name)
        directories[:] = kept
        for name in files:
            rel = base / name
            key = rel.as_posix()
            if name == MANIFEST_NAME:
                continue
            # A path the manifest names is always checked: the exclusions only
            # decide what an inventory skips, never what a recorded file means.
            if key not in keep and ignored(rel):
                if ".git" not in rel.parts:
                    excluded.append(key)
                continue
            entries.append((Path(current) / name, rel))
    return entries, excluded


def source_records(root: Path, tracked: list[str] | None = None,
                   keep: frozenset[str] = frozenset()) -> tuple[list[dict], list[str]]:
    if not root.is_dir() or root.is_symlink():
        raise IntegrityError("Source root must be a real directory")
    result = []
    total = 0
    seen_portable = set()
    seen_spelling = {}
    if tracked is None:
        candidates, excluded = source_entries(root, keep)
    else:
        candidates = []
        excluded = []
        for name in tracked:
            rel = Path(name)
            if ignored(rel):
                raise IntegrityError(f"Tracked source matches an inventory exclusion: {name}")
            candidates.append((root / rel, rel))
    for path, rel in candidates:
        if path.is_symlink():
            raise IntegrityError(f"Source tree contains a symbolic link: {rel}")
        if path.is_dir():
            if tracked is not None:
                raise IntegrityError(f"Tracked path is a directory: {rel}")
            continue
        if not path.is_file():
            if tracked is not None:
                raise IntegrityError(
                    f"Tracked file is missing from the working tree, so a sparse checkout "
                    f"cannot produce a complete manifest: {rel}")
            raise IntegrityError(f"Source tree contains a non-regular entry: {rel}")
        name = rel.as_posix()
        if not permitted_path(name):
            raise IntegrityError(f"Unsafe source filename: {name}")
        key = portable_key(name)
        check_component_spelling(name, seen_spelling)
        if key in seen_portable:
            raise IntegrityError(f"Source filename collides on case-insensitive filesystems: {name}")
        seen_portable.add(key)
        if name == MANIFEST_NAME:
            continue
        if len(result) >= MAX_FILES or path.stat().st_size > MAX_SINGLE_FILE:
            raise IntegrityError("Source tree exceeds the file count or individual file limit")
        with path.open("rb") as stream:
            length, digest = sha256_stream(stream, MAX_SINGLE_FILE)
        total += length
        if total > MAX_TOTAL_SIZE:
            raise IntegrityError("Source tree exceeds its total size limit")
        result.append({"path": name, "bytes": length, "sha256": digest})
    result.sort(key=lambda item: item["path"])
    return result, excluded


def make_manifest(root: Path, tracked: list[str] | None = None) -> dict:
    rows, _ = source_records(root, tracked)
    return {
        "schema": SCHEMA_VERSION,
        "root": root.name,
        "algorithm": "sha256",
        "file_count": len(rows),
        "total_bytes": sum(item["bytes"] for item in rows),
        "files": rows,
    }


def parse_manifest(raw: bytes) -> dict:
    if len(raw) > MAX_MANIFEST_SIZE:
        raise IntegrityError("Manifest exceeds the size limit")
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise IntegrityError(f"Invalid JSON manifest: {exc}") from exc
    if not isinstance(result, dict) or set(result) != {
        "schema", "root", "algorithm", "file_count", "total_bytes", "files",
    }:
        raise IntegrityError("Manifest shape is invalid")
    if result["schema"] != SCHEMA_VERSION or result["algorithm"] != "sha256":
        raise IntegrityError("Manifest version or algorithm is unsupported")
    if not isinstance(result["root"], str) or not permitted_path(result["root"]) or "/" in result["root"]:
        raise IntegrityError("Manifest root is invalid")
    rows = result["files"]
    if not isinstance(rows, list) or len(rows) > MAX_FILES or not rows:
        raise IntegrityError("Manifest file list is empty, malformed or excessive")
    total = 0
    previous = ""
    seen_portable = set()
    seen_spelling = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}:
            raise IntegrityError("Malformed source manifest entry")
        name, size, digest = row["path"], row["bytes"], row["sha256"]
        if not permitted_path(name) or name == MANIFEST_NAME or name <= previous:
            raise IntegrityError("Manifest paths must be safe, unique and sorted")
        key = portable_key(name)
        check_component_spelling(name, seen_spelling)
        if key in seen_portable:
            raise IntegrityError("Manifest paths collide on case-insensitive filesystems")
        seen_portable.add(key)
        if type(size) is not int or size < 0 or size > MAX_SINGLE_FILE:
            raise IntegrityError(f"Invalid file size for {name}")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise IntegrityError(f"Invalid SHA-256 for {name}")
        previous = name
        total += size
        if total > MAX_TOTAL_SIZE:
            raise IntegrityError("Manifest claims too much data")
    if type(result["file_count"]) is not int or result["file_count"] != len(rows):
        raise IntegrityError("Manifest file count is inconsistent")
    if type(result["total_bytes"]) is not int or result["total_bytes"] != total:
        raise IntegrityError("Manifest byte total is inconsistent")
    return result


def verify_tree(root: Path, *, require_declared_root: bool = False,
                require_exact: bool = False) -> dict:
    manifest_file = root / MANIFEST_NAME
    if manifest_file.is_symlink() or not manifest_file.is_file():
        raise IntegrityError("Source manifest is missing or is a symbolic link")
    if manifest_file.stat().st_size > MAX_MANIFEST_SIZE:
        raise IntegrityError("Source manifest is too large")
    manifest = parse_manifest(manifest_file.read_bytes())
    if require_declared_root and root.name != manifest["root"]:
        raise IntegrityError("Source root differs from manifest root")
    actual, excluded = source_records(root, keep=frozenset(row["path"] for row in manifest["files"]))
    if actual != manifest["files"]:
        raise IntegrityError("Source files are missing, changed or extra")
    if require_exact and excluded:
        raise IntegrityError(
            "Tree holds entries the inventory excludes: "
            + ", ".join(sorted(set(excluded))[:5]))
    return {
        "status": "verified", "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "manifest_sha256": hashlib.sha256(manifest_file.read_bytes()).hexdigest(),
        "declared_root": manifest["root"], "source_root": root.name,
        "root_policy": "strict" if require_declared_root else "compatible",
        "root_renamed": root.name != manifest["root"],
        # What verification did not look at. A tree can only be padded with
        # these, so a caller that needs "exactly the manifest" reads verify_zip.
        "excluded": sorted(set(excluded))[:20],
        "excluded_count": len(set(excluded)),
    }


def verify_zip(archive: Path | BinaryIO, *, require_declared_root: bool = False) -> dict:
    try:
        with zipfile.ZipFile(archive, "r") as zf:
            entries = zf.infolist()
            if len(entries) > MAX_FILES * 2:
                raise IntegrityError("ZIP entry count is excessive")
            seen = set()
            seen_portable = set()
            seen_spelling = {}
            regular = {}
            total_uncompressed = 0
            for item in entries:
                name = item.filename
                if not permitted_path(name) or name in seen:
                    raise IntegrityError(f"Unsafe or duplicate ZIP entry: {name}")
                seen.add(name)
                key = portable_key(name)
                check_component_spelling(name, seen_spelling)
                if key in seen_portable:
                    raise IntegrityError(f"Portable filename collision in ZIP: {name}")
                seen_portable.add(key)
                if stat.S_IFMT(item.external_attr >> 16) == stat.S_IFLNK:
                    raise IntegrityError(f"ZIP entry is a symbolic link: {name}")
                if item.is_dir():
                    continue
                if item.file_size > MAX_SINGLE_FILE:
                    raise IntegrityError("ZIP contains a file over the per-file limit")
                total_uncompressed += item.file_size
                if total_uncompressed > MAX_TOTAL_SIZE + MAX_MANIFEST_SIZE:
                    raise IntegrityError("ZIP uncompressed size is excessive")
                regular[name] = item
            file_keys = {portable_key(name) for name in regular}
            for key in file_keys:
                pieces = key.split("/")
                if any("/".join(pieces[:n]) in file_keys for n in range(1, len(pieces))):
                    raise IntegrityError("ZIP has a file used as a parent directory")
            manifest_candidates = [name for name in regular if name.endswith("/" + MANIFEST_NAME)]
            if len(manifest_candidates) != 1:
                raise IntegrityError("ZIP must contain one source integrity manifest")
            manifest_name = manifest_candidates[0]
            if regular[manifest_name].file_size > MAX_MANIFEST_SIZE:
                raise IntegrityError("ZIP manifest is too large")
            with zf.open(regular[manifest_name], "r") as stream:
                manifest_raw = stream.read(MAX_MANIFEST_SIZE + 1)
            manifest = parse_manifest(manifest_raw)
            source_root = manifest_name[:-len(MANIFEST_NAME)].rstrip("/")
            if "/" in source_root or not permitted_path(source_root):
                raise IntegrityError("ZIP source root is invalid")
            if require_declared_root and source_root != manifest["root"]:
                raise IntegrityError("ZIP manifest is not at the declared source root")
            prefix = source_root + "/"
            if any(
                item.is_dir() and item.filename != prefix and not item.filename.startswith(prefix)
                for item in entries
            ):
                raise IntegrityError("ZIP contains a directory outside its source root")
            observed = {}
            for name, item in regular.items():
                if name == manifest_name:
                    continue
                if not name.startswith(prefix):
                    raise IntegrityError(f"ZIP has an unexpected source location: {name}")
                relative = name[len(prefix):]
                if not permitted_path(relative):
                    raise IntegrityError(f"Unsafe ZIP source entry: {name}")
                with zf.open(item, "r") as stream:
                    count, digest = sha256_stream(stream, MAX_SINGLE_FILE)
                observed[relative] = {"path": relative, "bytes": count, "sha256": digest}
            if [observed[k] for k in sorted(observed)] != manifest["files"]:
                raise IntegrityError("ZIP source files are missing, changed or extra")
            return {
                "status": "verified", "file_count": manifest["file_count"],
                "total_bytes": manifest["total_bytes"],
                "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
                "declared_root": manifest["root"], "source_root": source_root,
                "root_policy": "strict" if require_declared_root else "compatible",
                "root_renamed": source_root != manifest["root"],
            }
    except (zipfile.BadZipFile, RuntimeError, EOFError, zlib.error) as exc:
        raise IntegrityError(f"ZIP cannot be read safely: {exc}") from exc


def write_zip_member(zf: zipfile.ZipFile, path: Path, name: str) -> None:
    """Write one member with normalized metadata so equal trees make equal ZIPs."""
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o644 << 16
    with path.open("rb") as source, zf.open(info, "w") as destination:
        shutil.copyfileobj(source, destination, length=1024 * 1024)


def package_zip(root: Path, archive: Path) -> dict:
    """Write a ZIP at the manifest's declared root and verify the produced file."""
    if archive.exists() or archive.is_symlink():
        raise IntegrityError(f"Refusing to overwrite an existing archive: {archive}")
    parent = archive.parent
    if parent.is_symlink() or not parent.is_dir():
        raise IntegrityError("Archive parent must be an existing, non-symlink directory")
    if archive.resolve().is_relative_to(root.resolve()):
        raise IntegrityError("Archive must be outside the source root")
    manifest_file = root / MANIFEST_NAME
    if manifest_file.is_symlink() or not manifest_file.is_file():
        raise IntegrityError("Source manifest is missing or is a symbolic link")
    manifest = parse_manifest(manifest_file.read_bytes())
    rows, _ = source_records(root, keep=frozenset(row["path"] for row in manifest["files"]))
    if rows != manifest["files"]:
        raise IntegrityError("Source files are missing, changed or extra")
    descriptor, temporary = tempfile.mkstemp(prefix=".source-zip-", dir=parent)
    os.close(descriptor)
    staged = Path(temporary)
    try:
        with zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as zf:
            prefix = manifest["root"] + "/"
            write_zip_member(zf, manifest_file, prefix + MANIFEST_NAME)
            for row in manifest["files"]:
                write_zip_member(zf, root / row["path"], prefix + row["path"])
        verify_zip(staged, require_declared_root=True)
        with tempfile.TemporaryDirectory(prefix=".package-check-", dir=parent) as check_parent:
            extract_verified_zip(staged, Path(check_parent), require_declared_root=True)
        try:
            destination_fd = os.open(archive, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o666)
        except FileExistsError as exc:
            raise IntegrityError(f"Refusing to overwrite an existing archive: {archive}") from exc
        destination = os.fdopen(destination_fd, "w+b")
        try:
            with staged.open("rb") as source:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
            destination.seek(0)
            verified = verify_zip(destination, require_declared_root=True)
            destination.seek(0)
            _, archive_digest = sha256_stream(destination, MAX_TOTAL_SIZE + MAX_MANIFEST_SIZE)
        except BaseException:
            try:
                destination.close()
            except OSError:
                pass
            try:
                archive.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        destination.close()
        return {
            "status": "packaged", "archive": str(archive),
            "archive_sha256": archive_digest,
            "file_count": verified["file_count"], "total_bytes": verified["total_bytes"],
            "manifest_sha256": verified["manifest_sha256"],
            "declared_root": verified["declared_root"], "source_root": verified["source_root"],
            "root_policy": "strict",
            "root_renamed": verified["root_renamed"],
        }
    finally:
        if staged.exists():
            staged.unlink()


def extract_verified_zip(archive: Path | BinaryIO, output_parent: Path, *,
                         require_declared_root: bool = False) -> dict:
    """Verify, stage, rehash and install a ZIP without touching existing output.

    All reads use one open descriptor, or the caller's seekable stream:
    replacing the pathname between verification and extraction cannot redirect
    the second read. Extraction uses a private directory beside the eventual
    source root. Do not use a shared output directory with adversarial
    concurrent writers.
    """
    if output_parent.is_symlink() or not output_parent.is_dir():
        raise IntegrityError("Output parent must be an existing, non-symlink directory")
    output_parent = output_parent.resolve()
    source_context = (archive.open("rb") if isinstance(archive, (str, os.PathLike))
                      else nullcontext(archive))
    try:
        with source_context as source:
            verified = verify_zip(source, require_declared_root=require_declared_root)
            source.seek(0)
            with zipfile.ZipFile(source, "r") as zf:
                manifest_paths = [
                    info.filename for info in zf.infolist()
                    if info.filename.endswith("/" + MANIFEST_NAME) and not info.is_dir()
                ]
                if len(manifest_paths) != 1:
                    raise IntegrityError("Missing or ambiguous verified manifest")
                source_root_name = manifest_paths[0].split("/", 1)[0]
                final_root = output_parent / source_root_name
                if final_root.exists() or final_root.is_symlink():
                    raise IntegrityError(f"Refusing to overwrite an existing source root: {final_root}")
                with tempfile.TemporaryDirectory(prefix=".verified-source-", dir=output_parent) as stage:
                    staged_root = Path(stage) / source_root_name
                    staged_root.mkdir(mode=0o700)
                    total = 0
                    for info in zf.infolist():
                        name = info.filename
                        if name == source_root_name + "/":
                            continue
                        if not name.startswith(source_root_name + "/"):
                            raise IntegrityError("ZIP member outside verified source root")
                        rel = name[len(source_root_name) + 1:].rstrip("/")
                        if not permitted_path(rel):
                            raise IntegrityError(f"Unsafe ZIP member during extraction: {name}")
                        destination = staged_root / rel
                        if info.is_dir():
                            destination.mkdir(parents=True, exist_ok=True)
                            continue
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        length = 0
                        with zf.open(info, "r") as src, destination.open("xb") as dst:
                            while chunk := src.read(1024 * 1024):
                                length += len(chunk)
                                total += len(chunk)
                                if length > MAX_SINGLE_FILE or total > MAX_TOTAL_SIZE + MAX_MANIFEST_SIZE:
                                    raise IntegrityError("Extraction exceeds source size limits")
                                dst.write(chunk)
                        if length != info.file_size:
                            raise IntegrityError("ZIP member size changed during extraction")
                    unpacked = verify_tree(staged_root, require_declared_root=require_declared_root)
                    if (unpacked["file_count"] != verified["file_count"]
                            or unpacked["manifest_sha256"] != verified["manifest_sha256"]):
                        raise IntegrityError("Extracted source does not match verified ZIP")
                    if final_root.exists() or final_root.is_symlink():
                        raise IntegrityError("Output source root appeared during staging")
                    os.rename(staged_root, final_root)
                    return {
                        "status": "extracted", "source_root": str(final_root),
                        "file_count": unpacked["file_count"],
                        "manifest_sha256": unpacked["manifest_sha256"],
                        "root_policy": "strict" if require_declared_root else "compatible",
                        "root_renamed": unpacked["root_renamed"],
                    }
    except (zipfile.BadZipFile, RuntimeError, EOFError, zlib.error) as exc:
        raise IntegrityError(f"ZIP extraction failed: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    for mode in ("create", "verify-tree"):
        p = modes.add_parser(mode)
        p.add_argument("--root", type=Path, required=True)
        if mode == "create":
            p.add_argument("--tracked", action=argparse.BooleanOptionalAction, default=None,
                           help="List files with Git instead of walking the tree "
                                "(default: on inside a Git work tree root)")
        else:
            p.add_argument("--require-declared-root", action="store_true",
                           help="Refuse a folder whose name differs from the manifest root")
            p.add_argument("--require-exact", action="store_true",
                           help="Refuse a tree that holds anything the inventory excludes")
    pzip = modes.add_parser("verify-zip")
    pzip.add_argument("--archive", type=Path, required=True)
    pzip.add_argument("--require-declared-root", action="store_true",
                      help="Refuse an archive whose top-level folder differs from the manifest root")
    ppackage = modes.add_parser("package-zip")
    ppackage.add_argument("--root", type=Path, required=True)
    ppackage.add_argument("--archive", type=Path, required=True,
                          help="New ZIP path; an existing archive is never overwritten")
    pextract = modes.add_parser("extract-zip")
    pextract.add_argument("--archive", type=Path, required=True)
    pextract.add_argument("--output-parent", type=Path, required=True,
                          help="Existing directory for the new source root; never overwrites a root")
    pextract.add_argument("--require-declared-root", action="store_true",
                          help="Refuse an archive whose top-level folder differs from the manifest root")
    args = parser.parse_args(argv)
    try:
        if args.mode == "create":
            root = args.root.absolute()
            existing = root / MANIFEST_NAME
            if existing.is_symlink():
                raise IntegrityError("Cannot replace a symbolic-link manifest")
            tracked = None
            if args.tracked is not False:
                if args.tracked or (root / ".git").exists():
                    tracked = git_tracked(root)
                    if tracked is None:
                        raise IntegrityError("--tracked needs a Git work tree root")
                else:
                    parent = parent_work_tree(root)
                    if parent is not None:
                        raise IntegrityError(
                            f"Run create from the repository root, not a subdirectory of {parent}")
            manifest = make_manifest(root, tracked)
            if tracked is not None:
                recorded = {row["path"] for row in manifest["files"]}
                walked, _ = source_entries(root)
                extra = sorted({rel.as_posix() for _, rel in walked} - recorded)
                if extra:
                    shown = ", ".join(extra[:5])
                    raise IntegrityError(
                        f"Working tree has files Git does not track, so verification would "
                        f"reject the tree: {shown}; commit, ignore or remove them")
            content = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
            if len(content) > MAX_MANIFEST_SIZE:
                raise IntegrityError("Generated manifest is too large")
            existing.write_bytes(content)
            result = verify_tree(root)
            result["action"] = "created"
            result["file_source"] = "git" if tracked is not None else "filesystem"
        elif args.mode == "verify-tree":
            result = verify_tree(args.root.absolute(),
                                 require_declared_root=args.require_declared_root,
                                 require_exact=args.require_exact)
        elif args.mode == "package-zip":
            result = package_zip(args.root.absolute(), args.archive.absolute())
        elif args.mode == "verify-zip":
            result = verify_zip(args.archive, require_declared_root=args.require_declared_root)
        else:
            result = extract_verified_zip(args.archive, args.output_parent,
                                          require_declared_root=args.require_declared_root)
    except (IntegrityError, OSError, ValueError) as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in ("verified", "packaged", "extracted") else 1


if __name__ == "__main__":
    sys.exit(main())
