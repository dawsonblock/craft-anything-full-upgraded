"""Test sequential MCP target lifecycle and transport without Rust installed."""

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

import native_mcp_target_smoke as target


def parsed_record() -> dict:
    keys = (
        "sections", "types", "imports", "exports", "functions", "memories",
        "tables", "globals", "data_segments", "element_segments", "custom_sections",
    )
    return {
        "id": "ev_" + "a" * 64,
        "operation": "formats_wasm_module_show",
        "subject": {"kind": "wasm", "sha256": target.WASM_SHA256, "name": target.FIXTURE_NAME},
        "environment": {"engine": "builtin", "clean_room": False},
        "result": {
            "encoding": "module", "version": 1, "size": len(target.WASM_FIXTURE),
            "validation": {"valid": True, "error": None, "offset": None},
            "parse_error": None, "counts": dict.fromkeys(keys, 0),
        },
    }


class SimulatedDialog:
    """Stand-in engine that records the order and changes its session state."""

    def __init__(self, *, bad_parse=False, refuse_close=False, extra_target=False):
        self.calls: list[str] = []
        self.open = False
        self.evidence_count = 0
        self.bad_parse = bad_parse
        self.refuse_close = refuse_close
        self.extra_target = extra_target
        self.target_id = "t_" + target.WASM_SHA256[:12]

    def notify(self, method: str) -> None:
        self.calls.append(method)

    def ask(self, method: str, params: dict) -> dict:
        if method == "initialize":
            self.calls.append(method)
            return {"protocolVersion": target.PROTOCOL_VERSION,
                    "serverInfo": {"name": target.SERVER_NAME, "version": "0.4.0"}}
        assert method == "tools/call"
        name = params["name"]
        self.calls.append(name)
        if name == "core_target_open":
            assert Path(params["arguments"]["path"]).name == target.FIXTURE_NAME
            self.open = True
            value = {"result": {
                "target_id": self.target_id, "kind": "wasm", "bytes": 8,
                "name": target.FIXTURE_NAME, "sha256": target.WASM_SHA256,
            }}
        elif name == "formats_wasm_module_show":
            assert self.open
            assert params["arguments"] == {"detail": "summary"}
            self.evidence_count += 1
            value = parsed_record()
            if self.bad_parse:
                value["result"]["validation"]["valid"] = False
        elif name == "core_target_close":
            assert params["arguments"]["target_id"] == self.target_id
            if not self.refuse_close:
                self.open = False
            value = {"result": {"closed": self.target_id, "evidence_records": self.evidence_count}}
        else:
            assert name == "core_session_show"
            targets = ([{"sha256": target.WASM_SHA256}] if self.open else [])
            if self.extra_target and not self.open:
                targets.append({"sha256": "0" * 64})
            value = {"result": {
                "open": self.open, "targets": targets,
                "active_target": self.target_id if self.open else None,
                "analysis_mode": "deep", "clean_room": False,
                "project": None, "evidence_records": self.evidence_count,
                "unknowns": 0,
            }}
        return {"isError": False, "structuredContent": value,
                "content": [{"type": "text", "text": json.dumps(value)}]}


