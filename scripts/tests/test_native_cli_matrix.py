"""Synthetic native CLI matrix tests: no Cargo or desktop environment required."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import native_cli_matrix as matrix


def passed_build(name: str, command: list[str], root: Path, timeout: int) -> dict:
    return {"name": name, "status": "pass", "returncode": 0, "command": command}


def passed_registry(binary_evidence: dict | None = None) -> dict:
    return {
        "name": "registry_smoke", "status": "pass",
        "registry_sha256": "a" * 64, "capabilities_sha256": "b" * 64,
        "registry_mcp_contract_sha256": "c" * 64,
        "command_count": 314, "toolset_count": 12,
        "capability_toolset_count": 12, "capability_engine_count": 7,
        "cli_version": "0.4.0",
        **(binary_evidence or {}),
    }


def passed_mcp(binary_evidence: dict | None = None) -> dict:
    return {
        "name": "mcp_smoke", "status": "pass", "mcp_contract_sha256": "c" * 64,
        "mcp_tool_count": 314, "mcp_protocol_version": "2025-06-18",
        "mcp_server_version": "0.4.0", "mcp_readonly_tool": "core_session_show",
        "mcp_readonly_session_empty": True, "mcp_readonly_descriptor_verified": True,
        "mcp_unknown_tool_refused": True, **(binary_evidence or {}),
    }


def passed_triage(binary_evidence: dict | None = None) -> dict:
    return {
        "name": "triage_smoke", "status": "pass", "triage_evidence_verified": True,
        "triage_kind": "wasm", "triage_fixture_sha256": matrix.WASM_SHA256,
        "triage_tool": matrix.EXPECTED_TOOL, **(binary_evidence or {}),
    }


def passed_target(binary_evidence: dict | None = None) -> dict:
    return {
        "name": "mcp_target_smoke", "status": "pass", "mcp_target_wasm_parsed": True,
        "mcp_target_state_closed": True, "mcp_target_evidence_preserved": True,
        "mcp_target_fixture_sha256": matrix.WASM_SHA256,
        "mcp_target_operations": len(matrix.OPERATIONS), **(binary_evidence or {}),
    }


class NativeCliMatrixTests(unittest.TestCase):
    def test_toolchain_missing_blocks_build_and_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "verify_tree", return_value={"file_count": 1}), \
                patch.object(matrix, "check_toolchain", return_value=(None, "no Rust")), \
                patch.object(matrix, "execute") as execute:
            result = matrix.run_matrix(ROOT, Path(temp) / "artifact", 5, cargo="cargo", rustc="rustc")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual([x["status"] for x in result["checks"]], [
            "pass", "blocked", "blocked", "blocked", "blocked", "blocked", "blocked", "blocked"])
        execute.assert_not_called()

    def test_integrity_failure_blocks_all_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "verify_tree", side_effect=matrix.IntegrityError("changed file")), \
                patch.object(matrix, "check_toolchain") as check_toolchain, \
                patch.object(matrix, "execute") as execute:
            result = matrix.run_matrix(ROOT, Path(temp) / "artifact", 5)
        self.assertEqual(result["status"], "fail")
        check_toolchain.assert_not_called()
        execute.assert_not_called()
        self.assertEqual(result["checks"][0]["name"], "source_integrity")
        self.assertIn("changed file", result["checks"][0]["reason"])

    def test_failed_compile_blocks_launch_and_export(self) -> None:
        def broken_build(name, command, root, timeout):
            return {"name": name, "status": "fail", "reason": "nonzero_exit", "command": command}

        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "verify_tree", return_value={"file_count": 1}), \
                patch.object(matrix, "check_toolchain", return_value=("rustc 1.97.0", "")), \
                patch.object(matrix, "execute", side_effect=broken_build), \
                patch.object(matrix, "smoke_cli") as smoke:
            result = matrix.run_matrix(ROOT, Path(temp) / "artifact", 5, cargo="cargo", rustc="rustc")
            self.assertFalse((Path(temp) / "artifact").exists())
        self.assertEqual(result["status"], "fail")
        smoke.assert_not_called()
        self.assertEqual(result["checks"][-1]["status"], "blocked")

    def test_success_produces_matching_executable_and_provenance(self) -> None:
        binary = Path(sys.executable).resolve()
        binary_evidence = matrix.hash_binary(binary)
        smoke = {"name": "cli_smoke", "status": "pass", **binary_evidence}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "verify_tree", return_value={"file_count": 1}) as integrity, \
                patch.object(matrix, "check_toolchain", return_value=("rustc 1.97.0", "")), \
                patch.object(matrix, "execute", side_effect=passed_build) as execute, \
                patch.object(matrix, "smoke_cli", return_value=smoke), \
                patch.object(matrix, "smoke_registry", return_value=passed_registry(binary_evidence)), \
                patch.object(matrix, "smoke_mcp", return_value=passed_mcp(binary_evidence)), \
                patch.object(matrix, "smoke_triage", return_value=passed_triage(binary_evidence)), \
                patch.object(matrix, "smoke_mcp_target", return_value=passed_target(binary_evidence)), \
                patch.object(matrix, "cli_binary_path", return_value=binary):
            artifact_dir = Path(temp) / "cli"
            result = matrix.run_matrix(ROOT, artifact_dir, 6, cargo="cargo", rustc="rustc")
            payload = json.loads((artifact_dir / "CLI_PROVENANCE.json").read_text(encoding="utf-8"))
            copied_hash = matrix.hash_binary(artifact_dir / binary.name)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(payload["binary_sha256"], binary_evidence["binary_sha256"])
        self.assertEqual(payload["source_manifest_sha256"], result["checks"][0]["manifest_sha256"])
        self.assertEqual(payload["registry_sha256"], "a" * 64)
        self.assertEqual(payload["capabilities_sha256"], "b" * 64)
        self.assertEqual(payload["registry_mcp_contract_sha256"], "c" * 64)
        self.assertEqual(payload["command_count"], 314)
        self.assertEqual(payload["capability_toolset_count"], 12)
        self.assertEqual(payload["capability_engine_count"], 7)
        self.assertEqual(payload["cli_version"], "0.4.0")
        self.assertEqual(payload["mcp_tool_count"], 314)
        self.assertEqual(payload["mcp_protocol_version"], "2025-06-18")
        self.assertEqual(payload["mcp_readonly_tool"], "core_session_show")
        self.assertTrue(payload["mcp_readonly_session_empty"])
        self.assertTrue(payload["mcp_readonly_descriptor_verified"])
        self.assertTrue(payload["mcp_unknown_tool_refused"])
        self.assertTrue(payload["triage_evidence_verified"])
        self.assertEqual(payload["triage_fixture_sha256"], matrix.WASM_SHA256)
        self.assertEqual(payload["triage_tool"], matrix.EXPECTED_TOOL)
        self.assertTrue(payload["mcp_target_wasm_parsed"])
        self.assertTrue(payload["mcp_target_state_closed"])
        self.assertTrue(payload["mcp_target_evidence_preserved"])
        self.assertEqual(payload["mcp_target_operations"], len(matrix.OPERATIONS))
        self.assertEqual(copied_hash, binary_evidence)
        self.assertIn("UNSIGNED", payload["label"])
        self.assertEqual(integrity.call_count, 3)
        self.assertEqual(execute.call_args.args[1], ["cargo", *matrix.BUILD_ARGS])

    def test_post_build_source_change_blocks_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "verify_tree", side_effect=[
                    {"file_count": 1}, matrix.IntegrityError("build modified source"),
                ]), \
                patch.object(matrix, "check_toolchain", return_value=("rustc 1.97.0", "")), \
                patch.object(matrix, "execute", side_effect=passed_build), \
                patch.object(matrix, "smoke_cli") as smoke:
            result = matrix.run_matrix(ROOT, Path(temp) / "cli", 5, cargo="cargo", rustc="rustc")
        self.assertEqual(result["status"], "fail")
        self.assertIn("build modified source", result["checks"][-7]["reason"])
        smoke.assert_not_called()

    def test_failed_smoke_never_exports_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "verify_tree", return_value={"file_count": 1}), \
                patch.object(matrix, "check_toolchain", return_value=("rustc 1.97.0", "")), \
                patch.object(matrix, "execute", side_effect=passed_build), \
                patch.object(matrix, "smoke_cli", return_value={"name": "cli_smoke", "status": "fail"}):
            artifact_dir = Path(temp) / "cli"
            result = matrix.run_matrix(ROOT, artifact_dir, 5, cargo="cargo", rustc="rustc")
            self.assertFalse(artifact_dir.exists())
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["checks"][-1]["status"], "blocked")

    def test_post_smoke_source_change_prevents_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "verify_tree", side_effect=[
                    {"file_count": 1}, {"file_count": 1}, matrix.IntegrityError("changed"),
                ]), \
                patch.object(matrix, "check_toolchain", return_value=("rustc 1.97.0", "")), \
                patch.object(matrix, "execute", side_effect=passed_build), \
                patch.object(matrix, "smoke_cli", return_value={"name": "cli_smoke", "status": "pass"}), \
                patch.object(matrix, "smoke_registry", return_value=passed_registry()), \
                patch.object(matrix, "smoke_mcp", return_value=passed_mcp()), \
                patch.object(matrix, "smoke_triage", return_value=passed_triage()), \
                patch.object(matrix, "smoke_mcp_target", return_value=passed_target()), \
                patch.object(matrix, "export_binary") as export:
            result = matrix.run_matrix(ROOT, Path(temp) / "cli", 5, cargo="cargo", rustc="rustc")
        self.assertEqual(result["status"], "fail")
        export.assert_not_called()
        self.assertEqual(result["checks"][-2]["name"], "source_integrity_after_smoke")
        self.assertEqual(result["checks"][-2]["status"], "fail")
        self.assertEqual(result["checks"][-1]["status"], "blocked")

    def test_export_rejects_hash_that_differs_from_smoke(self) -> None:
        binary = Path(sys.executable).resolve()
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "cli_binary_path", return_value=binary):
            target = Path(temp) / "cli"
            with self.assertRaisesRegex(ValueError, "no longer matches"):
                matrix.export_binary(
                    ROOT, target, {"status": "pass", "binary_sha256": "0" * 64, "binary_bytes": 1},
                    passed_registry(matrix.hash_binary(binary)),
                    passed_mcp(matrix.hash_binary(binary)),
                    passed_triage(matrix.hash_binary(binary)),
                    passed_target(matrix.hash_binary(binary)),
                )
            self.assertFalse(target.exists())

    def test_export_does_not_overwrite_a_previous_directory(self) -> None:
        binary = Path(sys.executable).resolve()
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "cli_binary_path", return_value=binary):
            target = Path(temp) / "cli"
            target.mkdir()
            marker = target / "old-data.txt"
            marker.write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                matrix.export_binary(
                    ROOT, target, {"status": "pass", **matrix.hash_binary(binary)},
                    passed_registry(matrix.hash_binary(binary)),
                    passed_mcp(matrix.hash_binary(binary)),
                    passed_triage(matrix.hash_binary(binary)),
                    passed_target(matrix.hash_binary(binary)),
                )
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_registry_smoke_failure_blocks_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "verify_tree", return_value={"file_count": 1}), \
                patch.object(matrix, "check_toolchain", return_value=("rustc 1.97.0", "")), \
                patch.object(matrix, "execute", side_effect=passed_build), \
                patch.object(matrix, "smoke_cli", return_value={"name": "cli_smoke", "status": "pass"}), \
                patch.object(matrix, "smoke_registry", return_value={
                    "name": "registry_smoke", "status": "fail", "reason": "invalid_registry_json"
                }), \
                patch.object(matrix, "export_binary") as export:
            target = Path(temp) / "cli"
            result = matrix.run_matrix(ROOT, target, 5, cargo="cargo", rustc="rustc")
            self.assertFalse(target.exists())
        export.assert_not_called()
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["checks"][-1]["reason"], "registry_smoke_failed")

    def test_artifact_requires_real_registry_evidence(self) -> None:
        binary = Path(sys.executable).resolve()
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "cli_binary_path", return_value=binary):
            target = Path(temp) / "cli"
            with self.assertRaisesRegex(ValueError, "All native smoke"):
                matrix.export_binary(ROOT, target, {"status": "pass", **matrix.hash_binary(binary)}, {}, {}, {}, {})
            self.assertFalse(target.exists())

    def test_export_rejects_missing_or_invalid_capabilities_digest(self) -> None:
        binary = Path(sys.executable).resolve()
        evidence = matrix.hash_binary(binary)
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "cli_binary_path", return_value=binary):
            for value in (None, "", "z" * 64, "a" * 32):
                registry = passed_registry(evidence)
                registry["capabilities_sha256"] = value
                target = Path(temp) / "cli"
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "capabilities_sha256"):
                    matrix.export_binary(ROOT, target, {"status": "pass", **evidence}, registry,
                                         passed_mcp(evidence), passed_triage(evidence), passed_target(evidence))
                self.assertFalse(target.exists())

    def test_export_rejects_inconsistent_capability_toolset_count(self) -> None:
        binary = Path(sys.executable).resolve()
        evidence = matrix.hash_binary(binary)
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "cli_binary_path", return_value=binary):
            registry = passed_registry(evidence)
            registry["capability_toolset_count"] = 11
            target = Path(temp) / "cli"
            with self.assertRaisesRegex(ValueError, "fewer toolsets"):
                matrix.export_binary(ROOT, target, {"status": "pass", **evidence}, registry,
                                     passed_mcp(evidence), passed_triage(evidence), passed_target(evidence))

    def test_mcp_failure_blocks_artifact_even_when_metadata_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "verify_tree", return_value={"file_count": 1}), \
                patch.object(matrix, "check_toolchain", return_value=("rustc 1.97.0", "")), \
                patch.object(matrix, "execute", side_effect=passed_build), \
                patch.object(matrix, "smoke_cli", return_value={"name": "cli_smoke", "status": "pass"}), \
                patch.object(matrix, "smoke_registry", return_value=passed_registry()), \
                patch.object(matrix, "smoke_mcp", return_value={"name": "mcp_smoke", "status": "fail", "reason": "mcp_tool_count_mismatch"}), \
                patch.object(matrix, "export_binary") as export:
            target = Path(temp) / "cli"
            result = matrix.run_matrix(ROOT, target, 5, cargo="cargo", rustc="rustc")
            self.assertFalse(target.exists())
        export.assert_not_called()
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["checks"][-4]["name"], "mcp_smoke")
        self.assertEqual(result["checks"][-4]["status"], "fail")
        self.assertEqual(result["checks"][-3]["name"], "triage_smoke")
        self.assertEqual(result["checks"][-3]["status"], "blocked")
        self.assertEqual(result["checks"][-2]["name"], "mcp_target_smoke")
        self.assertEqual(result["checks"][-2]["status"], "blocked")
        self.assertEqual(result["checks"][-1]["reason"], "mcp_smoke_failed")

    def test_triage_failure_blocks_export_even_when_mcp_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "verify_tree", return_value={"file_count": 1}), \
                patch.object(matrix, "check_toolchain", return_value=("rustc 1.97.0", "")), \
                patch.object(matrix, "execute", side_effect=passed_build), \
                patch.object(matrix, "smoke_cli", return_value={"name": "cli_smoke", "status": "pass"}), \
                patch.object(matrix, "smoke_registry", return_value=passed_registry()), \
                patch.object(matrix, "smoke_mcp", return_value=passed_mcp()), \
                patch.object(matrix, "smoke_triage", return_value={"name": "triage_smoke", "status": "fail", "reason": "triage_subject_kind_mismatch"}), \
                patch.object(matrix, "export_binary") as export:
            artifact_dir = Path(temp) / "artifact"
            result = matrix.run_matrix(ROOT, artifact_dir, 5, cargo="cargo", rustc="rustc")
            self.assertFalse(artifact_dir.exists())
        export.assert_not_called()
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["checks"][-3]["name"], "triage_smoke")
        self.assertEqual(result["checks"][-2]["name"], "mcp_target_smoke")
        self.assertEqual(result["checks"][-1]["reason"], "triage_smoke_failed")

    def test_mcp_target_failure_prevents_unsigned_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "verify_tree", return_value={"file_count": 1}), \
                patch.object(matrix, "check_toolchain", return_value=("rustc 1.97.0", "")), \
                patch.object(matrix, "execute", side_effect=passed_build), \
                patch.object(matrix, "smoke_cli", return_value={"name": "cli_smoke", "status": "pass"}), \
                patch.object(matrix, "smoke_registry", return_value=passed_registry()), \
                patch.object(matrix, "smoke_mcp", return_value=passed_mcp()), \
                patch.object(matrix, "smoke_triage", return_value=passed_triage()), \
                patch.object(matrix, "smoke_mcp_target", return_value={
                    "name": "mcp_target_smoke", "status": "fail", "reason": "mcp_target_cleanup_not_verified"}), \
                patch.object(matrix, "export_binary") as export:
            artifact_dir = Path(temp) / "artifact"
            result = matrix.run_matrix(ROOT, artifact_dir, 5, cargo="cargo", rustc="rustc")
            self.assertFalse(artifact_dir.exists())
        export.assert_not_called()
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["checks"][-2]["name"], "mcp_target_smoke")
        self.assertEqual(result["checks"][-1]["reason"], "mcp_target_smoke_failed")

    def test_export_rejects_missing_stateful_target_receipt(self) -> None:
        binary = Path(sys.executable).resolve()
        evidence = matrix.hash_binary(binary)
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "cli_binary_path", return_value=binary):
            output = Path(temp) / "cli"
            for mutation in ("missing", "not_closed", "not_parsed", "wrong_count", "wrong_fixture", "wrong_hash"):
                target = passed_target(evidence)
                if mutation == "missing":
                    target.pop("mcp_target_evidence_preserved")
                elif mutation == "not_closed":
                    target["mcp_target_state_closed"] = False
                elif mutation == "not_parsed":
                    target["mcp_target_wasm_parsed"] = False
                elif mutation == "wrong_count":
                    target["mcp_target_operations"] = -1
                elif mutation == "wrong_fixture":
                    target["mcp_target_fixture_sha256"] = "f" * 64
                else:
                    target["binary_sha256"] = "0" * 64
                with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, "MCP target"):
                    matrix.export_binary(ROOT, output, {"status": "pass", **evidence},
                                         passed_registry(evidence), passed_mcp(evidence),
                                         passed_triage(evidence), target)
                self.assertFalse(output.exists())

    def test_export_rejects_missing_or_forged_triage_evidence(self) -> None:
        binary = Path(sys.executable).resolve()
        evidence = matrix.hash_binary(binary)
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "cli_binary_path", return_value=binary):
            target = Path(temp) / "artifact"
            for mutation in ("missing", "unchecked", "wrong_fixture", "wrong_tool", "wrong_hash"):
                triage = passed_triage(evidence)
                if mutation == "missing":
                    triage.pop("triage_kind")
                elif mutation == "unchecked":
                    triage["triage_evidence_verified"] = False
                elif mutation == "wrong_fixture":
                    triage["triage_fixture_sha256"] = "0" * 64
                elif mutation == "wrong_tool":
                    triage["triage_tool"] = "core_session_show"
                else:
                    triage["binary_sha256"] = "0" * 64
                with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, "triage"):
                    matrix.export_binary(ROOT, target, {"status": "pass", **evidence},
                                         passed_registry(evidence), passed_mcp(evidence), triage, passed_target(evidence))
                self.assertFalse(target.exists())

    def test_export_requires_real_mcp_evidence_and_matching_hash(self) -> None:
        binary = Path(sys.executable).resolve()
        evidence = matrix.hash_binary(binary)
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "cli_binary_path", return_value=binary):
            target = Path(temp) / "cli"
            with self.assertRaisesRegex(ValueError, "All native smoke"):
                matrix.export_binary(ROOT, target, {"status": "pass", **evidence},
                                     passed_registry(evidence), {}, passed_triage(evidence), passed_target(evidence))
            forged = passed_mcp(evidence)
            forged["binary_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "MCP smoke receipt"):
                matrix.export_binary(ROOT, target, {"status": "pass", **evidence},
                                     passed_registry(evidence), forged, passed_triage(evidence), passed_target(evidence))
            self.assertFalse(target.exists())

    def test_export_requires_mcp_registry_contract_consistency(self) -> None:
        binary = Path(sys.executable).resolve()
        evidence = matrix.hash_binary(binary)
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "cli_binary_path", return_value=binary):
            target = Path(temp) / "cli"
            forged = passed_mcp(evidence)
            forged["mcp_contract_sha256"] = "e" * 64
            with self.assertRaisesRegex(ValueError, "MCP tools differ"):
                matrix.export_binary(ROOT, target, {"status": "pass", **evidence},
                                     passed_registry(evidence), forged, passed_triage(evidence), passed_target(evidence))
            self.assertFalse(target.exists())

    def test_export_requires_readonly_mcp_tool_receipt(self) -> None:
        binary = Path(sys.executable).resolve()
        evidence = matrix.hash_binary(binary)
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "cli_binary_path", return_value=binary):
            target = Path(temp) / "cli"
            for mutation in ("wrong_name", "not_empty", "missing"):
                forged = passed_mcp(evidence)
                if mutation == "wrong_name":
                    forged["mcp_readonly_tool"] = "core_target_open"
                elif mutation == "not_empty":
                    forged["mcp_readonly_session_empty"] = False
                else:
                    forged.pop("mcp_readonly_tool")
                with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, "read-only session query"):
                    matrix.export_binary(ROOT, target, {"status": "pass", **evidence},
                                         passed_registry(evidence), forged, passed_triage(evidence), passed_target(evidence))
                self.assertFalse(target.exists())

    def test_export_requires_readonly_metadata_and_unknown_tool_refusal(self) -> None:
        binary = Path(sys.executable).resolve()
        evidence = matrix.hash_binary(binary)
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(matrix, "cli_binary_path", return_value=binary):
            target = Path(temp) / "cli"
            for field, reason in (
                ("mcp_readonly_descriptor_verified", "read-only descriptor"),
                ("mcp_unknown_tool_refused", "unknown tool"),
            ):
                for mutation in (None, False, "true"):
                    forged = passed_mcp(evidence)
                    if mutation is None:
                        forged.pop(field)
                    else:
                        forged[field] = mutation
                    with self.subTest(field=field, mutation=mutation), self.assertRaisesRegex(ValueError, reason):
                        matrix.export_binary(ROOT, target, {"status": "pass", **evidence},
                                             passed_registry(evidence), forged, passed_triage(evidence), passed_target(evidence))
                    self.assertFalse(target.exists())

    def test_timeout_and_inside_source_artifacts_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "timeout"):
                matrix.run_matrix(ROOT, Path(temp) / "cli", 0)
            with self.assertRaisesRegex(ValueError, "outside"):
                matrix.run_matrix(ROOT, ROOT / "artifacts", 5)

    def test_report_inside_source_fails_without_overwriting_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with redirect_stdout(StringIO()):
                rc = matrix.main([
                    "--root", str(ROOT), "--report", str(ROOT / "invalid_receipt.json"),
                    "--artifact-dir", str(Path(temp) / "cli"),
                ])
        self.assertEqual(rc, 1)
        self.assertFalse((ROOT / "invalid_receipt.json").exists())

    def test_existing_report_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "receipt.json"
            report.write_text("original", encoding="utf-8")
            rc = matrix.main([
                "--root", str(ROOT), "--report", str(report),
                "--artifact-dir", str(Path(temp) / "cli"),
            ])
            self.assertEqual(rc, 1)
            self.assertEqual(report.read_text(encoding="utf-8"), "original")

    def test_workflow_is_manual_and_not_a_signed_release(self) -> None:
        workflow = (ROOT / ".github/workflows/fork-native-cli-matrix.yml").read_text(encoding="utf-8")
        for requirement in (
            "workflow_dispatch", "ubuntu-latest", "macos-latest", "windows-latest",
            "scripts/native_cli_matrix.py", "actions/upload-artifact@v4", "--artifact-dir",
        ):
            self.assertIn(requirement, workflow)
        for forbidden in ("secrets.", "release: publish", "cargo install --git"):
            self.assertNotIn(forbidden, workflow)


if __name__ == "__main__":
    unittest.main()
