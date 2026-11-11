"""Contract tests for the fail-closed native release gate."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import native_validation as gate


class NativeValidationTests(unittest.TestCase):
    def test_successful_command_is_pass(self) -> None:
        result = gate.execute("ok", [sys.executable, "-c", "print('good')"], ROOT, 3)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["returncode"], 0)
        self.assertIn("good", result["output_excerpt"])
        self.assertNotIn("full_output", result)

    def test_full_capture_is_opt_in_and_bounded(self) -> None:
        result = gate.execute(
            "registry", [sys.executable, "-c", "print('a' * 18000)"],
            ROOT, 3, capture_full_output=True,
        )
        self.assertEqual(result["status"], "pass")
        self.assertGreater(len(result["full_output"]), gate.MAX_EXCERPT)
        self.assertLessEqual(len(result["output_excerpt"]), gate.MAX_EXCERPT)

    def test_subprocess_environment_can_be_isolated(self) -> None:
        env = dict(os.environ)
        env["CRAFT_ANYTHING_CONFIG_DIR"] = "/tmp/synthetic-isolated-config"
        result = gate.execute(
            "env", [sys.executable, "-c", "import os; print(os.environ.get('CRAFT_ANYTHING_CONFIG_DIR'))"],
            ROOT, 3, env=env,
        )
        self.assertEqual(result["status"], "pass")
        self.assertIn("synthetic-isolated-config", result["output_excerpt"])

    def test_nonzero_command_is_fail(self) -> None:
        result = gate.execute("bad", [sys.executable, "-c", "raise SystemExit(9)"], ROOT, 3)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["reason"], "nonzero_exit")
        self.assertEqual(result["returncode"], 9)

    def test_missing_executable_is_fail(self) -> None:
        result = gate.execute("missing", ["this-executable-does-not-exist-349816"], ROOT, 3)
        self.assertEqual(result["status"], "fail")
        self.assertIn("Could not start command", result["reason"])

    def test_output_limit_is_fail(self) -> None:
        result = gate.execute(
            "verbose", [sys.executable, "-c", "import sys; sys.stdout.write('X'*2200000)"],
            ROOT, 3,
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["reason"], "output_limit_exceeded")
        self.assertLessEqual(len(result["output_excerpt"]), gate.MAX_EXCERPT)

    def test_timeout_is_fail(self) -> None:
        result = gate.execute(
            "slow", [sys.executable, "-c", "import time; time.sleep(10)"], ROOT, 1,
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["reason"], "timed_out")

    def test_missing_toolchain_is_reported(self) -> None:
        version, reason = gate.check_toolchain(None, None, ROOT)
        self.assertIsNone(version)
        self.assertIn("not installed", reason)

    def test_old_rust_toolchain_is_reported(self) -> None:
        with patch.object(gate, "execute", return_value={
            "status": "pass", "output_excerpt": "rustc 1.96.0 (fake)",
        }):
            version, reason = gate.check_toolchain("cargo", "rustc", ROOT)
        self.assertIn("1.96.0", version)
        self.assertIn("1.97", reason)

    def test_missing_rust_does_not_mark_gate_green(self) -> None:
        fake_py = {"name": "python_tests", "status": "pass", "returncode": 0}
        with patch.object(gate, "verify_tree", return_value={"file_count": 42}), \
                patch.object(gate, "execute", return_value=fake_py), \
                patch.object(gate, "check_toolchain", return_value=(None, "missing")), \
                patch.object(gate.shutil, "which", return_value=None):
            report = gate.run_gate(ROOT, 3)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["checks"][0]["status"], "pass")
        self.assertEqual(len(report["checks"]), 3 + len(gate.RUST_CHECKS))
        self.assertTrue(all(item["status"] == "blocked" for item in report["checks"][2:]))

    def test_deep_profile_missing_rust_is_blocked_not_passed(self) -> None:
        fake_py = {"name": "python_tests", "status": "pass", "returncode": 0}
        with patch.object(gate, "verify_tree", return_value={"file_count": 42}), \
                patch.object(gate, "execute", return_value=fake_py), \
                patch.object(gate, "check_toolchain", return_value=(None, "missing")):
            report = gate.run_gate(ROOT, 3, profile="deep")
        self.assertEqual(report["profile"], "deep")
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(len(report["checks"]), 2 + len(gate.DEEP_RUST_CHECKS))
        self.assertTrue(all(item["status"] == "blocked" for item in report["checks"][2:]))
        self.assertNotIn("cli_smoke", [item["name"] for item in report["checks"]])

    def test_deep_profile_pass_does_not_claim_live_ghidra_or_desktop_execution(self) -> None:
        def fake_execute(name, command, root, timeout):
            return {"name": name, "status": "pass", "returncode": 0}

        with patch.object(gate, "verify_tree", return_value={"file_count": 42}), \
                patch.object(gate, "execute", side_effect=fake_execute), \
                patch.object(gate, "check_toolchain", return_value=("rustc 1.97.0", "")), \
                patch.object(gate, "smoke_cli") as smoke_mock:
            report = gate.run_gate(ROOT, 3, cargo="cargo", rustc="rustc", profile="deep")
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["profile"], "deep")
        self.assertEqual(report["live_ghidra_validation"], "not_run")
        self.assertEqual(report["manual_validation"], "not_run")
        self.assertEqual(len(report["checks"]), 2 + len(gate.DEEP_RUST_CHECKS))
        smoke_mock.assert_not_called()

    def test_deep_profile_native_failure_is_not_masked(self) -> None:
        def fake_execute(name, command, root, timeout):
            return {"name": name, "status": "fail" if name == "deep_lifecycle" else "pass"}

        with patch.object(gate, "verify_tree", return_value={"file_count": 42}), \
                patch.object(gate, "execute", side_effect=fake_execute), \
                patch.object(gate, "check_toolchain", return_value=("rustc 1.97.0", "")):
            report = gate.run_gate(ROOT, 3, cargo="cargo", rustc="rustc", profile="deep")
        self.assertEqual(report["status"], "fail")
        self.assertEqual(next(item for item in report["checks"] if item["name"] == "deep_lifecycle")["status"], "fail")

    def test_deep_profile_rejects_source_integrity_failure_before_running_code(self) -> None:
        with patch.object(gate, "verify_tree", side_effect=gate.IntegrityError("tampered")), \
                patch.object(gate, "execute") as execute_mock:
            report = gate.run_gate(ROOT, 3, profile="deep")
        self.assertEqual(report["status"], "fail")
        self.assertEqual(len(report["checks"]), 2 + len(gate.DEEP_RUST_CHECKS))
        execute_mock.assert_not_called()

    def test_prior_failure_is_not_masked_by_blocked(self) -> None:
        with patch.object(gate, "verify_tree", side_effect=gate.IntegrityError("tampered")), \
                patch.object(gate, "execute") as execute_mock, \
                patch.object(gate, "check_toolchain") as toolchain_mock:
            report = gate.run_gate(ROOT, 3)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["checks"][0]["status"], "fail")
        self.assertEqual(report["checks"][1]["status"], "blocked")
        execute_mock.assert_not_called()
        toolchain_mock.assert_not_called()

    def test_cargo_target_directory_and_triple_are_applied(self) -> None:
        with patch.dict(os.environ, {"CARGO_TARGET_DIR": "custom", "CARGO_BUILD_TARGET": "x86_64-unknown-linux-gnu"}):
            result = gate.cli_binary_path(ROOT)
        self.assertEqual(result.parent.name, "debug")
        self.assertEqual(result.parent.parent.name, "x86_64-unknown-linux-gnu")
        self.assertEqual(result.parent.parent.parent, ROOT / "custom")

    def test_unsafe_target_triple_is_rejected(self) -> None:
        with patch.dict(os.environ, {"CARGO_BUILD_TARGET": "../outside"}):
            with self.assertRaisesRegex(ValueError, "Unsafe CARGO_BUILD_TARGET"):
                gate.cli_binary_path(ROOT)

    def test_cli_smoke_launches_a_real_help_executable(self) -> None:
        # The installed Python executable is a controlled --help stand-in.
        # This tests subprocess, output and SHA-256 behavior without Cargo.
        with patch.object(gate, "cli_binary_path", return_value=Path(sys.executable).resolve()):
            result = gate.smoke_cli(ROOT, 5)
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["command"], ["<built-cli>", "--help"])
        self.assertEqual(len(result["binary_sha256"]), 64)
        self.assertGreater(result["binary_bytes"], 0)

    def test_cli_smoke_missing_executable_is_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "nonexistent-cli"
            with patch.object(gate, "cli_binary_path", return_value=missing):
                result = gate.smoke_cli(ROOT, 5)
        self.assertEqual(result["status"], "fail")
        self.assertIn("missing", result["reason"])

    def test_cli_smoke_rejects_empty_binary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / "empty"
            empty.write_bytes(b"")
            with patch.object(gate, "cli_binary_path", return_value=empty):
                result = gate.smoke_cli(ROOT, 5)
        self.assertEqual(result["status"], "fail")
        self.assertIn("empty", result["reason"])

    def test_cli_smoke_requires_help_usage_line(self) -> None:
        with patch.object(gate, "cli_binary_path", return_value=Path(sys.executable).resolve()), \
                patch.object(gate, "execute", return_value={
                    "name": "cli_smoke", "status": "pass", "output_excerpt": "unexpected output",
                }):
            result = gate.smoke_cli(ROOT, 5)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["reason"], "cli_help_missing_usage")

    def test_cli_smoke_detects_binary_changes(self) -> None:
        with patch.object(gate, "cli_binary_path", return_value=Path(sys.executable).resolve()), \
                patch.object(gate, "execute", return_value={
                    "name": "cli_smoke", "status": "pass", "output_excerpt": "Usage: good",
                }), \
                patch.object(gate, "hash_binary", side_effect=[
                    {"binary_sha256": "a" * 64, "binary_bytes": 10},
                    {"binary_sha256": "b" * 64, "binary_bytes": 10},
                ]):
            result = gate.smoke_cli(ROOT, 5)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["reason"], "cli_binary_changed_during_smoke")

    def test_cli_build_failure_blocks_smoke(self) -> None:
        def fake_execute(name, command, root, timeout):
            status = "fail" if name == "cli_build" else "pass"
            return {"name": name, "status": status, "reason": None}

        with patch.object(gate, "verify_tree", return_value={"file_count": 42}), \
                patch.object(gate, "execute", side_effect=fake_execute), \
                patch.object(gate, "check_toolchain", return_value=("rustc 1.97.0", "")), \
                patch.object(gate, "smoke_cli") as smoke_mock:
            report = gate.run_gate(ROOT, 3, cargo="cargo", rustc="rustc")
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["checks"][-1]["status"], "blocked")
        self.assertEqual(report["checks"][-1]["reason"], "cli_build_fail")
        smoke_mock.assert_not_called()

    def test_green_native_gate_requires_cli_smoke(self) -> None:
        def fake_execute(name, command, root, timeout):
            return {"name": name, "status": "pass", "returncode": 0}

        with patch.object(gate, "verify_tree", return_value={"file_count": 42}), \
                patch.object(gate, "execute", side_effect=fake_execute), \
                patch.object(gate, "check_toolchain", return_value=("rustc 1.97.0", "")), \
                patch.object(gate, "smoke_cli", return_value={"name": "cli_smoke", "status": "fail"}):
            report = gate.run_gate(ROOT, 3, cargo="cargo", rustc="rustc")
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["checks"][-1]["status"], "fail")

    def test_timeout_bounds_are_enforced(self) -> None:
        for invalid in (0, 3601, -1):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                gate.run_gate(ROOT, invalid)

    def test_unknown_profile_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown validation profile"):
            gate.run_gate(ROOT, 3, profile="not-a-real-gate")

    def test_report_inside_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            other_root = Path(directory)
            with redirect_stdout(StringIO()):
                result = gate.main(["--root", str(other_root), "--report", str(other_root / "report.json")])
            self.assertEqual(result, 1)
            self.assertFalse((other_root / "report.json").exists())

    def test_existing_receipt_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "existing.json"
            report.write_text("user-owned original receipt\n", encoding="utf-8")
            with patch.object(gate, "run_gate") as runner, redirect_stdout(StringIO()):
                result = gate.main(["--root", str(ROOT), "--profile", "deep", "--report", str(report)])
            self.assertEqual(result, 1)
            self.assertEqual(report.read_text(encoding="utf-8"), "user-owned original receipt\n")
            runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
