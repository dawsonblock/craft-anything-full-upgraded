#!/usr/bin/env python3
"""Bounded, sequential MCP open → WASM parse → close integration smoke.

Each state-changing JSON-RPC request awaits its reply before the next one is
sent. That ordering matters: the native MCP server can process requests on
concurrent worker threads. No arbitrary files, credentials, raw results, or
local paths are retained in the resulting CI receipt.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading
import time
from typing import Any

from mcp_protocol_smoke import PROTOCOL_VERSION, SERVER_NAME
from native_triage_smoke import WASM_FIXTURE, WASM_SHA256
from native_validation import cli_binary_path, hash_binary, stop_process
from registry_smoke import private_cli_environment


TARGET_TIMEOUT = 75
MAX_STDOUT = 2_000_000
MAX_STDERR = 64_000
MAX_MESSAGES = 16
FIXTURE_NAME = "craft_native_smoke.wasm"
OPERATIONS = (
    "core_session_show", "core_target_open", "formats_wasm_module_show",
    "core_session_show", "core_target_close", "core_session_show",
)


class TargetSmokeError(ValueError):
    """A stable, sanitized reason suitable for an unsigned CI receipt."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise TargetSmokeError(reason)


class McpDialog:
    """Single subprocess, bounded reader threads, sequential JSON-RPC requests."""

    def __init__(self, command: list[str], root: Path, env: dict[str, str], timeout: int):
        self.deadline = time.monotonic() + min(timeout, TARGET_TIMEOUT)
        self.messages: queue.Queue[bytes] = queue.Queue(maxsize=MAX_MESSAGES)
        self.bad_io = threading.Event()
        self.too_large = threading.Event()
        self.stdout_bytes = 0
        self.stderr_bytes = 0
        self.next_id = 1
        self.closed = False
        self.process = subprocess.Popen(
            command, cwd=root, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=(os.name != "nt"),
            creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0),
        )
        self.threads = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for thread in self.threads:
            thread.start()

    def _read_stdout(self) -> None:
        pipe = self.process.stdout
        if pipe is None:
            self.bad_io.set()
            return
        pending = bytearray()
        try:
            while chunk := pipe.read1(16 * 1024):
                self.stdout_bytes += len(chunk)
                if self.stdout_bytes > MAX_STDOUT:
                    self.too_large.set()
                    break
                pending.extend(chunk)
                while b"\n" in pending:
                    end = pending.index(b"\n")
                    line = bytes(pending[:end]).strip()
                    del pending[:end + 1]
                    if line:
                        try:
                            self.messages.put_nowait(line)
                        except queue.Full:
                            self.too_large.set()
                            return
            if pending.strip():
                self.bad_io.set()
        except OSError:
            self.bad_io.set()
        finally:
            pipe.close()

    def _read_stderr(self) -> None:
        pipe = self.process.stderr
        if pipe is None:
            self.bad_io.set()
            return
        try:
            while chunk := pipe.read1(16 * 1024):
                self.stderr_bytes += len(chunk)
                if self.stderr_bytes > MAX_STDERR:
                    self.too_large.set()
                    break
        except OSError:
            self.bad_io.set()
        finally:
            pipe.close()

    def _send(self, message: dict) -> None:
        try:
            if self.process.stdin is None:
                raise BrokenPipeError()
            self.process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
            self.process.stdin.flush()
        except OSError as exc:
            raise TargetSmokeError("mcp_target_request_write_failed") from exc

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def ask(self, method: str, params: dict) -> dict:
        rid = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            if self.too_large.is_set():
                raise TargetSmokeError("mcp_target_output_limit_exceeded")
            if self.bad_io.is_set():
                raise TargetSmokeError("mcp_target_response_pipe_failed")
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TargetSmokeError("mcp_target_timed_out")
            try:
                raw = self.messages.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                if self.process.poll() is not None and self.messages.empty():
                    raise TargetSmokeError("mcp_target_server_exited_early")
                continue
            try:
                reply = json.loads(raw.decode("utf-8", errors="strict"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise TargetSmokeError("mcp_target_invalid_json") from exc
            require(isinstance(reply, dict) and reply.get("jsonrpc") == "2.0", "mcp_target_bad_envelope")
            require(type(reply.get("id")) is int and reply["id"] == rid, "mcp_target_out_of_order_response")
            require("result" in reply and "error" not in reply, "mcp_target_request_failed")
            require(isinstance(reply["result"], dict), "mcp_target_result_not_object")
            return reply["result"]

    def finish(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
            remaining = max(0.0, self.deadline - time.monotonic())
            self.process.wait(timeout=min(remaining, 3.0))
            require(self.process.returncode == 0, "mcp_target_server_nonzero_exit")
            require(not self.too_large.is_set() and not self.bad_io.is_set(), "mcp_target_pipe_limit_or_error")
            require(self.messages.empty(), "mcp_target_unsolicited_reply")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TargetSmokeError("mcp_target_shutdown_failed") from exc
        finally:
            if self.process.poll() is None:
                stop_process(self.process)
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    stop_process(self.process)
            for thread in self.threads:
                thread.join(timeout=2)

    def abort(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.process.poll() is None:
            stop_process(self.process)
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            stop_process(self.process)
        for thread in self.threads:
            thread.join(timeout=2)


def tool(dialog: Any, name: str, args: dict) -> dict:
    """Require equivalent MCP text and structured payloads (as V14 does)."""
    body = dialog.ask("tools/call", {"name": name, "arguments": args})
    require(body.get("isError") is False, "mcp_target_tool_rejected")
    structured = body.get("structuredContent")
    require(isinstance(structured, dict), "mcp_target_structured_payload_missing")
    blocks = body.get("content")
    require(isinstance(blocks, list) and bool(blocks), "mcp_target_text_payload_missing")
    require(isinstance(blocks[0], dict) and blocks[0].get("type") == "text"
            and isinstance(blocks[0].get("text"), str), "mcp_target_text_payload_invalid")
    try:
        text_value = json.loads(blocks[0]["text"])
    except json.JSONDecodeError as exc:
        raise TargetSmokeError("mcp_target_text_payload_not_json") from exc
    require(text_value == structured, "mcp_target_text_structured_mismatch")
    return structured


def session_state(payload: dict) -> dict:
    state = payload.get("result")
    require(isinstance(state, dict), "mcp_target_session_invalid")
    require(state.get("analysis_mode") == "deep" and state.get("clean_room") is False,
            "mcp_target_not_in_deep_mode")
    require(state.get("project") is None, "mcp_target_unexpected_project")
    return state


def verify_parse(evidence: dict) -> None:
    require(evidence.get("operation") == "formats_wasm_module_show", "mcp_target_wrong_parser")
    require(isinstance(evidence.get("id"), str) and
            re.fullmatch(r"ev_[0-9a-f]{64}", evidence["id"]) is not None,
            "mcp_target_bad_evidence_id")
    subject = evidence.get("subject")
    require(isinstance(subject, dict) and subject.get("kind") == "wasm" and
            subject.get("sha256") == WASM_SHA256 and subject.get("name") == FIXTURE_NAME,
            "mcp_target_wrong_evidence_subject")
    environment = evidence.get("environment")
    require(isinstance(environment, dict) and environment.get("engine") == "builtin"
            and environment.get("clean_room") is False, "mcp_target_wrong_evidence_environment")
    result = evidence.get("result")
    require(isinstance(result, dict) and result.get("encoding") == "module" and
            type(result.get("version")) is int and result["version"] == 1 and
            type(result.get("size")) is int and result["size"] == len(WASM_FIXTURE),
            "mcp_target_wrong_wasm_format")
    validation = result.get("validation")
    require(isinstance(validation, dict) and validation.get("valid") is True and
            validation.get("error") is None, "mcp_target_wasm_validation_failed")
    require(result.get("parse_error") is None, "mcp_target_wasm_parse_incomplete")
    counts = result.get("counts")
    require(isinstance(counts, dict) and all(type(counts.get(key)) is int and counts[key] == 0
            for key in ("sections", "types", "imports", "exports", "functions", "memories",
                        "tables", "globals", "data_segments", "element_segments", "custom_sections")),
            "mcp_target_unexpected_wasm_structure")


def verify_sequence(dialog: Any, fixture: Path, cli_version: str) -> dict:
    """Synchronous state machine: each result is validated before next call."""
    hello = dialog.ask("initialize", {
        "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
        "clientInfo": {"name": "craft-target-smoke", "version": "1"},
    })
    info = hello.get("serverInfo")
    require(hello.get("protocolVersion") == PROTOCOL_VERSION and isinstance(info, dict) and
            info.get("name") == SERVER_NAME and info.get("version") == cli_version,
            "mcp_target_handshake_mismatch")
    dialog.notify("notifications/initialized")

    initial = session_state(tool(dialog, "core_session_show", {}))
    require(initial.get("open") is False and initial.get("targets") == [] and
            initial.get("active_target") is None and initial.get("evidence_records") == 0,
            "mcp_target_initial_session_not_empty")

    opened = tool(dialog, "core_target_open", {"path": str(fixture)}).get("result")
    require(isinstance(opened, dict), "mcp_target_open_payload_invalid")
    target_id = opened.get("target_id")
    require(isinstance(target_id, str) and re.fullmatch(r"t_[0-9a-f]{12}", target_id) is not None,
            "mcp_target_open_id_invalid")
    require(opened.get("sha256") == WASM_SHA256 and opened.get("name") == FIXTURE_NAME and
            opened.get("kind") == "wasm" and opened.get("bytes") == len(WASM_FIXTURE),
            "mcp_target_open_not_our_wasm")

    parsed = tool(dialog, "formats_wasm_module_show", {"detail": "summary"})
    verify_parse(parsed)

    active = session_state(tool(dialog, "core_session_show", {}))
    require(active.get("open") is True and active.get("active_target") == target_id and
            isinstance(active.get("targets"), list) and len(active["targets"]) == 1 and
            active["targets"][0].get("sha256") == WASM_SHA256,
            "mcp_target_not_active_during_parse")
    evidence_count = active.get("evidence_records")
    require(type(evidence_count) is int and evidence_count >= 1,
            "mcp_target_parser_evidence_not_recorded")

    closed = tool(dialog, "core_target_close", {"target_id": target_id}).get("result")
    require(isinstance(closed, dict) and closed.get("closed") == target_id and
            closed.get("evidence_records") == evidence_count,
            "mcp_target_close_not_confirmed")

    final = session_state(tool(dialog, "core_session_show", {}))
    require(final.get("open") is False and final.get("active_target") is None and
            final.get("targets") == [] and final.get("evidence_records") == evidence_count and
            final.get("unknowns") == 0,
            "mcp_target_cleanup_not_verified")
    return {
        "mcp_target_wasm_parsed": True,
        "mcp_target_state_closed": True,
        "mcp_target_evidence_preserved": True,
        "mcp_target_fixture_sha256": WASM_SHA256,
        "mcp_target_operations": len(OPERATIONS),
    }


def smoke_mcp_target(root: Path, timeout: int, registry: dict) -> dict:
    """Run only a synthetic target through one isolated, bounded native MCP."""
    started = time.monotonic()
    command = ["<built-cli>", "--deep", "mcp", "--all-tools"]
    report: dict = {"name": "mcp_target_smoke", "status": "fail", "command": command}
    try:
        binary = cli_binary_path(root)
        before = hash_binary(binary)
    except (OSError, ValueError):
        return {**report, "reason": "mcp_target_binary_missing"}
    dialog: McpDialog | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="craft_mcp_target_") as directory:
            isolated = Path(directory)
            fixture = isolated / FIXTURE_NAME
            fixture.write_bytes(WASM_FIXTURE)
            env = private_cli_environment(isolated)
            dialog = McpDialog([str(binary), "--deep", "mcp", "--all-tools"], root, env, timeout)
            report.update(verify_sequence(dialog, fixture, registry["cli_version"]))
            dialog.finish()
            report["status"] = "pass"
    except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        report["reason"] = str(exc) if isinstance(exc, TargetSmokeError) else "mcp_target_unexpected_failure"
    finally:
        if dialog is not None:
            dialog.abort()
    try:
        after = hash_binary(binary)
        if before != after:
            report["status"] = "fail"
            report["reason"] = "mcp_target_binary_mutated"
    except (OSError, ValueError):
        report["status"] = "fail"
        report["reason"] = "mcp_target_binary_unavailable_after_smoke"
    if report["status"] != "pass":
        for key in ("mcp_target_wasm_parsed", "mcp_target_state_closed",
                    "mcp_target_evidence_preserved", "mcp_target_fixture_sha256",
                    "mcp_target_operations"):
            report.pop(key, None)
    report.update(before)
    report["duration_seconds"] = round(time.monotonic() - started, 3)
    return report
