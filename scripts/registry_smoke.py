#!/usr/bin/env python3
"""Cross-check two real CLI metadata views in an isolated environment.

This smoke never calls a project tool, accesses a target or authorizes effects.
Only metadata-listing `commands` and `capabilities` subcommands are invoked.
A successfully smoked binary must remain unchanged after both invocations.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from native_validation import cli_binary_path, execute, hash_binary


REGISTRY_TIMEOUT = 60
MIN_COMMANDS = 200
FIELDS = ("command", "tool", "toolset", "title", "summary")
SAFE_HOST_ENV = {
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
}


def private_cli_environment(isolated: Path) -> dict[str, str]:
    """Construct the disposable config and credential-free environment."""
    config_dir = isolated / "config"
    config_dir.mkdir()
    env = {key: value for key, value in os.environ.items() if key.upper() in SAFE_HOST_ENV}
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        env[key] = str(isolated)
    for key in ("TEMP", "TMP", "TMPDIR"):
        env[key] = str(isolated)
    env["CRAFT_ANYTHING_CONFIG_DIR"] = str(config_dir)
    env["CRAFT_ANYTHING_LOG"] = "off"
    return env


def mcp_contract_digest(contracts: list[dict]) -> str:
    """Canonical digest of tool IDs, titles and summaries across CLI/MCP."""
    sorted_contracts = sorted(contracts, key=lambda item: item["name"])
    canonical = json.dumps(sorted_contracts, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_registry(raw: str) -> dict:
    """Parse bounded native output, requiring distinct, typed registry entries."""
    try:
        entries = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("registry_output_is_not_json") from exc
    if not isinstance(entries, list):
        raise ValueError("registry_root_is_not_array")
    if len(entries) < MIN_COMMANDS:
        raise ValueError("registry_has_too_few_commands")
    tool_ids: set[str] = set()
    command_names: set[str] = set()
    toolsets: set[str] = set()
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ValueError(f"registry_entry_{index}_not_object")
        if any(not isinstance(item.get(field), str) or not item[field].strip() for field in FIELDS):
            raise ValueError(f"registry_entry_{index}_has_invalid_fields")
        if not isinstance(item.get("needs_target"), bool):
            raise ValueError(f"registry_entry_{index}_has_invalid_target_flag")
        if item["tool"] in tool_ids or item["command"] in command_names:
            raise ValueError(f"registry_entry_{index}_duplicate_identifier")
        tool_ids.add(item["tool"])
        command_names.add(item["command"])
        toolsets.add(item["toolset"])
    if len(toolsets) < 3:
        raise ValueError("registry_has_too_few_toolsets")
    canonical = json.dumps(entries, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    contract = [
        {"name": item["tool"], "title": item["title"], "description": item["summary"]}
        for item in entries
    ]
    return {
        "command_count": len(entries),
        "toolset_count": len(toolsets),
        "registry_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "registry_mcp_contract_sha256": mcp_contract_digest(contract),
    }


def validate_capabilities(raw: str, registry_raw: str) -> dict:
    """Check that `capabilities` describes exactly the same registered tools."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("capabilities_output_is_not_json") from exc
    if not isinstance(data, dict):
        raise ValueError("capabilities_root_is_not_object")
    if not isinstance(data.get("version"), str) or not data["version"].strip():
        raise ValueError("capabilities_version_invalid")
    if not isinstance(data.get("host"), str) or not data["host"].strip():
        raise ValueError("capabilities_host_invalid")
    if data.get("analysis_mode") not in ("clean-room", "deep"):
        raise ValueError("capabilities_mode_invalid")
    if not isinstance(data.get("tools"), int) or isinstance(data["tools"], bool):
        raise ValueError("capabilities_tool_count_invalid")
    if not isinstance(data.get("toolsets"), list) or not isinstance(data.get("engines"), list):
        raise ValueError("capabilities_collections_invalid")

    commands = json.loads(registry_raw)  # already validated by validate_registry
    expected = {item["tool"]: item for item in commands}
    if data["tools"] != len(expected):
        raise ValueError("capabilities_tool_count_mismatch")
    observed: set[str] = set()
    observed_toolsets: set[str] = set()
    for index, group in enumerate(data["toolsets"]):
        if not isinstance(group, dict) or not isinstance(group.get("id"), str) or not group["id"].strip():
            raise ValueError(f"capabilities_toolset_{index}_invalid")
        if not isinstance(group.get("title"), str) or not group["title"].strip():
            raise ValueError(f"capabilities_toolset_{index}_title_invalid")
        if not isinstance(group.get("tools"), list) or group["id"] in observed_toolsets:
            raise ValueError(f"capabilities_toolset_{index}_tools_invalid")
        observed_toolsets.add(group["id"])
        for item in group["tools"]:
            if not isinstance(item, dict) or not isinstance(item.get("tool"), str):
                raise ValueError("capabilities_tool_record_invalid")
            tool = item["tool"]
            if tool not in expected or tool in observed:
                raise ValueError("capabilities_tool_missing_extra_or_duplicate")
            if group["id"] != expected[tool]["toolset"]:
                raise ValueError("capabilities_toolset_assignment_mismatch")
            if item.get("needs_target") is not expected[tool]["needs_target"]:
                raise ValueError("capabilities_target_requirement_mismatch")
            if not isinstance(item.get("available"), bool):
                raise ValueError("capabilities_availability_invalid")
            if item.get("reason") is not None and not isinstance(item["reason"], str):
                raise ValueError("capabilities_availability_reason_invalid")
            observed.add(tool)
    if observed != set(expected):
        raise ValueError("capabilities_tool_missing_extra_or_duplicate")

    engine_ids: set[str] = set()
    for index, engine in enumerate(data["engines"]):
        if not isinstance(engine, dict) or not isinstance(engine.get("id"), str) or not engine["id"].strip():
            raise ValueError(f"capabilities_engine_{index}_invalid")
        if engine["id"] in engine_ids or not isinstance(engine.get("name"), str) or not engine["name"].strip():
            raise ValueError(f"capabilities_engine_{index}_duplicate_or_invalid")
        if not isinstance(engine.get("needed_for"), str) or not engine["needed_for"].strip():
            raise ValueError(f"capabilities_engine_{index}_purpose_invalid")
        if not isinstance(engine.get("found"), bool) or not isinstance(engine.get("installable_here"), bool):
            raise ValueError(f"capabilities_engine_{index}_flags_invalid")
        path = engine.get("path")
        if (path is not None and (not isinstance(path, str) or not path.strip())) or engine["found"] != (path is not None):
            raise ValueError(f"capabilities_engine_{index}_path_inconsistent")
        engine_ids.add(engine["id"])

    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {
        "capabilities_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "capability_toolset_count": len(observed_toolsets),
        "capability_engine_count": len(engine_ids),
        "cli_version": data["version"],
    }


