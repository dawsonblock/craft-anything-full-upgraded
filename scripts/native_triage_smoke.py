#!/usr/bin/env python3
"""Isolated native CLI end-to-end triage of a tiny deterministic WASM module.

This is an actual target-opening/analysis/closing smoke, unlike registry or
protocol-only checks. It never reads a user target, forwards credentials,
accepts arbitrary input paths or saves raw target evidence to CI receipts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tempfile
import time

from native_validation import cli_binary_path, execute, hash_binary
from registry_smoke import private_cli_environment


TRIAGE_TIMEOUT = 45
# A valid, empty WebAssembly 1.0 binary: magic followed by version 1.
WASM_FIXTURE = b"\x00asm\x01\x00\x00\x00"
WASM_SHA256 = hashlib.sha256(WASM_FIXTURE).hexdigest()
EXPECTED_TOOL = "formats_wasm_module_show"
EXPECTED_TOOLSETS = {"core", "formats"}


def validate_triage(raw: str) -> dict:
    """Reject plausible-looking text: require the specific evidence contract."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("triage_output_is_not_json") from exc
    if not isinstance(data, dict):
        raise ValueError("triage_output_not_object")
    evidence_id = data.get("id")
    if not isinstance(evidence_id, str) or re.fullmatch(r"ev_[0-9a-f]{64}", evidence_id) is None:
        raise ValueError("triage_evidence_id_invalid")
    if data.get("operation") != "core_target_triage":
        raise ValueError("triage_operation_mismatch")
    subject = data.get("subject")
    if not isinstance(subject, dict) or subject.get("kind") != "wasm":
        raise ValueError("triage_subject_kind_mismatch")
    if subject.get("sha256") != WASM_SHA256 or subject.get("name") != "craft_native_smoke.wasm":
        raise ValueError("triage_target_not_our_fixture")
    environment = data.get("environment")
    if not isinstance(environment, dict) or environment.get("clean_room") is not False:
        raise ValueError("triage_not_in_deep_mode")
    if environment.get("engine") != "builtin":
        raise ValueError("triage_unexpected_engine")
    result = data.get("result")
    if not isinstance(result, dict) or result.get("kind") != "wasm" or result.get("size") != len(WASM_FIXTURE):
        raise ValueError("triage_result_kind_or_size_invalid")
    plain = result.get("plain_english")
    if not isinstance(plain, str) or "WebAssembly" not in plain:
        raise ValueError("triage_plain_english_missing")
    toolsets = result.get("recommended_toolsets")
    if (not isinstance(toolsets, list) or len(toolsets) != 2 or
            any(not isinstance(item, str) for item in toolsets) or
            set(toolsets) != EXPECTED_TOOLSETS):
        raise ValueError("triage_toolsets_invalid")
    steps = result.get("next_steps")
    if not isinstance(steps, list) or not any(
        isinstance(step, dict) and step.get("tool") == EXPECTED_TOOL and
        isinstance(step.get("why"), str) and step["why"].strip()
        for step in steps
    ):
        raise ValueError("triage_missing_wasm_followup")
    return {
        "triage_evidence_verified": True,
        "triage_kind": "wasm",
        "triage_fixture_sha256": WASM_SHA256,
        "triage_tool": EXPECTED_TOOL,
    }


def smoke_triage(root: Path, timeout: int) -> dict:
    """Run the compiled CLI on a disposable 8-byte fixture, fail closed."""
    started = time.monotonic()
    redacted = ["<built-cli>", "--deep", "triage", "<synthetic-wasm>"]
    try:
        binary = cli_binary_path(root)
        before = hash_binary(binary)
    except (OSError, ValueError) as exc:
        return {"name": "triage_smoke", "status": "fail", "command": redacted, "reason": str(exc)}

    try:
        with tempfile.TemporaryDirectory(prefix="craft_native_triage_") as temporary:
            isolated = Path(temporary)
            fixture = isolated / "craft_native_smoke.wasm"
            fixture.write_bytes(WASM_FIXTURE)
            env = private_cli_environment(isolated)
            result = execute(
                "triage_smoke", [str(binary), "--deep", "triage", str(fixture)],
                root, min(timeout, TRIAGE_TIMEOUT), env=env, capture_full_output=True,
            )
            raw = result.pop("full_output", "")
            # Neither tool output nor ephemeral/host-specific paths go into receipts.
            result.pop("output_excerpt", None)
            result["command"] = redacted
            if result["status"] == "pass":
                try:
                    result.update(validate_triage(raw))
                except ValueError as exc:
                    result["status"] = "fail"
                    result["reason"] = str(exc)
    except (OSError, ValueError) as exc:
        result = {"name": "triage_smoke", "status": "fail", "command": redacted, "reason": str(exc)}

    try:
        after = hash_binary(binary)
        if before != after:
            result["status"] = "fail"
            result["reason"] = "triage_modified_or_replaced_cli_binary"
    except (OSError, ValueError) as exc:
        result["status"] = "fail"
        result["reason"] = f"triage_binary_unavailable_after_smoke: {exc}"
    # Preserve the binary identity but never leak raw evidence or local paths.
    result.update(before)
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    return result
