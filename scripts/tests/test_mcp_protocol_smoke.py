"""MCP native smoke tests: adversarial JSON-RPC and bounded subprocess fixtures."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import mcp_protocol_smoke as mcp
from registry_smoke import validate_registry


def commands() -> list[dict]:
    return [{"command": f"name-{index}",
             "tool": mcp.READ_ONLY_TOOL if index == 0 else f"tool_{index}",
             "toolset": f"set_{index % 5}", "title": f"Tool {index}",
             "summary": f"Summary {index}", "needs_target": False}
            for index in range(200)]


def evidence() -> dict:
    return {"status": "pass", "cli_version": "0.4.0", **validate_registry(json.dumps(commands()))}


def transcript() -> list[dict]:
    records = [{"name": command["tool"], "title": command["title"],
                "description": command["summary"], "inputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": True, "destructiveHint": False,
                                "idempotentHint": True, "openWorldHint": False}}
               for command in commands()]
    records[0]["inputSchema"] = dict(mcp.EMPTY_INPUT_SCHEMA)
    records[0]["annotations"]["title"] = records[0]["title"]
    session_body = {"result": {
        "open": False, "active_target": None, "targets": [], "project": None,
        "analysis_mode": "deep", "clean_room": False,
        "evidence_records": 0, "evidence_not_loaded": 0, "unknowns": 0,
        "server": {"name": mcp.SERVER_NAME, "version": "0.4.0"},
        "host": "synthetic-test-host",
    }}
    return [
        {"jsonrpc": "2.0", "id": 1, "result": {
            "protocolVersion": mcp.PROTOCOL_VERSION,
            "serverInfo": {"name": mcp.SERVER_NAME, "version": "0.4.0"},
            "capabilities": {"tools": {"listChanged": True}},
        }},
        {"jsonrpc": "2.0", "id": 2, "result": {"tools": records}},
        {"jsonrpc": "2.0", "id": 3, "result": {}},
        {"jsonrpc": "2.0", "id": 4, "error": {"code": -32601, "message": "Method not found"}},
        {"jsonrpc": "2.0", "id": 5, "result": {
            "isError": False, "structuredContent": session_body,
            "content": [{"type": "text", "text": json.dumps(session_body)}],
        }},
        {"jsonrpc": "2.0", "id": 6, "result": {
            "isError": True,
            "content": [{"type": "text", "text": json.dumps({"error": {
                "code": "bad_input", "message": f"No tool named {mcp.UNKNOWN_TOOL}",
                "remediation": "List the available tools first.",
            }})}],
        }},
    ]


def encoded(records: list[dict]) -> bytes:
    return ("\n".join(json.dumps(record) for record in records) + "\n").encode("utf-8")


class McpProtocolTests(unittest.TestCase):
    def test_input_calls_only_readonly_and_nonexistent_tools(self) -> None:
        input_records = [json.loads(line) for line in mcp.protocol_input().splitlines()]
        self.assertEqual([entry["method"] for entry in input_records], [
            "initialize", "notifications/initialized", "tools/list", "ping",
            "__craft_smoke_unknown_method__", "tools/call", "tools/call",
        ])
        calls = [entry for entry in input_records if entry["method"] == "tools/call"]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["params"], {"name": "core_session_show", "arguments": {}})
        self.assertEqual(calls[1]["params"], {"name": mcp.UNKNOWN_TOOL, "arguments": {}})

    def test_valid_transcript_is_matched_to_cli_registry(self) -> None:
        report = mcp.validate_transcript(encoded(transcript()), evidence())
        self.assertEqual(report["mcp_tool_count"], 200)
        self.assertEqual(report["mcp_contract_sha256"], evidence()["registry_mcp_contract_sha256"])
        self.assertEqual(report["mcp_protocol_version"], mcp.PROTOCOL_VERSION)
        self.assertEqual(report["mcp_server_version"], "0.4.0")
        self.assertEqual(report["mcp_readonly_tool"], "core_session_show")
        self.assertTrue(report["mcp_readonly_session_empty"])
        self.assertTrue(report["mcp_readonly_descriptor_verified"])
        self.assertTrue(report["mcp_unknown_tool_refused"])

    def test_response_order_and_tool_order_are_not_significant(self) -> None:
        reordered = list(reversed(transcript()))
        next(item for item in reordered if item["id"] == 2)["result"]["tools"].reverse()
        self.assertEqual(mcp.validate_transcript(encoded(reordered), evidence())["mcp_tool_count"], 200)

    def test_rejects_protocol_version_server_and_handshake_mismatch(self) -> None:
        for mutation, reason in (
            ("protocol", "protocol_negotiation_mismatch"),
            ("server", "server_identity_mismatch"),
            ("version", "server_identity_mismatch"),
            ("capabilities", "handshake_capabilities_missing"),
        ):
            rows = transcript()
            hello = rows[0]["result"]
            if mutation == "protocol":
                hello["protocolVersion"] = "unknown"
            elif mutation == "server":
                hello["serverInfo"]["name"] = "imposter"
            elif mutation == "version":
                hello["serverInfo"]["version"] = "1.0.0"
            else:
                hello["capabilities"] = {}
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, reason):
                mcp.validate_transcript(encoded(rows), evidence())

    def test_rejects_bad_response_envelopes_and_ids(self) -> None:
        for mutation, reason in (
            ("not_json", "output_not_json_rpc"),
            ("missing", "response_count_invalid"),
            ("duplicate", "duplicate_response_id"),
            ("unknown", "response_id_invalid"),
            ("bool", "response_id_invalid"),
            ("jsonrpc", "response_envelope_invalid"),
            ("both_result_error", "result_error_envelope_invalid"),
            ("neither_result_error", "result_error_envelope_invalid"),
        ):
            rows = transcript()
            if mutation == "not_json":
                raw = b"secret output is not JSON\n"
            else:
                if mutation == "missing":
                    rows.pop()
                elif mutation == "duplicate":
                    rows[3]["id"] = 1
                elif mutation == "unknown":
                    rows[3]["id"] = 100
                elif mutation == "bool":
                    rows[3]["id"] = True
                elif mutation == "both_result_error":
                    rows[2]["error"] = {"code": -32603}
                elif mutation == "neither_result_error":
                    rows[2].pop("result")
                else:
                    rows[0]["jsonrpc"] = "1.0"
                raw = encoded(rows)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, reason):
                mcp.validate_transcript(raw, evidence())

    def test_rejects_broken_ping_and_unknown_method_response(self) -> None:
        for mutation, reason in (("ping", "ping_reply_invalid"),
                                 ("negative", "unknown_method_not_rejected"),
                                 ("error", "request_2_failed")):
            rows = transcript()
            if mutation == "ping":
                rows[2]["result"] = {"status": "wrong"}
            elif mutation == "negative":
                rows[3]["error"]["code"] = -32000
            else:
                rows[1] = {"jsonrpc": "2.0", "id": 2, "error": {"code": -32603}}
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, reason):
                mcp.validate_transcript(encoded(rows), evidence())

    def test_readonly_call_rejects_errors_malformed_text_and_mismatched_data(self) -> None:
        for mutation, reason in (
            ("is_error", "readonly_tool_failed"),
            ("no_content", "readonly_text_missing"),
            ("text_invalid", "readonly_text_not_json"),
            ("text_mismatch", "readonly_text_structured_mismatch"),
            ("no_session", "readonly_session_invalid"),
        ):
            rows = transcript()
            status = rows[4]["result"]
            if mutation == "is_error":
                status["isError"] = True
            elif mutation == "no_content":
                status["content"] = []
            elif mutation == "text_invalid":
                status["content"][0]["text"] = "not json"
            elif mutation == "text_mismatch":
                status["content"][0]["text"] = json.dumps({"different": "body"})
            else:
                status["structuredContent"].pop("result")
                status["content"][0]["text"] = json.dumps(status["structuredContent"])
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, reason):
                mcp.validate_transcript(encoded(rows), evidence())

    def test_readonly_call_rejects_dirty_state_or_wrong_mode(self) -> None:
        for mutation, reason in (
            ("open", "readonly_session_not_empty"),
            ("targets", "readonly_session_not_empty"),
            ("project", "readonly_session_not_empty"),
            ("mode", "readonly_session_mode_mismatch"),
            ("evidence", "readonly_session_counters_invalid"),
            ("identity", "readonly_session_identity_mismatch"),
            ("host", "readonly_session_host_invalid"),
        ):
            rows = transcript()
            result = rows[4]["result"]
            session = result["structuredContent"]["result"]
            if mutation == "open":
                session["open"] = True
            elif mutation == "targets":
                session["targets"] = [{"id": "unexpected"}]
            elif mutation == "project":
                session["project"] = {"name": "unexpected"}
            elif mutation == "mode":
                session["analysis_mode"] = "clean-room"
            elif mutation == "evidence":
                session["evidence_records"] = 1
            elif mutation == "identity":
                session["server"]["version"] = "wrong"
            else:
                session["host"] = ""
            result["content"][0]["text"] = json.dumps(result["structuredContent"])
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, reason):
                mcp.validate_transcript(encoded(rows), evidence())

    def test_unknown_tool_call_must_return_typed_refusal(self) -> None:
        for mutation, reason in (
            ("success", "unknown_tool_not_rejected"),
            ("missing_content", "unknown_tool_error_text_missing"),
            ("text", "unknown_tool_error_not_json"),
            ("payload", "unknown_tool_error_invalid"),
            ("code", "unknown_tool_error_invalid"),
            ("message", "unknown_tool_error_invalid"),
            ("remediation", "unknown_tool_error_invalid"),
        ):
            rows = transcript()
            reply = rows[5]["result"]
            if mutation == "success":
                reply["isError"] = False
            elif mutation == "missing_content":
                reply["content"] = []
            elif mutation == "text":
                reply["content"][0]["text"] = "not JSON"
            else:
                payload = json.loads(reply["content"][0]["text"])
                if mutation == "payload":
                    payload.pop("error")
                elif mutation == "code":
                    payload["error"]["code"] = "not_found"
                elif mutation == "message":
                    payload["error"]["message"] = "some other failure"
                else:
                    payload["error"]["remediation"] = ""
                reply["content"][0]["text"] = json.dumps(payload)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, reason):
                mcp.validate_transcript(encoded(rows), evidence())

    def test_readonly_descriptor_must_be_readonly_and_parameter_free(self) -> None:
        for mutation, reason in (
            ("params", "readonly_input_schema_invalid"),
            ("readonly", "readonly_annotations_invalid"),
            ("destructive", "readonly_annotations_invalid"),
            ("idempotent", "readonly_annotations_invalid"),
            ("open_world", "readonly_annotations_invalid"),
            ("title", "readonly_annotation_title_invalid"),
            ("missing", "readonly_descriptor_missing"),
            ("unknown_registered", "unknown_tool_unexpectedly_registered"),
        ):
            rows = transcript()
            tool = rows[1]["result"]["tools"][0]
            if mutation == "params":
                tool["inputSchema"]["additionalProperties"] = True
            elif mutation == "readonly":
                tool["annotations"]["readOnlyHint"] = False
            elif mutation == "destructive":
                tool["annotations"]["destructiveHint"] = True
            elif mutation == "idempotent":
                tool["annotations"]["idempotentHint"] = False
            elif mutation == "open_world":
                tool["annotations"]["openWorldHint"] = True
            elif mutation == "title":
                tool["annotations"]["title"] = "different"
            elif mutation == "missing":
                tool["name"] = "missing_core_session_show"
            else:
                rows[1]["result"]["tools"][1]["name"] = mcp.UNKNOWN_TOOL
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, reason):
                mcp.validate_transcript(encoded(rows), evidence())

    def test_rejects_tool_count_duplicates_schema_and_contract_drift(self) -> None:
        for mutation, reason in (("missing", "tool_count_mismatch"),
                                 ("duplicate", "duplicate_tool"),
                                 ("schema", "schema_or_annotations_invalid"),
                                 ("annotation", "tool_annotations_invalid"),
                                 ("description", "registry_contract_mismatch")):
            rows = transcript()
            tools = rows[1]["result"]["tools"]
            if mutation == "missing":
                tools.pop()
            elif mutation == "duplicate":
                tools[1]["name"] = tools[0]["name"]
            elif mutation == "schema":
                tools[0]["inputSchema"] = None
            elif mutation == "annotation":
                tools[0]["annotations"]["readOnlyHint"] = "yes"
            else:
                tools[0]["description"] = "Altered tool summary"
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, reason):
                mcp.validate_transcript(encoded(rows), evidence())

    def test_process_drains_separate_stdout_and_stderr(self) -> None:
        fixture = (
            "import sys, json\n"
            "for line in sys.stdin:\n"
            "    m=json.loads(line)\n"
            "    if 'id' in m:\n"
            "        sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':{}})+'\\n')\n"
            "sys.stderr.write('private diagnostic')\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            output = mcp.run_protocol_process([sys.executable, "-u", "-c", fixture], Path(temp), dict(os.environ), 5)
        self.assertEqual(output["status"], "pass")
        self.assertEqual(len(output["stdout"].splitlines()), 6)
        self.assertEqual(output["stderr_bytes"], len("private diagnostic"))
        self.assertNotIn(b"private diagnostic", output["stdout"])

    def test_process_timeout_and_output_cap_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            timed = mcp.run_protocol_process([sys.executable, "-u", "-c", "import time; time.sleep(10)"],
                                             Path(temp), dict(os.environ), 1)
            self.assertEqual(timed["status"], "fail")
            self.assertEqual(timed["reason"], "timed_out")
            with patch.object(mcp, "MAX_STDOUT_BYTES", 1024):
                over = mcp.run_protocol_process([sys.executable, "-u", "-c", "print('x'*10000)"],
                                                Path(temp), dict(os.environ), 5)
            self.assertEqual(over["status"], "fail")
            self.assertEqual(over["reason"], "output_limit_exceeded")
            self.assertLessEqual(len(over["stdout"]), 1024)

    def test_process_keeps_multi_megabyte_json_rpc_line_intact(self) -> None:
        # MCP tool schemas can produce a single JSON-RPC line larger than 1 MB.
        fixture = (
            "import sys, json\n"
            "sys.stdin.read()\n"
            "sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':2,"
            "'result':{'blob':'x'*2500000}})+'\\n')\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            result = mcp.run_protocol_process([sys.executable, "-u", "-c", fixture],
                                              Path(temp), dict(os.environ), 10)
        self.assertEqual(result["status"], "pass")
        self.assertGreater(result["stdout_bytes"], 2_500_000)
        self.assertEqual(len(result["stdout"].splitlines()), 1)
        self.assertEqual(len(json.loads(result["stdout"])["result"]["blob"]), 2_500_000)

    def test_smoke_is_isolated_and_does_not_persist_raw_tool_schema(self) -> None:
        binary = Path(sys.executable).resolve()
        observed = []

        def fake_process(command, root, env, timeout):
            self.assertEqual(command[1:], ["--deep", "mcp", "--all-tools"])
            self.assertTrue(Path(env["CRAFT_ANYTHING_CONFIG_DIR"]).is_dir())
            self.assertEqual(env["HOME"], env["APPDATA"])
            self.assertNotIn("OPENAI_API_KEY", env)
            observed.append(timeout)
            return {"status": "pass", "stdout": encoded(transcript()),
                    "stdout_bytes": len(encoded(transcript())), "stderr_bytes": 3}

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}), \
                patch.object(mcp, "cli_binary_path", return_value=binary), \
                patch.object(mcp, "run_protocol_process", side_effect=fake_process):
            report = mcp.smoke_mcp(ROOT, 100, evidence())
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["mcp_tool_count"], 200)
        self.assertEqual(report["command"], ["<built-cli>", "--deep", "mcp", "--all-tools"])
        self.assertNotIn("stdout", report)
        self.assertNotIn("inputSchema", json.dumps(report))
        self.assertEqual(observed, [mcp.MCP_TIMEOUT])

    def test_smoke_rejects_registry_failure_and_native_binary_mutation(self) -> None:
        blocked = mcp.smoke_mcp(ROOT, 5, {"status": "fail"})
        self.assertEqual(blocked["status"], "blocked")
        binary = Path(sys.executable).resolve()
        with patch.object(mcp, "cli_binary_path", return_value=binary), \
                patch.object(mcp, "run_protocol_process", return_value={
                    "status": "pass", "stdout": encoded(transcript())}), \
                patch.object(mcp, "hash_binary", side_effect=[
                    {"binary_sha256": "a" * 64, "binary_bytes": 100},
                    {"binary_sha256": "b" * 64, "binary_bytes": 100},
                ]):
            report = mcp.smoke_mcp(ROOT, 5, evidence())
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["reason"], "cli_binary_changed_during_mcp_smoke")


if __name__ == "__main__":
    unittest.main()
