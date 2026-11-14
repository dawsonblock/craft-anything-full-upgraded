#!/usr/bin/env python3
"""Verify a source ZIP attached to a published release and write a receipt.

The archive must carry SOURCE_INTEGRITY.json under the manifest's canonical
top-level folder. It is checked without extracting, unpacked into a private
temporary folder, and the extracted tree is rehashed. Archived source is never
executed. This is an inventory and packaging check, not a signature.

A receipt can also be bound to the commit a release should represent and to a
preflight receipt written before publication. With --verify-attestation, the
archive's GitHub artifact attestation must cover the same bytes.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import BinaryIO
import urllib.parse

from source_bundle_integrity import (
    MAX_MANIFEST_SIZE, MAX_TOTAL_SIZE, IntegrityError, extract_verified_zip,
    sha256_stream, verify_zip,
)

SCHEMA_VERSION = 1
MAX_ARCHIVE_BYTES = MAX_TOTAL_SIZE + MAX_MANIFEST_SIZE
DEFAULT_ASSET = "*source*.zip"
DEFAULT_SIGNER_WORKFLOW = ".github/workflows/fork-release-source-preflight.yml"
DOWNLOAD_TIMEOUT_SECONDS = 600
GH_TIMEOUT_SECONDS = 120
ERROR_EXCERPT = 4000
MAX_GH_OUTPUT = 5_000_000
REPO_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+/-]{0,255}$")
ASSET_PATTERN = re.compile(r"^[A-Za-z0-9*?][A-Za-z0-9._*?\[\]-]{0,255}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
MAX_ATTESTATION_DEPTH = 20


def validate_repo(repo: str) -> None:
    """Reject a repository value that could be mistaken for an option."""
    if not REPO_PATTERN.fullmatch(repo):
        raise IntegrityError(
            "Repository must be OWNER/NAME with only letters, digits, '.', '_' or '-'"
        )


def validate_tag(tag: str) -> None:
    """Reject a tag value that could be mistaken for an option or shell syntax."""
    if not TAG_PATTERN.fullmatch(tag):
        raise IntegrityError("Release tag has unsupported characters")


def validate_asset(asset: str) -> None:
    """Reject an asset glob that could be mistaken for an option or shell syntax."""
    if not ASSET_PATTERN.fullmatch(asset):
        raise IntegrityError("Asset pattern has unsupported characters")


def validate_release_identity(repo: str, tag: str, asset: str) -> None:
    """Reject values that could be mistaken for options or shell syntax."""
    validate_repo(repo)
    validate_tag(tag)
    validate_asset(asset)


def normalized_sha256(value: str, what: str) -> str:
    digest = value.lower()
    if not SHA256_PATTERN.fullmatch(digest):
        raise IntegrityError(f"{what} must be 64 hexadecimal characters")
    return digest


def normalized_commit(value: str, what: str) -> str:
    commit = value.lower()
    if not COMMIT_PATTERN.fullmatch(commit):
        raise IntegrityError(f"{what} must be a 40- or 64-character hexadecimal commit")
    return commit


def run_gh(command: list[str], *, timeout: int, what: str) -> subprocess.CompletedProcess:
    """Run the GitHub CLI without a shell, with a deadline and bounded output."""
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False,
        )
    except FileNotFoundError as exc:
        raise IntegrityError("The GitHub CLI (gh) is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise IntegrityError(f"{what} timed out") from exc
    if len(completed.stdout) > MAX_GH_OUTPUT or len(completed.stderr) > MAX_GH_OUTPUT:
        raise IntegrityError(f"{what} produced too much output")
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[:ERROR_EXCERPT]
        raise IntegrityError(f"{what} failed: {detail or 'gh failed'}")
    return completed


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


def stage_archive(source: BinaryIO, destination: Path) -> tuple[int, str]:
    """Copy an archive into a private file while hashing the copied bytes."""
    digest = hashlib.sha256()
    count = 0
    try:
        with destination.open("xb") as staged:
            while chunk := source.read(1024 * 1024):
                count += len(chunk)
                if count > MAX_ARCHIVE_BYTES:
                    raise IntegrityError("The release source ZIP exceeds the size limit")
                digest.update(chunk)
                staged.write(chunk)
    except OSError as exc:
        raise IntegrityError(
            f"Cannot stage the release source ZIP: {exc.strerror or exc}"
        ) from exc
    return count, digest.hexdigest()


def download_release(repo: str, tag: str, asset: str, destination: Path) -> Path:
    """Download exactly one matching release asset with the GitHub CLI."""
    validate_release_identity(repo, tag, asset)
    run_gh(
        [
            "gh", "release", "download", tag, "--repo", repo,
            "--pattern", asset, "--dir", str(destination),
        ],
        timeout=DOWNLOAD_TIMEOUT_SECONDS,
        what=f"Downloading {asset} from {repo} {tag}",
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


def resolve_release_commit(repo: str, tag: str) -> str:
    """Ask GitHub which commit a release tag points at."""
    validate_repo(repo)
    validate_tag(tag)
    encoded = urllib.parse.quote(tag, safe="")
    completed = run_gh(
        ["gh", "api", f"repos/{repo}/commits/{encoded}"],
        timeout=GH_TIMEOUT_SECONDS,
        what=f"Resolving {repo} {tag}",
    )
    try:
        data = json.loads(completed.stdout)
    except ValueError as exc:
        raise IntegrityError(f"Resolving {repo} {tag} returned invalid JSON") from exc
    commit = data.get("sha") if isinstance(data, dict) else None
    if not isinstance(commit, str):
        raise IntegrityError(f"Resolving {repo} {tag} returned no commit")
    return normalized_commit(commit, f"The commit for {repo} {tag}")


def load_provenance(path: Path) -> dict:
    """Read the archive hash and source commit a preflight receipt recorded."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IntegrityError(
            f"Cannot read the provenance receipt: {exc.strerror or exc}"
        ) from exc
    if len(raw) > MAX_MANIFEST_SIZE:
        raise IntegrityError("Provenance receipt exceeds the size limit")
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise IntegrityError(f"Invalid provenance receipt: {exc}") from exc
    if not isinstance(data, dict):
        raise IntegrityError("Provenance receipt shape is invalid")
    digest = data.get("archive_sha256")
    if not isinstance(digest, str):
        raise IntegrityError("Provenance receipt has no archive SHA-256")
    commit = data.get("source_commit")
    if commit is None and isinstance(data.get("source"), dict):
        commit = data["source"].get("commit")
    if commit is not None and not isinstance(commit, str):
        raise IntegrityError("Provenance receipt has no valid source commit")
    return {
        "receipt": path.name,
        "archive_sha256": normalized_sha256(digest, "The provenance archive SHA-256"),
        "commit": (normalized_commit(commit, "The provenance source commit")
                   if commit is not None else None),
    }