def smoke_registry(root: Path, timeout: int) -> dict:
    """Run both metadata commands against one private, disposable config."""
    commands = [["<built-cli>", "commands"], ["<built-cli>", "capabilities"]]
    try:
        binary = cli_binary_path(root)
        before = hash_binary(binary)
    except (ValueError, OSError):
        return {"name": "registry_smoke", "commands": commands, "status": "fail", "reason": "cli_binary_unavailable_before_smoke"}

    with tempfile.TemporaryDirectory(prefix="craft_cli_registry_") as temporary:
        isolated = Path(temporary)
        # Do not hand API keys, GitHub credentials, agent config or the person's
        # session environment to a test executable. Native PATH and OS basics
        # are sufficient for the metadata-only CLI launch.
        env = private_cli_environment(isolated)
        result = execute(
            "registry_smoke", [str(binary), "commands"], root,
            min(timeout, REGISTRY_TIMEOUT), env=env, capture_full_output=True,
        )
        raw = result.pop("full_output", "")
        result.pop("output_excerpt", None)  # no raw metadata or process diagnostics in receipts
        result.pop("command", None)
        result["commands"] = commands  # no user-specific executable paths
        if result["status"] != "pass" and result.get("reason") not in (
            "timed_out", "output_limit_exceeded", "process_or_descendant_stuck", "nonzero_exit"
        ):
            result["reason"] = "commands_execution_failed"
        if result["status"] == "pass":
            try:
                result.update(validate_registry(raw))
                result.pop("output_excerpt", None)
                second = execute(
                    "capabilities_smoke", [str(binary), "capabilities"], root,
                    min(timeout, REGISTRY_TIMEOUT), env=env, capture_full_output=True,
                )
                capabilities_raw = second.pop("full_output", "")
                result["capabilities_duration_seconds"] = second.get("duration_seconds")
                if second["status"] != "pass":
                    result["status"] = "fail"
                    reason = second.get("reason")
                    if reason not in ("timed_out", "output_limit_exceeded", "process_or_descendant_stuck", "nonzero_exit"):
                        reason = "execution_failed"
                    result["reason"] = "capabilities_" + reason
                else:
                    result.update(validate_capabilities(capabilities_raw, raw))
            except ValueError as exc:
                result["status"] = "fail"
                result["reason"] = str(exc)
    try:
        after = hash_binary(binary)
        if before != after:
            result["status"] = "fail"
            result["reason"] = "cli_binary_changed_during_registry_smoke"
        else:
            result.update(after)
    except (ValueError, OSError):
        result["status"] = "fail"
        result["reason"] = "cli_binary_unavailable_after_registry_smoke"
    return result
