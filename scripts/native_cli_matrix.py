#!/usr/bin/env python3
"""Build and launch an unsigned native CLI with a fail-closed CI receipt.

This checks the CLI and a synthetic MCP target lifecycle, not the desktop GUI or signing.
Outputs must live outside the immutable source inventory.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys

from native_validation import check_toolchain, cli_binary_path, execute, hash_binary, smoke_cli
from mcp_protocol_smoke import READ_ONLY_TOOL, smoke_mcp
from native_mcp_target_smoke import OPERATIONS, smoke_mcp_target
from native_triage_smoke import EXPECTED_TOOL, WASM_SHA256, smoke_triage
from registry_smoke import smoke_registry
from source_bundle_integrity import IntegrityError, verify_tree


BUILD_ARGS = ("build", "--locked", "-p", "craft-anything-cli")
SCHEMA = 1


def receipt(checks: list[dict], version: str | None) -> dict:
    states = [entry["status"] for entry in checks]
    status = "fail" if "fail" in states else "blocked" if "blocked" in states else "pass"
    return {
        "schema": SCHEMA,
        "status": status,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.system(),
        "architecture": platform.machine(),
        "rust_version": version,
        "checks": checks,
        "manual_validation": "not_run",
        "limitations": [
            "This is an unsigned CI test executable, not an official or supported release.",
            "Metadata, MCP, synthetic WASM triage and stateful WASM parsing do not validate the GUI, devices or other formats.",
            "The source hash inventory is unsigned and does not authenticate its publisher.",
        ],
    }


def export_binary(root: Path, output_dir: Path, evidence: dict, registry: dict, mcp: dict, triage: dict, target: dict) -> dict:
    """Copy only a binary that passed native metadata, protocol, parsing and lifecycle."""
    binary = cli_binary_path(root)
    before = hash_binary(binary)
    if any(item.get("status") != "pass" for item in (evidence, registry, mcp, triage, target)):
        raise ValueError("All native smoke checks must pass before exporting a CLI")
    if before != {key: evidence.get(key) for key in ("binary_sha256", "binary_bytes")}:
        raise ValueError("Built CLI hash no longer matches the successful smoke receipt")
    if before != {key: registry.get(key) for key in ("binary_sha256", "binary_bytes")}:
        raise ValueError("Built CLI hash no longer matches the successful registry smoke receipt")
    if before != {key: mcp.get(key) for key in ("binary_sha256", "binary_bytes")}:
        raise ValueError("Built CLI hash no longer matches the successful MCP smoke receipt")
    if before != {key: triage.get(key) for key in ("binary_sha256", "binary_bytes")}:
        raise ValueError("Built CLI hash no longer matches the successful triage smoke receipt")
    if before != {key: target.get(key) for key in ("binary_sha256", "binary_bytes")}:
        raise ValueError("Built CLI hash no longer matches the stateful MCP target smoke receipt")
    for key in ("registry_sha256", "capabilities_sha256", "registry_mcp_contract_sha256"):
        digest = registry.get(key)
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"Registry smoke is missing a valid {key} digest")
    for key in ("command_count", "toolset_count", "capability_toolset_count", "capability_engine_count"):
        count = registry.get(key)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError(f"Registry smoke is missing a valid {key}")
    if registry["command_count"] < 200 or registry["toolset_count"] < 3:
        raise ValueError("Registry smoke reported insufficient command coverage")
    if registry["capability_toolset_count"] < registry["toolset_count"]:
        raise ValueError("Capabilities contain fewer toolsets than the registry")
    if not isinstance(registry.get("cli_version"), str) or not registry["cli_version"].strip():
        raise ValueError("Capabilities smoke is missing the CLI version")
    if mcp.get("mcp_tool_count") != registry["command_count"]:
        raise ValueError("MCP tool count differs from the command registry")
    if mcp.get("mcp_contract_sha256") != registry["registry_mcp_contract_sha256"]:
        raise ValueError("MCP tools differ from the command registry")
    if mcp.get("mcp_server_version") != registry["cli_version"]:
        raise ValueError("MCP server version differs from the CLI version")
    if not isinstance(mcp.get("mcp_protocol_version"), str) or not mcp["mcp_protocol_version"].strip():
        raise ValueError("MCP handshake is missing a protocol version")
    if mcp.get("mcp_readonly_tool") != READ_ONLY_TOOL or mcp.get("mcp_readonly_session_empty") is not True:
        raise ValueError("MCP read-only session query was not independently verified")
    if mcp.get("mcp_readonly_descriptor_verified") is not True:
        raise ValueError("MCP read-only descriptor annotations and schema were not verified")
    if mcp.get("mcp_unknown_tool_refused") is not True:
        raise ValueError("MCP unknown tool was not rejected with a typed error")
    if (triage.get("triage_evidence_verified") is not True or
            triage.get("triage_kind") != "wasm" or
            triage.get("triage_fixture_sha256") != WASM_SHA256 or
            triage.get("triage_tool") != EXPECTED_TOOL):
        raise ValueError("Native WASM triage evidence was not independently verified")
    if (target.get("mcp_target_wasm_parsed") is not True or
            target.get("mcp_target_state_closed") is not True or
            target.get("mcp_target_evidence_preserved") is not True or
            target.get("mcp_target_fixture_sha256") != WASM_SHA256 or
            target.get("mcp_target_operations") != len(OPERATIONS)):
        raise ValueError("Stateful MCP target analysis and cleanup were not independently verified")
    if not output_dir.parent.is_dir():
        raise ValueError("Artifact parent directory must already exist")
    output_dir.mkdir(exist_ok=False)
    destination = output_dir / binary.name
    try:
        with binary.open("rb") as source, destination.open("xb") as target:
            copied = 0
            while chunk := source.read(1024 * 1024):
                copied += len(chunk)
                if copied > 512_000_000:
                    raise ValueError("CLI executable grew beyond the 512 MB limit")
                target.write(chunk)
        shutil.copymode(binary, destination)
        after = hash_binary(destination)
        if after != before:
            raise ValueError("Artifact differs from the executable that passed smoke testing")
        provenance = {
            "schema": SCHEMA,
            "label": "UNSIGNED TEST EXECUTABLE - NOT A RELEASE",
            "filename": binary.name,
            "platform": platform.system(),
            "architecture": platform.machine(),
            "source_manifest_sha256": hashlib.sha256((root / "SOURCE_INTEGRITY.json").read_bytes()).hexdigest(),
            "registry_sha256": registry["registry_sha256"],
            "capabilities_sha256": registry["capabilities_sha256"],
            "registry_mcp_contract_sha256": registry["registry_mcp_contract_sha256"],
            "command_count": registry["command_count"],
            "toolset_count": registry["toolset_count"],
            "capability_toolset_count": registry["capability_toolset_count"],
            "capability_engine_count": registry["capability_engine_count"],
            "cli_version": registry["cli_version"],
            "mcp_tool_count": mcp["mcp_tool_count"],
            "mcp_protocol_version": mcp["mcp_protocol_version"],
            "mcp_readonly_tool": mcp["mcp_readonly_tool"],
            "mcp_readonly_session_empty": True,
            "mcp_readonly_descriptor_verified": True,
            "mcp_unknown_tool_refused": True,
            "triage_evidence_verified": True,
            "triage_kind": "wasm",
            "triage_fixture_sha256": WASM_SHA256,
            "triage_tool": EXPECTED_TOOL,
            "mcp_target_wasm_parsed": True,
            "mcp_target_state_closed": True,
            "mcp_target_evidence_preserved": True,
            "mcp_target_fixture_sha256": WASM_SHA256,
            "mcp_target_operations": len(OPERATIONS),
            **after,
        }
        (output_dir / "CLI_PROVENANCE.json").write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (OSError, ValueError):
        destination.unlink(missing_ok=True)
        (output_dir / "CLI_PROVENANCE.json").unlink(missing_ok=True)
        output_dir.rmdir()
        raise
    return {"name": "artifact_export", "status": "pass", "filename": binary.name, **after}


def run_matrix(
    root: Path, output_dir: Path, timeout: int,
    cargo: str | None = None, rustc: str | None = None,
) -> dict:
    root = root.resolve()
    output_dir = output_dir.resolve()
    if not 1 <= timeout <= 3600:
        raise ValueError("Build timeout must be between 1 and 3600 seconds")
    if not root.is_dir():
        raise ValueError("Source root must be an existing directory")
    if output_dir.is_relative_to(root):
        raise ValueError("Artifact directory must be outside the source root")

    checks: list[dict] = []
    version = None
    try:
        manifest = verify_tree(root)
        checks.append({
            "name": "source_integrity", "status": "pass", "file_count": manifest["file_count"],
            "manifest_sha256": hashlib.sha256((root / "SOURCE_INTEGRITY.json").read_bytes()).hexdigest(),
        })
    except (IntegrityError, OSError, ValueError) as exc:
        checks.append({"name": "source_integrity", "status": "fail", "reason": str(exc)})
        checks.append({"name": "cli_build", "status": "blocked", "reason": "source_integrity_failed"})
        checks.append({"name": "cli_smoke", "status": "blocked", "reason": "source_integrity_failed"})
        checks.append({"name": "registry_smoke", "status": "blocked", "reason": "source_integrity_failed"})
        checks.append({"name": "mcp_smoke", "status": "blocked", "reason": "source_integrity_failed"})
        checks.append({"name": "triage_smoke", "status": "blocked", "reason": "source_integrity_failed"})
        checks.append({"name": "mcp_target_smoke", "status": "blocked", "reason": "source_integrity_failed"})
        checks.append({"name": "artifact_export", "status": "blocked", "reason": "source_integrity_failed"})
        return receipt(checks, version)

    cargo = cargo or shutil.which("cargo")
    rustc = rustc or shutil.which("rustc")
    version, toolchain_problem = check_toolchain(cargo, rustc, root)
    if toolchain_problem:
        for name in ("cli_build", "cli_smoke", "registry_smoke", "mcp_smoke", "triage_smoke", "mcp_target_smoke", "artifact_export"):
            checks.append({"name": name, "status": "blocked", "reason": toolchain_problem})
        return receipt(checks, version)

    build = execute("cli_build", [cargo, *BUILD_ARGS], root, timeout)
    build["command"] = ["cargo", *BUILD_ARGS]  # avoid user-specific toolchain paths in receipts
    checks.append(build)
    if build["status"] != "pass":
        for name in ("cli_smoke", "registry_smoke", "mcp_smoke", "triage_smoke", "mcp_target_smoke", "artifact_export"):
            checks.append({"name": name, "status": "blocked", "reason": "cli_build_failed"})
        return receipt(checks, version)

    try:
        verify_tree(root)
        checks.append({"name": "source_integrity_after_build", "status": "pass"})
    except (IntegrityError, OSError, ValueError) as exc:
        checks.append({"name": "source_integrity_after_build", "status": "fail", "reason": str(exc)})
        checks.append({"name": "cli_smoke", "status": "blocked", "reason": "source_changed_during_build"})
        checks.append({"name": "registry_smoke", "status": "blocked", "reason": "source_changed_during_build"})
        checks.append({"name": "mcp_smoke", "status": "blocked", "reason": "source_changed_during_build"})
        checks.append({"name": "triage_smoke", "status": "blocked", "reason": "source_changed_during_build"})
        checks.append({"name": "mcp_target_smoke", "status": "blocked", "reason": "source_changed_during_build"})
        checks.append({"name": "artifact_export", "status": "blocked", "reason": "source_changed_during_build"})
        return receipt(checks, version)

    smoke = smoke_cli(root, timeout)
    checks.append(smoke)
    if smoke["status"] != "pass":
        checks.append({"name": "registry_smoke", "status": "blocked", "reason": "cli_smoke_failed"})
        checks.append({"name": "mcp_smoke", "status": "blocked", "reason": "cli_smoke_failed"})
        checks.append({"name": "triage_smoke", "status": "blocked", "reason": "cli_smoke_failed"})
        checks.append({"name": "mcp_target_smoke", "status": "blocked", "reason": "cli_smoke_failed"})
        checks.append({"name": "artifact_export", "status": "blocked", "reason": "cli_smoke_failed"})
        return receipt(checks, version)

    registry = smoke_registry(root, timeout)
    checks.append(registry)
    if registry["status"] != "pass":
        checks.append({"name": "mcp_smoke", "status": "blocked", "reason": "registry_smoke_failed"})
        checks.append({"name": "triage_smoke", "status": "blocked", "reason": "registry_smoke_failed"})
        checks.append({"name": "mcp_target_smoke", "status": "blocked", "reason": "registry_smoke_failed"})
        checks.append({"name": "artifact_export", "status": "blocked", "reason": "registry_smoke_failed"})
        return receipt(checks, version)

    mcp = smoke_mcp(root, timeout, registry)
    checks.append(mcp)
    if mcp["status"] != "pass":
        checks.append({"name": "triage_smoke", "status": "blocked", "reason": "mcp_smoke_failed"})
        checks.append({"name": "mcp_target_smoke", "status": "blocked", "reason": "mcp_smoke_failed"})
        checks.append({"name": "artifact_export", "status": "blocked", "reason": "mcp_smoke_failed"})
        return receipt(checks, version)

    triage = smoke_triage(root, timeout)
    checks.append(triage)
    if triage["status"] != "pass":
        checks.append({"name": "mcp_target_smoke", "status": "blocked", "reason": "triage_smoke_failed"})
        checks.append({"name": "artifact_export", "status": "blocked", "reason": "triage_smoke_failed"})
        return receipt(checks, version)

    target = smoke_mcp_target(root, timeout, registry)
    checks.append(target)
    if target["status"] != "pass":
        checks.append({"name": "artifact_export", "status": "blocked", "reason": "mcp_target_smoke_failed"})
        return receipt(checks, version)

    try:
        verify_tree(root)
        checks.append({"name": "source_integrity_after_smoke", "status": "pass"})
    except (IntegrityError, OSError, ValueError) as exc:
        checks.append({"name": "source_integrity_after_smoke", "status": "fail", "reason": str(exc)})
        checks.append({"name": "artifact_export", "status": "blocked", "reason": "source_changed_during_smoke"})
        return receipt(checks, version)
    try:
        checks.append(export_binary(root, output_dir, smoke, registry, mcp, triage, target))
    except (OSError, ValueError) as exc:
        checks.append({"name": "artifact_export", "status": "fail", "reason": str(exc)})
    return receipt(checks, version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)
    root, report_path, output_dir = (args.root.resolve(), args.report.resolve(), args.artifact_dir.resolve())
    if report_path.is_relative_to(root) or output_dir.is_relative_to(root):
        print("Receipts and binary artifacts must be outside the source tree", file=sys.stderr)
        return 1
    if report_path.exists():
        print("Refusing to overwrite an existing validation receipt", file=sys.stderr)
        return 1
    if not report_path.parent.is_dir():
        print("Receipt parent directory does not exist", file=sys.stderr)
        return 1
    try:
        result = run_matrix(root, output_dir, args.timeout)
    except (OSError, ValueError) as exc:
        result = {"schema": SCHEMA, "status": "fail", "error": str(exc)}
    serialized = json.dumps(result, indent=2, sort_keys=True) + "\n"
    with report_path.open("x", encoding="utf-8") as stream:
        stream.write(serialized)
    print(serialized)
    return {"pass": 0, "fail": 1, "blocked": 2}.get(result["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