def attestation_digests(value, depth: int = 0) -> set[str]:
    """Collect every SHA-256 subject digest in gh's verification JSON."""
    if depth > MAX_ATTESTATION_DEPTH:
        return set()
    digests: set[str] = set()
    if isinstance(value, dict):
        subject = value.get("subject")
        if isinstance(subject, list):
            for item in subject:
                if not isinstance(item, dict):
                    continue
                digest = item.get("digest")
                if isinstance(digest, dict):
                    sha = digest.get("sha256")
                    if isinstance(sha, str) and SHA256_PATTERN.fullmatch(sha.lower()):
                        digests.add(sha.lower())
        for key, item in value.items():
            if key != "subject":
                digests |= attestation_digests(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            digests |= attestation_digests(item, depth + 1)
    return digests


def verify_attestation(
    archive: Path, repo: str, *, commit: str | None, expected_sha256: str,
    signer_workflow: str | None,
) -> dict:
    """Require a GitHub attestation that covers the verified archive bytes."""
    validate_repo(repo)
    command = [
        "gh", "attestation", "verify", str(archive),
        "--repo", repo, "--format", "json",
    ]
    if commit is not None:
        command += ["--source-digest", commit]
    if signer_workflow is not None:
        command += ["--signer-workflow", signer_workflow]
    completed = run_gh(
        command, timeout=GH_TIMEOUT_SECONDS,
        what="Verifying the archive attestation",
    )
    try:
        data = json.loads(completed.stdout)
    except ValueError as exc:
        raise IntegrityError("Attestation verification returned invalid JSON") from exc
    digests = attestation_digests(data)
    if not digests:
        raise IntegrityError("Attestation verification returned no subject digest")
    if expected_sha256 not in digests:
        raise IntegrityError("Attestation does not cover the verified archive")
    return {
        "repo": repo, "verified": True, "source_digest": commit,
        "signer_workflow": signer_workflow,
    }


def verify_release_archive(
    archive: Path, *, repo: str | None = None, tag: str | None = None,
    asset: str | None = None, expect_sha256: str | None = None,
    commit: str | None = None, commit_verified: bool = False,
) -> dict:
    """Verify one release ZIP, re-verify its extracted tree and describe it."""
    if archive.is_symlink():
        raise IntegrityError("The release source ZIP must be a regular file")
    with tempfile.TemporaryDirectory(prefix=".release-stage-") as stage:
        staged_path = Path(stage) / "archive.zip"
        with open_regular_archive(archive) as source:
            archive_bytes, archive_digest = stage_archive(source, staged_path)
        if expect_sha256 is not None and archive_digest != expect_sha256:
            raise IntegrityError(
                f"Archive SHA-256 is {archive_digest}, not the expected {expect_sha256}"
            )
        with open_regular_archive(staged_path) as staged:
            staged.seek(0)
            _, staged_digest = sha256_stream(staged, MAX_ARCHIVE_BYTES)
            if staged_digest != archive_digest:
                raise IntegrityError("Staged archive changed after hashing")
            staged.seek(0)
            verified = verify_zip(staged, require_declared_root=True)
            staged.seek(0)
            with tempfile.TemporaryDirectory(prefix=".release-source-") as install:
                extracted = extract_verified_zip(
                    staged, Path(install), require_declared_root=True,
                )
            staged.seek(0)
            _, final_digest = sha256_stream(staged, MAX_ARCHIVE_BYTES)
            if final_digest != archive_digest:
                raise IntegrityError("Staged archive changed during verification")
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
        "root_policy": "strict",
        "root_renamed": verified["root_renamed"],
    }
    if commit is not None:
        receipt["source"] = {"commit": commit, "verified": commit_verified}
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


