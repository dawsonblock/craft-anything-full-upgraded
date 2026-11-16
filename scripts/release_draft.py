#!/usr/bin/env python3
"""Name the tag a release should carry and pick that release's draft.

A draft GitHub Release records its tag name before the Git tag exists, and more
than one draft can be open at once, so "the newest draft" is not necessarily the
one this run created. This names the expected tag (`v` plus the workspace
version, or the version a dispatch asked for) and selects the one draft that
carries it, refusing none or several. It reads JSON only and never calls GitHub.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tomllib

TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+/-]{0,255}$")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
MAX_RELEASES = 5000


class DraftError(ValueError):
    """A release identity that cannot be used."""


def normalize_version(value: str) -> str:
    """Accept `1.2.3` or `v1.2.3`, with an optional pre-release or build suffix."""
    version = value.strip()
    if version[:1] in ("v", "V"):
        version = version[1:]
    if not VERSION_PATTERN.fullmatch(version):
        raise DraftError(f"Not a release version: {value!r}")
    return version


def workspace_version(root: Path) -> str:
    """The single source of the app version: `[workspace.package]` in Cargo.toml."""
    try:
        with (root / "Cargo.toml").open("rb") as stream:
            data = tomllib.load(stream)
    except OSError as exc:
        raise DraftError(f"Cannot read the workspace manifest: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise DraftError(f"The workspace manifest is not valid TOML: {exc}") from exc
    package = data.get("workspace", {}).get("package", {})
    version = package.get("version") if isinstance(package, dict) else None
    if not isinstance(version, str):
        raise DraftError("The workspace manifest has no [workspace.package] version")
    return normalize_version(version)


def expected_tag(root: Path, version: str | None = None) -> str:
    """`v` plus the version a dispatch asked for, else the workspace version."""
    chosen = normalize_version(version) if version else workspace_version(root)
    tag = f"v{chosen}"
    if not TAG_PATTERN.fullmatch(tag):
        raise DraftError(f"Not a usable release tag: {tag!r}")
    return tag


def releases_from(payload: object) -> list[dict]:
    """Flatten `gh api` output: one array of releases, or an array of pages (`--slurp`)."""
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise DraftError("The release list is not a JSON array")
    result: list[dict] = []
    for item in payload:
        if isinstance(item, list):
            result.extend(entry for entry in item if isinstance(entry, dict))
        elif isinstance(item, dict):
            result.append(item)
        else:
            raise DraftError("The release list holds a value that is not an object")
        if len(result) > MAX_RELEASES:
            raise DraftError("The release list is longer than expected")
    return result


def select_draft(payload: object, tag: str, commit: str | None = None) -> dict:
    """The one draft carrying `tag`, checked against the release commit when it names one."""
    if not TAG_PATTERN.fullmatch(tag):
        raise DraftError(f"Not a usable release tag: {tag!r}")
    if commit is not None:
        commit = commit.strip().lower()
        if not COMMIT_PATTERN.fullmatch(commit):
            raise DraftError("The source commit must be 40 or 64 hexadecimal characters")
    matches = [
        release for release in releases_from(payload)
        if release.get("draft") is True and release.get("tag_name") == tag
    ]
    if not matches:
        raise DraftError(f"No draft release carries the tag {tag}")
    if len(matches) > 1:
        raise DraftError(f"{len(matches)} draft releases carry the tag {tag}; keep one")
    release = matches[0]
    identifier = release.get("id")
    if type(identifier) is not int or identifier <= 0:
        raise DraftError("The draft release has no usable id")
    target = release.get("target_commitish")
    if commit is not None and isinstance(target, str):
        candidate = target.strip().lower()
        if COMMIT_PATTERN.fullmatch(candidate) and candidate != commit:
            raise DraftError(f"The draft targets {candidate}, not the release commit {commit}")
    return {"id": identifier, "tag_name": tag, "target_commitish": target}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    name = sub.add_parser("tag", help="print the tag a release should carry")
    name.add_argument("--root", type=Path, required=True)
    name.add_argument("--version", default="", help="a dispatch's version override")
    pick = sub.add_parser("select", help="print the one draft release with that tag")
    pick.add_argument("--releases", type=Path, required=True)
    pick.add_argument("--tag", required=True)
    pick.add_argument("--commit", default="")
    args = parser.parse_args(argv)
    try:
        if args.command == "tag":
            print(expected_tag(args.root, args.version or None))
        else:
            try:
                payload = json.loads(args.releases.read_text(encoding="utf-8"))
            except OSError as exc:
                raise DraftError(f"Cannot read the release list: {exc}") from exc
            except ValueError as exc:
                raise DraftError(f"The release list is not valid JSON: {exc}") from exc
            chosen = select_draft(payload, args.tag, args.commit or None)
            print(json.dumps(chosen, sort_keys=True))
    except DraftError as exc:
        print(json.dumps({"error": str(exc), "status": "error"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
