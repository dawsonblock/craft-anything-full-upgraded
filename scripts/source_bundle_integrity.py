#!/usr/bin/env python3
"""Create and independently check a bounded Craft Anything source manifest.

Every tracked path is inventoried except the living records the contribution rules edit in almost
every pull request (`LIVING_RECORDS`) and what `ignored()` leaves out; those are process records
rather than source, and hashing them would invalidate the manifest on nearly every commit.
This is a checksum/inventory check, not a digital signature or a Rust build.
It uses only the Python standard library and never executes bundled source.
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
# The top-level folder every release ZIP carries, whatever the checkout happens to be called.
CANONICAL_ROOT = "craft-anything-full-upgraded"
SCHEMA_VERSION = 1
MAX_FILES = 20_000
MAX_SINGLE_FILE = 100_000_000
MAX_TOTAL_SIZE = 2_000_000_000
MAX_MANIFEST_SIZE = 5_000_000
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


TOOL_CACHES = frozenset({".ruff_cache", ".pytest_cache", ".mypy_cache"})

# Living records the contribution rules require editing in almost every pull request
# ("the roadmap item ticked, STATUS.md if what works changed"). Hashing them would invalidate the
# manifest on every commit, so a clean checkout stops verifying the moment anyone follows those
# rules, and two concurrent PRs both rewrite a 158 KB generated file. They are process records,
# not build inputs, so they are neither inventoried nor packaged. The archive's own bytes are
# still bound to the commit by the release attestation.
LIVING_RECORDS = frozenset({"docs/ROADMAP.md", "docs/STATUS.md"})


def ignored(rel: Path) -> bool:
    # A freshly checked-out source tree carries Git internals not distributed
    # by the ZIP. Cargo build outputs, the local devlog and Python tool caches
    # are similarly not part of the source. Ignore only the root-level folders
    # (tool caches excepted: a run from a subfolder puts one beside the code),
    # not nested files.
    return (rel.as_posix() in LIVING_RECORDS
            or ".git" in rel.parts or "__pycache__" in rel.parts
            or rel.suffix == ".pyc"
            or any(part in TOOL_CACHES for part in rel.parts)
            or (bool(rel.parts) and rel.parts[0] in ("target", "log")))


def sha256_stream(stream, limit: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    while chunk := stream.read(1024 * 1024):
        count += len(chunk)
        if count > limit:
            raise IntegrityError("A file exceeds the size limit")
        digest.update(chunk)
    return count, digest.hexdigest()


def tracked_files(root: Path) -> set[str] | None:
    """The paths Git tracks under `root`, or `None` when this isn't a work tree.

    A manifest made by walking the working tree records whatever scratch files happen to be there,
    and a clean checkout then fails to match it. In a work tree, list what Git tracks instead; a
    tree without `.git` (an extracted ZIP) keeps the walk.
    """
    if not (root / ".git").exists():
        return None
    try:
        run = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if run.returncode != 0:
        return None
    return {part.decode("utf-8", "surrogateescape") for part in run.stdout.split(b"\0") if part}


def source_records(root: Path, only: set[str] | None = None) -> list[dict]:
    if not root.is_dir() or root.is_symlink():
        raise IntegrityError("Source root must be a real directory")
    result = []
    total = 0
    seen_portable = set()
    seen_spelling = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if ignored(rel):
            continue
        if path.is_symlink():
            raise IntegrityError(f"Source tree contains a symbolic link: {rel}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise IntegrityError(f"Source tree contains a non-regular entry: {rel}")
        name = rel.as_posix()
        if not permitted_path(name):
            raise IntegrityError(f"Unsafe source filename: {name}")
        if only is not None and name not in only:
            continue
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
    return result


def make_manifest(root: Path, root_name: str | None = None) -> dict:
    rows = source_records(root, tracked_files(root))
    return {
        "schema": SCHEMA_VERSION,
        "root": root_name or root.name,
        "algorithm": "sha256",
        "file_count": len(rows),
        "total_bytes": sum(item["bytes"] for item in rows),
        "files": rows,
    }


def require_clean_work_tree(root: Path) -> None:
    """Refuse to package a checkout whose tracked files differ from its commit.

    The manifest is generated from the tree at packaging time, so a modified file would be
    described faithfully and still ship — and the release would attest those bytes as coming from
    a commit they never came from. Asking Git is the only thing that catches it.
    """
    if not (root / ".git").exists():
        return
    try:
        run = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise IntegrityError(
            "Cannot ask Git whether the source tree matches its commit"
        ) from exc
    if run.returncode != 0:
        raise IntegrityError("Cannot ask Git whether the source tree matches its commit")
    if run.stdout.strip():
        raise IntegrityError(
            "The source tree has uncommitted changes: commit them before packaging, so the "
            "archive is the commit the release attests to"
        )


def tree_integrity(root: Path, *, require_declared_root: bool = False) -> dict:
    """The tree's own inventory: the packaged manifest when there is one, else one generated now.

    An extracted release ZIP carries the manifest generated when it was packaged, so that one is
    verified and used. A checkout has none: it is checked against its commit and inventoried from
    Git. A folder with neither cannot be checked, and is refused rather than assumed good.
    """
    manifest_file = root / MANIFEST_NAME
    if manifest_file.is_file() and not manifest_file.is_symlink():
        verify_tree(root, require_declared_root=require_declared_root)
        return {"basis": "manifest", "manifest": parse_manifest(manifest_file.read_bytes())}
    if not (root / ".git").exists():
        raise IntegrityError(
            "The source root carries no source manifest and is not a Git checkout, so there is "
            "nothing to check its contents against")
    require_clean_work_tree(root)
    return {"basis": "worktree", "manifest": make_manifest(root)}


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


def verify_tree(root: Path, *, require_declared_root: bool = False) -> dict:
    manifest_file = root / MANIFEST_NAME
    if manifest_file.is_symlink() or not manifest_file.is_file():
        raise IntegrityError("Source manifest is missing or is a symbolic link")
    if manifest_file.stat().st_size > MAX_MANIFEST_SIZE:
        raise IntegrityError("Source manifest is too large")
    manifest = parse_manifest(manifest_file.read_bytes())
    if require_declared_root and root.name != manifest["root"]:
        raise IntegrityError("Source root differs from manifest root")
    actual = source_records(root)
    if actual != manifest["files"]:
        raise IntegrityError("Source files are missing, changed or extra")
    return {
        "status": "verified", "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "manifest_sha256": hashlib.sha256(manifest_file.read_bytes()).hexdigest(),
        "declared_root": manifest["root"], "source_root": root.name,
        "root_policy": "strict" if require_declared_root else "compatible",
        "root_renamed": root.name != manifest["root"],
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


def write_zip_member_bytes(zf: zipfile.ZipFile, data: bytes, name: str) -> None:
    """Write one in-memory member with the same normalized metadata as a file."""
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def package_zip(root: Path, archive: Path, *, root_name: str = CANONICAL_ROOT) -> dict:
    """Generate the manifest from the tree, write the ZIP at `root_name` and verify the file.

    The manifest is built here rather than read from the checkout: a committed copy has to be
    regenerated by hand after every source change, and one forgotten refresh blocks a release
    while every test still passes. Generating it makes the ZIP describe exactly the bytes being
    packaged, which is what the release attestation binds to the commit.
    """
    if archive.exists() or archive.is_symlink():
        raise IntegrityError(f"Refusing to overwrite an existing archive: {archive}")
    parent = archive.parent
    if parent.is_symlink() or not parent.is_dir():
        raise IntegrityError("Archive parent must be an existing, non-symlink directory")
    if archive.resolve().is_relative_to(root.resolve()):
        raise IntegrityError("Archive must be outside the source root")
    if "/" in root_name or not permitted_path(root_name):
        raise IntegrityError("The source root name must be one safe path component")
    require_clean_work_tree(root)
    manifest = make_manifest(root, root_name)
    manifest_raw = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(manifest_raw) > MAX_MANIFEST_SIZE:
        raise IntegrityError("Generated manifest is too large")
    descriptor, temporary = tempfile.mkstemp(prefix=".source-zip-", dir=parent)
    os.close(descriptor)
    staged = Path(temporary)
    try:
        with zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as zf:
            prefix = root_name + "/"
            write_zip_member_bytes(zf, manifest_raw, prefix + MANIFEST_NAME)
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
                    # Create the final root exclusively, then move the staged entries into it: a
                    # rename would replace an empty directory another writer made in between.
                    try:
                        os.mkdir(final_root, 0o700)
                    except FileExistsError as exc:
                        raise IntegrityError("Output source root appeared during staging") from exc
                    try:
                        for entry in staged_root.iterdir():
                            os.rename(entry, final_root / entry.name)
                    except BaseException:
                        shutil.rmtree(final_root, ignore_errors=True)
                        raise
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
        if mode == "verify-tree":
            p.add_argument("--require-declared-root", action="store_true",
                           help="Refuse a folder whose name differs from the manifest root")
    pzip = modes.add_parser("verify-zip")
    pzip.add_argument("--archive", type=Path, required=True)
    pzip.add_argument("--require-declared-root", action="store_true",
                      help="Refuse an archive whose top-level folder differs from the manifest root")
    ppackage = modes.add_parser("package-zip")
    ppackage.add_argument("--root", type=Path, required=True)
    ppackage.add_argument("--archive", type=Path, required=True,
                          help="New ZIP path; an existing archive is never overwritten")
    ppackage.add_argument("--root-name", default=CANONICAL_ROOT,
                          help=f"Top-level folder in the archive (default: {CANONICAL_ROOT})")
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
            # The committed manifest names the canonical source root. Re-baselining a renamed
            # checkout would silently move it, and the next ZIP would carry the wrong name, so
            # keep the name and tell the person to rename the folder instead.
            if existing.is_file():
                try:
                    declared = parse_manifest(existing.read_bytes())["root"]
                except IntegrityError:
                    declared = None
                if declared is not None and declared != root.name:
                    raise IntegrityError(
                        f"The manifest declares the source root {declared!r}, but this folder is "
                        f"{root.name!r}: rename the folder to the declared root instead of "
                        "re-baselining under another name")
            content = (json.dumps(make_manifest(root), indent=2, sort_keys=True) + "\n").encode("utf-8")
            if len(content) > MAX_MANIFEST_SIZE:
                raise IntegrityError("Generated manifest is too large")
            # A work tree can hold files Git doesn't track (scratch notes, a local cache). They are
            # not source, and recording them makes a clean checkout fail, so refuse instead of
            # writing a manifest that describes this tree only.
            if source_records(root) != json.loads(content)["files"]:
                raise IntegrityError(
                    "The tree has files Git doesn't track, which are not source: commit, ignore "
                    "or remove them before re-baselining the manifest")
            existing.write_bytes(content)
            result = verify_tree(root)
            result["action"] = "created"
        elif args.mode == "verify-tree":
            result = verify_tree(args.root.absolute(),
                                 require_declared_root=args.require_declared_root)
        elif args.mode == "package-zip":
            result = package_zip(args.root.absolute(), args.archive.absolute(),
                                 root_name=args.root_name)
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