class McpTargetSmokeTests(unittest.TestCase):
    def test_stateful_parser_then_close_in_a_single_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / target.FIXTURE_NAME
            fixture.write_bytes(target.WASM_FIXTURE)
            dialog = SimulatedDialog()
            result = target.verify_sequence(dialog, fixture, "0.4.0")
        self.assertTrue(result["mcp_target_wasm_parsed"])
        self.assertTrue(result["mcp_target_state_closed"])
        self.assertTrue(result["mcp_target_evidence_preserved"])
        self.assertEqual(dialog.calls, ["initialize", "notifications/initialized", *target.OPERATIONS])
        self.assertFalse(dialog.open)
        self.assertEqual(dialog.evidence_count, 1)

    def test_refuse_parser_validation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / target.FIXTURE_NAME
            with self.assertRaisesRegex(ValueError, "validation_failed"):
                target.verify_sequence(SimulatedDialog(bad_parse=True), fixture, "0.4.0")

    def test_refuse_false_close_and_unexpected_initial_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / target.FIXTURE_NAME
            with self.assertRaisesRegex(ValueError, "cleanup_not_verified"):
                target.verify_sequence(SimulatedDialog(refuse_close=True), fixture, "0.4.0")
            with self.assertRaisesRegex(ValueError, "initial_session_not_empty"):
                target.verify_sequence(SimulatedDialog(extra_target=True), fixture, "0.4.0")

    def test_bad_parser_evidence_fields_are_rejected(self) -> None:
        mutations = (
            ("operation", "core_target_triage"),
            ("id", "missing"),
            ("subject.sha256", "x" * 64),
            ("subject.kind", "unknown"),
            ("environment.clean_room", True),
            ("result.encoding", "component"),
            ("result.version", 2),
            ("result.size", False),
            ("result.validation.valid", False),
            ("result.parse_error", "truncated"),
            ("result.counts.imports", 1),
        )
        for path, value in mutations:
            record = copy.deepcopy(parsed_record())
            parts = path.split(".")
            parent = record
            for part in parts[:-1]:
                parent = parent[part]
            parent[parts[-1]] = value
            with self.subTest(mutation=path), self.assertRaises(target.TargetSmokeError):
                target.verify_parse(record)

    def test_mcp_text_and_structured_content_must_match(self) -> None:
        class Mismatch:
            def ask(self, method, params):
                return {"isError": False, "structuredContent": {"result": {"a": 1}},
                        "content": [{"type": "text", "text": '{"result":{"a":2}}'}]}
        with self.assertRaisesRegex(ValueError, "text_structured_mismatch"):
            target.tool(Mismatch(), "core_session_show", {})

    def test_real_jsonl_transport_preserves_request_order(self) -> None:
        server = (
            "import json,sys\n"
            "for line in sys.stdin:\n"
            " q=json.loads(line)\n"
            " if 'id' not in q: continue\n"
            " print(json.dumps({'jsonrpc':'2.0','id':q['id'],'result':{'method':q['method']}}),flush=True)\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            isolated = Path(temp)
            env = target.private_cli_environment(isolated)
            dialog = target.McpDialog([sys.executable, "-u", "-c", server], ROOT, env, 8)
            try:
                self.assertEqual(dialog.ask("initialize", {})["method"], "initialize")
                dialog.notify("notifications/initialized")
                self.assertEqual(dialog.ask("tools/call", {"name": "core_session_show"})["method"], "tools/call")
                dialog.finish()
            finally:
                dialog.abort()

    def test_transport_rejects_wrong_id(self) -> None:
        server = (
            "import json,sys\n"
            "for line in sys.stdin:\n"
            " q=json.loads(line)\n"
            " if 'id' in q: print(json.dumps({'jsonrpc':'2.0','id':999,'result':{}}),flush=True)\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            env = target.private_cli_environment(Path(temp))
            dialog = target.McpDialog([sys.executable, "-u", "-c", server], ROOT, env, 5)
            try:
                with self.assertRaisesRegex(ValueError, "out_of_order"):
                    dialog.ask("initialize", {})
            finally:
                dialog.abort()

    def test_missing_native_binary_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(target, "cli_binary_path", return_value=Path(temp) / "missing"):
            result = target.smoke_mcp_target(ROOT, 5, {"cli_version": "0.4.0"})
        self.assertEqual(result["status"], "fail")
        self.assertNotIn("mcp_target_state_closed", result)

    def test_mocked_native_dialog_stores_no_paths_or_raw_evidence(self) -> None:
        binary = Path(sys.executable).resolve()
        recorded: list[str] = []
        holder = {}

        class FakeNative(SimulatedDialog):
            def __init__(self, command, root, env, timeout):
                self.command = command
                self.env = env
                self._closed = False
                super().__init__()
                holder["instance"] = self

            def ask(self, method, params):
                if method == "tools/call" and params["name"] == "core_target_open":
                    fixture = Path(params["arguments"]["path"])
                    self.assertions = (fixture.read_bytes() == target.WASM_FIXTURE)
                    recorded.append(str(fixture))
                return super().ask(method, params)

            def finish(self):
                self._closed = True

            def abort(self):
                self._closed = True

        with patch.object(target, "cli_binary_path", return_value=binary), \
                patch.object(target, "McpDialog", FakeNative), \
                patch.dict(os.environ, {"CANARY_SECRET": "do-not-forward"}):
            result = target.smoke_mcp_target(ROOT, 10, {"cli_version": "0.4.0"})
        self.assertEqual(result["status"], "pass")
        self.assertTrue(holder["instance"].assertions)
        self.assertNotIn("CANARY_SECRET", holder["instance"].env)
        self.assertFalse(Path(recorded[0]).exists())
        self.assertNotIn(recorded[0], json.dumps(result))
        self.assertNotIn("structuredContent", json.dumps(result))
        self.assertTrue(result["mcp_target_state_closed"])


if __name__ == "__main__":
    unittest.main()