def fail(message: str) -> int:
    print(json.dumps({
        "schema": SCHEMA_VERSION, "status": "error", "error": message,
    }, indent=2))
    return 1


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
    parser.add_argument("--commit",
                        help="Commit the source ZIP must represent; checked against the tag when downloading")
    parser.add_argument("--expect-archive-sha256",
                        help="Require the archive's SHA-256 to match this value")
    parser.add_argument("--provenance", type=Path,
                        help="Preflight receipt whose archive SHA-256 and commit must match")
    parser.add_argument("--verify-attestation", nargs="?", const="", metavar="OWNER/NAME",
                        help="Verify a GitHub artifact attestation (default: the download repository)")
    parser.add_argument("--signer-workflow",
                        help=f"Workflow identity that must have signed the attestation "
                             f"(default: OWNER/NAME/{DEFAULT_SIGNER_WORKFLOW})")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    destination = args.report.resolve() if args.report else None
    if destination is not None and destination.is_relative_to(root):
        return fail("Place the receipt outside the source tree")
    if destination is not None and destination.exists():
        return fail("Refusing to overwrite an existing receipt")
    try:
        if args.commit is not None:
            args.commit = normalized_commit(args.commit, "The source commit")
        expected_sha256 = (normalized_sha256(args.expect_archive_sha256,
                                             "The expected archive SHA-256")
                           if args.expect_archive_sha256 is not None else None)
        provenance = load_provenance(args.provenance) if args.provenance else None
    except IntegrityError as exc:
        return fail(str(exc))
    if provenance is not None:
        if expected_sha256 is not None and expected_sha256 != provenance["archive_sha256"]:
            return fail("The expected archive SHA-256 and the provenance receipt disagree")
        expected_sha256 = expected_sha256 or provenance["archive_sha256"]
        if provenance["commit"] is not None:
            if args.commit is not None and args.commit != provenance["commit"]:
                return fail("The source commit and the provenance receipt disagree")
            args.commit = args.commit or provenance["commit"]
    attestation_repo = args.verify_attestation
    if attestation_repo is not None:
        if attestation_repo == "":
            if args.repo is None:
                return fail("--verify-attestation needs OWNER/NAME when checking a local archive")
            attestation_repo = args.repo
        try:
            validate_repo(attestation_repo)
        except IntegrityError as exc:
            return fail(str(exc))
    if args.signer_workflow is not None and attestation_repo is None:
        return fail("--signer-workflow requires --verify-attestation")
    signer_workflow = args.signer_workflow
    if attestation_repo is not None and signer_workflow is None:
        signer_workflow = f"{attestation_repo}/{DEFAULT_SIGNER_WORKFLOW}"
    commit = args.commit
    commit_verified = False
    try:
        if args.archive is not None:
            receipt = verify_release_archive(
                args.archive, expect_sha256=expected_sha256,
                commit=commit, commit_verified=False,
            )
            if attestation_repo is not None:
                receipt["attestation"] = verify_attestation(
                    args.archive, attestation_repo, commit=commit,
                    expected_sha256=receipt["archive_sha256"],
                    signer_workflow=signer_workflow,
                )
        else:
            if not args.tag:
                raise IntegrityError("--tag is required with --repo")
            if commit is not None or attestation_repo is not None:
                resolved = resolve_release_commit(args.repo, args.tag)
                if commit is not None and resolved != commit:
                    raise IntegrityError(
                        f"Release tag resolves to {resolved}, not the expected commit {commit}"
                    )
                commit = resolved
                commit_verified = True
            with tempfile.TemporaryDirectory(prefix=".release-download-") as download:
                archive = download_release(
                    args.repo, args.tag, args.asset, Path(download),
                )
                receipt = verify_release_archive(
                    archive, repo=args.repo, tag=args.tag, asset=args.asset,
                    expect_sha256=expected_sha256, commit=commit,
                    commit_verified=commit_verified,
                )
                if attestation_repo is not None:
                    receipt["attestation"] = verify_attestation(
                        archive, attestation_repo, commit=commit,
                        expected_sha256=receipt["archive_sha256"],
                        signer_workflow=signer_workflow,
                    )
    except (IntegrityError, OSError, ValueError) as exc:
        receipt = {"schema": SCHEMA_VERSION, "status": "error", "error": str(exc)}
    if provenance is not None and receipt["status"] == "verified":
        receipt["provenance"] = provenance
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
