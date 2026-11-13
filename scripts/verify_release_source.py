#!/usr/bin/env python3
"""Verify a source ZIP attached to a published release and write a receipt.

The archive must carry SOURCE_INTEGRITY.json under the manifest's canonical
top-level folder. It is checked without extracting, unpacked into a private
temporary folder, and the extracted tree is rehashed. Archived source is never
executed. This is an inventory and packaging check, not a signature.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import BinaryIO

from source_bundle_integrity import (
    MAX_MANIFEST_SIZE, MAX_TOTAL_SIZE, IntegrityError, extract_verified_zip,
    sha256_stream, verify_zip,
)

SCHEMA_VERSION = 1
MAX_ARCHIVE_BYTES = MAX_TOTAL_SIZE + MAX_MANIFEST_SIZE
DEFAULT_ASSET = "*source*.zip"
DOWNLOAD_TIMEOUT_SECONDS = 600
ERROR_EXCERPT = 4000
REPO_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+/-]{0,255}$")
ASSET_PATTERN = re.compile(r"^[A-Za-z0-9*?][A-Za-z0-9._*?\[\]-]{0,255}$")


def validate_release_identity(repo: str, tag: str, asset: str) -> None:
    """Reject values that could be mistaken for options or shell syntax."""
    if not REPO_PATTERN.fullmatch(repo):
        raise IntegrityError(
            "Repository must be OWNER/NAME with only letters, digits, '.', '_' or '-'"
        )
    if not TAG_PATTERN.fullmatch(tag):
        raise IntegrityError("Release tag has unsupported characters")
    if not ASSET_PATTERN.fullmatch(asset):
        raise IntegrityError("Asset pattern has unsupported characters")


def open_regular_archive(path: Path) -> BinaryIO:
    """Open one archive inode without following a symlink where supported."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise IntegrityError(
            f"Cannot open the release source ZIP: {exc.strerror or exc}"
        ) from exc
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        os.close(descriptor)
        raise IntegrityError("The release source ZIP must be a regular file")
    return os.fdopen(descriptor, "rb")


def download_release(repo: str, tag: str, asset: str, destination: Path) -> Path:
    """Download exactly one matching release asset with the GitHub CLI."""
    validate_release_identity(repo, tag, asset)
    command = [
        "gh", "release", "download", tag, "--repo", repo,
        "--pattern", asset, "--dir", str(destination),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=DOWNLOAD_TIMEOUT_SECONDS, check=False,
        )
    except FileNotFoundError as exc:
        raise IntegrityError("The GitHub CLI (gh) is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise IntegrityError("Downloading the release asset timed out") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:ERROR_EXCERPT]
        raise IntegrityError(
            f"Could not download {asset} from {repo} {tag}: {detail or 'gh failed'}"
        )
    files = [
        path for path in destination.iterdir()
        if path.is_file() and not path.is_symlink()
    ]
    if len(files) != 1:
        raise IntegrityError(
            f"Release {repo} {tag} must attach exactly one source ZIP matching {asset}; "
            f"found {len(files)}"
        )
    return files[0]


def verify_release_archive(
    archive: Path, *, repo: str | None = None, tag: str | None = None,
    asset: str | None = None,
) -> dict:
    """Verify one release ZIP, re-verify its extracted tree and describe it."""
    if archive.is_symlink():
        raise IntegrityError("The release source ZIP must be a regular file")
    with open_regular_archive(archive) as source:
        archive_bytes, archive_digest = sha256_stream(source, MAX_ARCHIVE_BYTES)
        source.seek(0)
        verified = verify_zip(source, require_declared_root=True)
        source.seek(0)
        with tempfile.TemporaryDirectory(prefix=".release-source-") as install:
            extracted = extract_verified_zip(
                source, Path(install), require_declared_root=True,
            )
    if (extracted["file_count"] != verified["file_count"]
            or extracted["manifest_sha256"] != verified["manifest_sha256"]):
        raise IntegrityError("Extracted source does not match the verified release ZIP")
    receipt = {
        "schema": SCHEMA_VERSION,
        "status": "verified",
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "archive_name": archive.name,
        "archive_bytes": archive_bytes,
        "archive_sha256": archive_digest,
        "manifest_sha256": verified["manifest_sha256"],
        "file_count": verified["file_count"],
        "total_bytes": verified["total_bytes"],
        "declared_root": verified["declared_root"],
        "root_renamed": verified["root_renamed"],
    }
    if repo is not None:
        receipt["release"] = {"repo": repo, "tag": tag, "asset": asset}
    return receipt


def write_receipt(destination: Path, receipt: dict) -> None:
    text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    try:
        with destination.open("x", encoding="utf-8") as output:
            output.write(text)
    except FileExistsError as exc:
        raise IntegrityError("Refusing to overwrite an existing receipt") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path,
                        help="An already downloaded release source ZIP")
    source.add_argument("--repo", help="OWNER/NAME of the repository holding the release")
    parser.add_argument("--tag", help="Release tag, required with --repo")
    parser.add_argument("--asset", default=DEFAULT_ASSET,
                        help=f"Asset glob to download (default: {DEFAULT_ASSET})")
    parser.add_argument("--report", type=Path,
                        help="JSON receipt path OUTSIDE the checkout")
    parser.add_argument("--root", type=Path, default=Path("."),
                        help="Checkout root the receipt must stay outside of")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    destination = args.report.resolve() if args.report else None
    if destination is not None and destination.is_relative_to(root):
        print(json.dumps({
            "schema": SCHEMA_VERSION, "status": "error",
            "error": "Place the receipt outside the source tree",
        }, indent=2))
        return 1
    if destination is not None and destination.exists():
        print(json.dumps({
            "schema": SCHEMA_VERSION, "status": "error",
            "error": "Refusing to overwrite an existing receipt",
        }, indent=2))
        return 1
    try:
        if args.archive is not None:
            receipt = verify_release_archive(args.archive)
        else:
            if not args.tag:
                raise IntegrityError("--tag is required with --repo")
            with tempfile.TemporaryDirectory(prefix=".release-download-") as download:
                archive = download_release(
                    args.repo, args.tag, args.asset, Path(download),
                )
                receipt = verify_release_archive(
                    archive, repo=args.repo, tag=args.tag, asset=args.asset,
                )
    except (IntegrityError, OSError, ValueError) as exc:
        receipt = {"schema": SCHEMA_VERSION, "status": "error", "error": str(exc)}
    text = json.dumps(receipt, indent=2, sort_keys=True)
    if destination is not None:
        try:
            write_receipt(destination, receipt)
        except (IntegrityError, OSError) as exc:
            print(json.dumps({
                "schema": SCHEMA_VERSION, "status": "error", "error": str(exc),
            }, indent=2))
            return 1
    print(text)
    return 0 if receipt["status"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
