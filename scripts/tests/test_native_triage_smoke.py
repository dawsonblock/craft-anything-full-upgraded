"""Synthetic tests of the actual-target native smoke harness (no Rust needed)."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import native_triage_smoke as triage


def valid_evidence() -> dict:
    return {
        "id": "ev_" + "a" * 64,
        "operation": "core_target_triage",
        "subject": {
            "kind": "wasm", "sha256": triage.WASM_SHA256,
            "name": "craft_native_smoke.wasm",
        },
        "environment": {"clean_room": False, "engine": "builtin"},
        "result": {
            "kind": "wasm", "size": 8,
            "plain_english": "craft_native_smoke.wasm is a WebAssembly module.",
            "recommended_toolsets": ["core", "formats"],
            "next_steps": [{"tool": "formats_wasm_module_show", "why": "exports and imports"}],
        },
    }


class NativeTriageSmokeTests(unittest.TestCase):
    def test_fixture_is_a_real_webassembly_header(self) -> None:
        self.assertEqual(triage.WASM_FIXTURE, bytes.fromhex("0061736d01000000"))
        self.assertEqual(len(triage.WASM_SHA256), 64)

    def test_valid_evidence_reduces_to_non_sensitive_summary(self) -> None:
        outcome = triage.validate_triage(json.dumps(valid_evidence()))
        self.assertEqual(outcome, {
            "triage_evidence_verified": True, "triage_kind": "wasm",
            "triage_fixture_sha256": triage.WASM_SHA256,
            "triage_tool": triage.EXPECTED_TOOL,
        })

    def test_wrong_root_and_malformed_json_are_rejected(self) -> None:
        for content in ("", "not JSON", "[]", "null", "{\"error\":\"denied\"}"):
            with self.subTest(content=content), self.assertRaises(ValueError):
                triage.validate_triage(content)

    def test_mutations_cannot_pass_evidence_validation(self) -> None:
        alterations = (
            ("id", "ev_BAD"),
            ("operation", "core_session_show"),
            ("subject.kind", "pe"),
            ("subject.sha256", "0" * 64),
            ("subject.name", "not_our_fixture.exe"),
            ("environment.clean_room", True),
            ("environment.engine", "ghidra"),
            ("result.kind", "unknown"),
            ("result.size", 9),
            ("result.plain_english", "something else"),
            ("result.recommended_toolsets", ["core", "android"]),
            ("result.recommended_toolsets", [{"unhashable": True}]),
            ("result.next_steps", []),
            ("result.next_steps", [{"tool": "formats_wasm_module_show"}]),
        )
        for dotted, value in alterations:
            evidence = copy.deepcopy(valid_evidence())
            part = evidence
            for key in dotted.split(".")[:-1]:
                part = part[key]
            part[dotted.split(".")[-1]] = value
            with self.subTest(mutation=dotted, value=value), self.assertRaises(ValueError):
                triage.validate_triage(json.dumps(evidence))

    def test_real_subprocess_contract_isolated_and_redacted(self) -> None:
        binary = Path(sys.executable).resolve()
        seen = []

        def fake_execute(name, command, root, timeout, *, env, capture_full_output):
            self.assertEqual(name, "triage_smoke")
            self.assertEqual(command[:3], [str(binary), "--deep", "triage"])
            self.assertEqual(Path(command[-1]).read_bytes(), triage.WASM_FIXTURE)
            self.assertTrue(capture_full_output)
            self.assertTrue(timeout <= triage.TRIAGE_TIMEOUT)
            self.assertEqual(env["HOME"], str(Path(command[-1]).parent))
            self.assertNotIn("CANARY_SECRET", env)
            seen.append(command[-1])
            return {"name": name, "status": "pass", "command": command,
                    "output_excerpt": "raw target output", "full_output": json.dumps(valid_evidence())}

        with patch.object(triage, "cli_binary_path", return_value=binary), \
                patch.object(triage, "execute", side_effect=fake_execute), \
                patch.dict(os.environ, {"CANARY_SECRET": "must-not-forward"}):
            receipt = triage.smoke_triage(ROOT, 30)
        self.assertEqual(receipt["status"], "pass")
        self.assertTrue(receipt["triage_evidence_verified"])
        self.assertEqual(receipt["command"], ["<built-cli>", "--deep", "triage", "<synthetic-wasm>"])
        self.assertNotIn("full_output", receipt)
        self.assertNotIn("output_excerpt", receipt)
        self.assertNotIn("raw target output", json.dumps(receipt))
        self.assertNotIn(seen[0], json.dumps(receipt))
        self.assertFalse(Path(seen[0]).exists())

    def test_native_failure_or_malformed_output_never_passes(self) -> None:
        binary = Path(sys.executable).resolve()
        with patch.object(triage, "cli_binary_path", return_value=binary):
            for payload in (
                {"name": "triage_smoke", "status": "fail", "reason": "nonzero_exit", "full_output": "denied"},
                {"name": "triage_smoke", "status": "pass", "full_output": "invalid JSON"},
            ):
                with self.subTest(payload=payload), patch.object(triage, "execute", return_value=payload):
                    outcome = triage.smoke_triage(ROOT, 5)
                self.assertEqual(outcome["status"], "fail")
                self.assertNotIn("full_output", outcome)

    def test_missing_native_binary_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(triage, "cli_binary_path", return_value=Path(temp) / "missing"):
            outcome = triage.smoke_triage(ROOT, 5)
        self.assertEqual(outcome["status"], "fail")
        self.assertNotIn("triage_evidence_verified", outcome)


if __name__ == "__main__":
    unittest.main()
