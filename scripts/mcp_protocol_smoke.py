#!/usr/bin/env python3
"""Bounded, credential-free native MCP handshake and full tool-list smoke.

Only JSON-RPC initialize, initialized notification, tools/list, ping, one
unknown-method negative control, one explicitly allowlisted read-only
`core_session_show` call, and one nonexistent-tool rejection probe are sent.
No target operations or potentially effectful registered tools are invoked.
The CLI is launched in --deep for complete registry enumeration; no target is
opened and the inspected state must remain empty.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from native_validation import cli_binary_path, hash_binary, stop_process
from registry_smoke import mcp_contract_digest, private_cli_environment


MCP_TIMEOUT = 60
MAX_STDOUT_BYTES = 24_000_000  # tools/list is one potentially multi-MB JSON-RPC line
MAX_STDERR_BYTES = 256_000
PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "craft-anything"
EXPECTED_IDS = {1, 2, 3, 4, 5, 6}
READ_ONLY_TOOL = "core_session_show"
UNKNOWN_TOOL = "__craft_smoke_nonexistent_tool__"
BOOL_ANNOTATIONS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True, "destructiveHint": False,
    "idempotentHint": True, "openWorldHint": False,
}
EMPTY_INPUT_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


def protocol_input() -> bytes:
    """One finite MCP session with two safe tools/call probes."""
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "craft-native-smoke", "version": "1"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}},
        {"jsonrpc": "2.0", "id": 4, "method": "__craft_smoke_unknown_method__", "params": {}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
            "name": READ_ONLY_TOOL, "arguments": {},
        }},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {
            "name": UNKNOWN_TOOL, "arguments": {},
        }},
    ]
    return ("\n".join(json.dumps(m, separators=(",", ":")) for m in messages) + "\n").encode("utf-8")


def run_protocol_process(command: list[str], root: Path, env: dict[str, str], timeout: int) -> dict:
    """Drain separate pipes concurrently, enforce output/deadline caps, reap child."""
    start = time.monotonic()
    try:
        process = subprocess.Popen(
            command, cwd=root, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=(os.name != "nt"),
            creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0),
        )
    except OSError:
        return {"status": "fail", "reason": "launch_failed", "duration_seconds": round(time.monotonic() - start, 3)}

    stdout = bytearray()
    stderr = bytearray()
    excessive = threading.Event()
    read_error = threading.Event()

    def drain(pipe, output: bytearray, limit: int) -> None:
        try:
            while chunk := pipe.read1(64 * 1024):
                free = max(0, limit - len(output))
                output.extend(chunk[:free])
                if len(chunk) > free:
                    excessive.set()
        except OSError:
            read_error.set()
        finally:
            pipe.close()

    readers: list[threading.Thread] = []
    if process.stdout is not None and process.stderr is not None:
        for pipe, target, limit in ((process.stdout, stdout, MAX_STDOUT_BYTES),
                                    (process.stderr, stderr, MAX_STDERR_BYTES)):
            thread = threading.Thread(target=drain, args=(pipe, target, limit), daemon=True)
            thread.start()
            readers.append(thread)
    else:
        read_error.set()

    write_failed = False
    try:
        if process.stdin is None:
            write_failed = True
        else:
            try:
                process.stdin.write(protocol_input())
                process.stdin.flush()
            except OSError:
                write_failed = True
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    write_failed = True

        deadline = start + timeout
        timed_out = False
        while process.poll() is None:
            if excessive.is_set() or read_error.is_set() or write_failed:
                stop_process(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                stop_process(process)
                break
            time.sleep(0.02)

        stuck = False
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            stuck = True
            stop_process(process)
        for reader in readers:
            reader.join(timeout=3)
            if reader.is_alive():
                stuck = True
                stop_process(process)
        reason = (
            "process_or_descendant_stuck" if stuck else
            "timed_out" if timed_out else
            "output_limit_exceeded" if excessive.is_set() else
            "pipe_read_failed" if read_error.is_set() else
            "stdin_write_failed" if write_failed else
            "nonzero_exit" if process.returncode != 0 else None
        )
        return {
            "status": "fail" if reason else "pass", "reason": reason,
            "duration_seconds": round(time.monotonic() - start, 3),
            "returncode": process.returncode,
            "stdout": bytes(stdout), "stdout_bytes": len(stdout),
            "stderr_bytes": len(stderr),
        }
    finally:
        if process.poll() is None:
            stop_process(process)


def validate_transcript(raw: bytes, registry: dict) -> dict:
    """Validate native JSON-RPC behavior and match the full CLI tool contract."""
    try:
        lines = raw.decode("utf-8", errors="strict").splitlines()
        messages = [json.loads(line) for line in lines]
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("mcp_output_not_json_rpc") from exc
    if len(messages) != len(EXPECTED_IDS):
        raise ValueError("mcp_response_count_invalid")
    responses = {}
    for item in messages:
        if not isinstance(item, dict) or item.get("jsonrpc") != "2.0":
            raise ValueError("mcp_response_envelope_invalid")
        if ("result" in item) == ("error" in item):
            raise ValueError("mcp_result_error_envelope_invalid")
        request_id = item.get("id")
        if not isinstance(request_id, int) or isinstance(request_id, bool) or request_id not in EXPECTED_IDS:
            raise ValueError("mcp_response_id_invalid")
        if request_id in responses:
            raise ValueError("mcp_duplicate_response_id")
        responses[request_id] = item
    if set(responses) != EXPECTED_IDS:
        raise ValueError("mcp_missing_response_id")

    for request_id in (1, 2, 3, 5):
        item = responses[request_id]
        if "error" in item or not isinstance(item.get("result"), dict):
            raise ValueError(f"mcp_request_{request_id}_failed")
    hello = responses[1]["result"]
    if hello.get("protocolVersion") != PROTOCOL_VERSION:
        raise ValueError("mcp_protocol_negotiation_mismatch")
    info = hello.get("serverInfo")
    if not isinstance(info, dict) or info.get("name") != SERVER_NAME or info.get("version") != registry.get("cli_version"):
        raise ValueError("mcp_server_identity_mismatch")
    capabilities = hello.get("capabilities")
    if not isinstance(capabilities, dict) or not isinstance(capabilities.get("tools"), dict):
        raise ValueError("mcp_handshake_capabilities_missing")
    if responses[3]["result"] != {}:
        raise ValueError("mcp_ping_reply_invalid")
    negative = responses[4].get("error")
    if not isinstance(negative, dict) or negative.get("code") != -32601:
        raise ValueError("mcp_unknown_method_not_rejected")

    rejected = responses[6].get("result")
    if not isinstance(rejected, dict) or rejected.get("isError") is not True:
        raise ValueError("mcp_unknown_tool_not_rejected")
    blocks = rejected.get("content")
    if (not isinstance(blocks, list) or len(blocks) != 1 or not isinstance(blocks[0], dict)
            or blocks[0].get("type") != "text" or not isinstance(blocks[0].get("text"), str)):
        raise ValueError("mcp_unknown_tool_error_text_missing")
    try:
        rejected_json = json.loads(blocks[0]["text"])
    except json.JSONDecodeError as exc:
        raise ValueError("mcp_unknown_tool_error_not_json") from exc
    if not isinstance(rejected_json, dict) or not isinstance(rejected_json.get("error"), dict):
        raise ValueError("mcp_unknown_tool_error_invalid")
    denied = rejected_json["error"]
    if (denied.get("code") != "bad_input"
            or not isinstance(denied.get("message"), str) or UNKNOWN_TOOL not in denied["message"]
            or not isinstance(denied.get("remediation"), str) or not denied["remediation"].strip()):
        raise ValueError("mcp_unknown_tool_error_invalid")

    status = responses[5]["result"]
    if status.get("isError") is not False or not isinstance(status.get("structuredContent"), dict):
        raise ValueError("mcp_readonly_tool_failed")
    content = status.get("content")
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        raise ValueError("mcp_readonly_text_missing")
    text_body = content[0].get("text")
    if content[0].get("type") != "text" or not isinstance(text_body, str):
        raise ValueError("mcp_readonly_text_missing")
    try:
        text_value = json.loads(text_body)
    except json.JSONDecodeError as exc:
        raise ValueError("mcp_readonly_text_not_json") from exc
    if text_value != status["structuredContent"]:
        raise ValueError("mcp_readonly_text_structured_mismatch")
    session = status["structuredContent"].get("result")
    if not isinstance(session, dict):
        raise ValueError("mcp_readonly_session_invalid")
    if (session.get("open") is not False or session.get("active_target") is not None
            or session.get("targets") != [] or session.get("project") is not None):
        raise ValueError("mcp_readonly_session_not_empty")
    if session.get("analysis_mode") != "deep" or session.get("clean_room") is not False:
        raise ValueError("mcp_readonly_session_mode_mismatch")
    for field in ("evidence_records", "evidence_not_loaded", "unknowns"):
        count = session.get(field)
        if not isinstance(count, int) or isinstance(count, bool) or count != 0:
            raise ValueError("mcp_readonly_session_counters_invalid")
    about = session.get("server")
    if not isinstance(about, dict) or about.get("name") != SERVER_NAME or about.get("version") != registry.get("cli_version"):
        raise ValueError("mcp_readonly_session_identity_mismatch")
    if not isinstance(session.get("host"), str) or not session["host"].strip():
        raise ValueError("mcp_readonly_session_host_invalid")

    tools = responses[2]["result"].get("tools")
    if not isinstance(tools, list) or len(tools) != registry.get("command_count"):
        raise ValueError("mcp_tool_count_mismatch")
    seen: set[str] = set()
    contract = []
    readonly_descriptor_verified = False
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            raise ValueError(f"mcp_tool_{index}_invalid")
        for key in ("name", "title", "description"):
            if not isinstance(tool.get(key), str) or not tool[key].strip():
                raise ValueError(f"mcp_tool_{index}_{key}_invalid")
        if tool["name"] in seen:
            raise ValueError("mcp_duplicate_tool")
        seen.add(tool["name"])
        if not isinstance(tool.get("inputSchema"), dict) or not isinstance(tool.get("annotations"), dict):
            raise ValueError("mcp_tool_schema_or_annotations_invalid")
        if any(not isinstance(tool["annotations"].get(key), bool) for key in BOOL_ANNOTATIONS):
            raise ValueError("mcp_tool_annotations_invalid")
        if tool["name"] == READ_ONLY_TOOL:
            if tool["inputSchema"] != EMPTY_INPUT_SCHEMA:
                raise ValueError("mcp_readonly_input_schema_invalid")
            if any(tool["annotations"][key] is not value for key, value in READ_ONLY_ANNOTATIONS.items()):
                raise ValueError("mcp_readonly_annotations_invalid")
            if tool["annotations"].get("title") != tool["title"]:
                raise ValueError("mcp_readonly_annotation_title_invalid")
            readonly_descriptor_verified = True
        contract.append({"name": tool["name"], "title": tool["title"], "description": tool["description"]})
    if not readonly_descriptor_verified:
        raise ValueError("mcp_readonly_descriptor_missing")
    if UNKNOWN_TOOL in seen:
        raise ValueError("mcp_unknown_tool_unexpectedly_registered")
    digest = mcp_contract_digest(contract)
    if digest != registry.get("registry_mcp_contract_sha256"):
        raise ValueError("mcp_registry_contract_mismatch")
    return {"mcp_tool_count": len(tools), "mcp_contract_sha256": digest,
            "mcp_protocol_version": PROTOCOL_VERSION, "mcp_server_version": info["version"],
            "mcp_readonly_tool": READ_ONLY_TOOL, "mcp_readonly_session_empty": True,
            "mcp_readonly_descriptor_verified": True, "mcp_unknown_tool_refused": True}


def smoke_mcp(root: Path, timeout: int, registry: dict) -> dict:
    """Run the real server; never retain raw tool schemas or local stderr."""
    sanitized_command = ["<built-cli>", "--deep", "mcp", "--all-tools"]
    if registry.get("status") != "pass":
        return {"name": "mcp_smoke", "status": "blocked", "reason": "registry_smoke_failed"}
    try:
        binary = cli_binary_path(root)
        before = hash_binary(binary)
    except (OSError, ValueError):
        return {"name": "mcp_smoke", "status": "fail", "reason": "cli_binary_unavailable_before_mcp"}
    with tempfile.TemporaryDirectory(prefix="craft_mcp_smoke_") as temporary:
        env = private_cli_environment(Path(temporary))
        result = run_protocol_process([str(binary), "--deep", "mcp", "--all-tools"], root, env,
                                      min(MCP_TIMEOUT, timeout))
        raw = result.pop("stdout", b"")
        result["name"] = "mcp_smoke"
        result["command"] = sanitized_command
        if result["status"] == "pass":
            try:
                result.update(validate_transcript(raw, registry))
            except ValueError as exc:
                result["status"] = "fail"
                result["reason"] = str(exc)
    try:
        after = hash_binary(binary)
        if after != before:
            result["status"] = "fail"
            result["reason"] = "cli_binary_changed_during_mcp_smoke"
        else:
            result.update(after)
    except (OSError, ValueError):
        result["status"] = "fail"
        result["reason"] = "cli_binary_unavailable_after_mcp_smoke"
    return result
