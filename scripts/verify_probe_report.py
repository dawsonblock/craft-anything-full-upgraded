#!/usr/bin/env python3
"""Recompute a saved replay report's checks and PNG digests without rerunning the game.

This proves internal consistency of saved evidence, not that a target's
telemetry was truthful. Reports are not signed against hostile modification.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from independent_game_probe import (
    MAX_CASE_BYTES, MAX_RESPONSE_BYTES, ProbeError, case_digest, validate,
)


def verify(case: dict, report: dict) -> dict:
    if not isinstance(case, dict) or not isinstance(report, dict):
        raise ProbeError("Case and report must be JSON objects")
    if report.get("status") != "pass":
        raise ProbeError("Only passing probe reports can be independently rechecked")
    if report.get("case_sha256") != case_digest(case):
        raise ProbeError("Replay case changed since the report was created")
    capture_label = report.get("capture_folder")
    nonce = report.get("challenge_nonce")
    response = report.get("response")
    if not isinstance(capture_label, str) or not capture_label or not Path(capture_label).is_absolute():
        raise ProbeError("The report needs an absolute capture_folder")
    if not isinstance(nonce, str) or len(nonce) != 32:
        raise ProbeError("The report needs its original challenge nonce")
    if not isinstance(response, dict):
        raise ProbeError("The report is missing recorded telemetry")
    capture = Path(capture_label)
    if not capture.is_dir() or capture.is_symlink():
        raise ProbeError("The saved capture folder is missing or is a symbolic link")
    rebuilt = validate(case, response, capture, expected_nonce=nonce)
    if rebuilt["status"] != "pass":
        raise ProbeError("The saved telemetry or screenshots no longer satisfy the case")
    for key in ("checks", "metrics", "screenshot_sha256"):
        if rebuilt[key] != report.get(key):
            raise ProbeError(f"The report's {key} no longer matches its saved evidence")
    return {
        "status": "verified",
        "case_sha256": report["case_sha256"],
        "screenshot_sha256": rebuilt["screenshot_sha256"],
        "limitations": "Revalidated stored telemetry and PNG bytes; the evidence is not signed and the program may have fabricated its observations.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.case.stat().st_size > MAX_CASE_BYTES:
            raise ProbeError("Replay case is too large")
        if args.report.stat().st_size > MAX_RESPONSE_BYTES * 2:
            raise ProbeError("Replay report is too large")
        case = json.loads(args.case.read_text(encoding="utf-8"))
        report = json.loads(args.report.read_text(encoding="utf-8"))
        result = verify(case, report)
    except (ProbeError, OSError, ValueError, TypeError, KeyError) as exc:
        result = {"status": "error", "error": str(exc)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
