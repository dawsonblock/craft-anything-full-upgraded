#!/usr/bin/env python3
"""Create and independently check a complete, bounded Craft Anything source manifest.

This is a checksum/inventory check, not a digital signature or a Rust build.
It uses only the Python standard library and never executes bundled source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
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
    return (".git" in rel.parts or "__pycache__" in rel.parts
            or rel.suffix == ".pyc"
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


def source_records(root: Path) -> list[dict]:
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


def make_manifest(root: Path) -> dict:
    rows = source_records(root)
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


def verify_tree(root: Path) -> dict:
    manifest_file = root / MANIFEST_NAME
    if manifest_file.is_symlink() or not manifest_file.is_file():
        raise IntegrityError("Source manifest is missing or is a symbolic link")
    if manifest_file.stat().st_size > MAX_MANIFEST_SIZE:
        raise IntegrityError("Source manifest is too large")
    manifest = parse_manifest(manifest_file.read_bytes())
    actual = source_records(root)
    if actual != manifest["files"]:
        raise IntegrityError("Source files are missing, changed or extra")
    return {
        "status": "verified", "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "manifest_sha256": hashlib.sha256(manifest_file.read_bytes()).hexdigest(),
        "declared_root": manifest["root"], "source_root": root.name,
    }


def verify_zip(archive: Path | BinaryIO) -> dict:
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
            }
    except (zipfile.BadZipFile, RuntimeError, EOFError, zlib.error) as exc:
        raise IntegrityError(f"ZIP cannot be read safely: {exc}") from exc


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
    if source_records(root) != manifest["files"]:
        raise IntegrityError("Source files are missing, changed or extra")
    descriptor, temporary = tempfile.mkstemp(prefix=".source-zip-", dir=parent)
    os.close(descriptor)
    staged = Path(temporary)
    try:
        with zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as zf:
            prefix = manifest["root"] + "/"
            zf.write(manifest_file, prefix + MANIFEST_NAME)
            for row in manifest["files"]:
                zf.write(root / row["path"], prefix + row["path"])
        verified = verify_zip(staged)
        with staged.open("rb") as stream:
            _, archive_digest = sha256_stream(stream, MAX_TOTAL_SIZE + MAX_MANIFEST_SIZE)
        if archive.exists() or archive.is_symlink():
            raise IntegrityError("Archive appeared during packaging")
        os.rename(staged, archive)
        return {
            "status": "packaged", "archive": str(archive),
            "archive_sha256": archive_digest,
            "file_count": verified["file_count"], "total_bytes": verified["total_bytes"],
            "manifest_sha256": verified["manifest_sha256"],
            "declared_root": verified["declared_root"], "source_root": verified["source_root"],
        }
    finally:
        if staged.exists():
            staged.unlink()


def extract_verified_zip(archive: Path, output_parent: Path) -> dict:
    """Verify, stage, rehash and install a ZIP without touching existing output.

    All reads of the archive use one open descriptor: replacing its pathname
    between verification and extraction cannot redirect the second read.
    Extraction uses a private directory beside the eventual source root.
    Do not use a shared output directory with adversarial concurrent writers.
    """
    if output_parent.is_symlink() or not output_parent.is_dir():
        raise IntegrityError("Output parent must be an existing, non-symlink directory")
    output_parent = output_parent.resolve()
    try:
        with archive.open("rb") as source:
            verified = verify_zip(source)
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
                    unpacked = verify_tree(staged_root)
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
                    }
    except (zipfile.BadZipFile, RuntimeError, EOFError, zlib.error) as exc:
        raise IntegrityError(f"ZIP extraction failed: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    for mode in ("create", "verify-tree"):
        p = modes.add_parser(mode)
        p.add_argument("--root", type=Path, required=True)
    pzip = modes.add_parser("verify-zip")
    pzip.add_argument("--archive", type=Path, required=True)
    ppackage = modes.add_parser("package-zip")
    ppackage.add_argument("--root", type=Path, required=True)
    ppackage.add_argument("--archive", type=Path, required=True,
                          help="New ZIP path; an existing archive is never overwritten")
    pextract = modes.add_parser("extract-zip")
    pextract.add_argument("--archive", type=Path, required=True)
    pextract.add_argument("--output-parent", type=Path, required=True,
                          help="Existing directory for the new source root; never overwrites a root")
    args = parser.parse_args(argv)
    try:
        if args.mode == "create":
            root = args.root.absolute()
            existing = root / MANIFEST_NAME
            if existing.is_symlink():
                raise IntegrityError("Cannot replace a symbolic-link manifest")
            content = (json.dumps(make_manifest(root), indent=2, sort_keys=True) + "\n").encode("utf-8")
            if len(content) > MAX_MANIFEST_SIZE:
                raise IntegrityError("Generated manifest is too large")
            existing.write_bytes(content)
            result = verify_tree(root)
            result["action"] = "created"
        elif args.mode == "verify-tree":
            result = verify_tree(args.root.absolute())
        elif args.mode == "package-zip":
            result = package_zip(args.root.absolute(), args.archive.absolute())
        elif args.mode == "verify-zip":
            result = verify_zip(args.archive)
        else:
            result = extract_verified_zip(args.archive, args.output_parent)
    except (IntegrityError, OSError, ValueError) as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in ("verified", "packaged", "extracted") else 1


if __name__ == "__main__":
    sys.exit(main())
