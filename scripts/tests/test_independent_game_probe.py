"""Meaningful independent probe regressions: good evidence passes, broken evidence fails."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "independent_game_probe.py"
FIXTURE = Path(__file__).resolve().parent / "fixture_probe_game.py"

CASE = {
    "protocol": 1,
    "steps": [
        {"id": "initial", "keys": []},
        {"id": "moved", "keys": ["Right"]},
        {"id": "acted", "keys": ["Space"]},
        {"id": "hud", "keys": []},
    ],
    "requirements": {
        "min_move": 5,
        "min_action_delta": 1,
        "min_pixel_changed_fraction": 0.05,
        "hud_contains": "Score: 1",
    },
}


class IndependentGameProbeTests(unittest.TestCase):
    def check_mode(self, mode: str, case: dict | None = None, preseed: bool = False, timeout: int = 5):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            if preseed:
                subprocess.run(
                    [sys.executable, str(FIXTURE), "--mode", "good"],
                    input=json.dumps({"protocol": 1, "steps": CASE["steps"]}),
                    cwd=root, capture_output=True, text=True, timeout=5, check=True,
                )
            test_case = root / "case.json"
            test_case.write_text(json.dumps(case or CASE), encoding="utf-8")
            report_file = root / "report.json"
            completed = subprocess.run(
                [sys.executable, str(SCRIPT),
                 "--case", str(test_case), "--output-folder", str(root),
                 "--timeout", str(timeout), "--report", str(report_file), "--",
                 sys.executable, str(FIXTURE), "--mode", mode],
                capture_output=True, text=True, timeout=10, check=False,
            )
            self.assertTrue(report_file.is_file())
            report = json.loads(report_file.read_text(encoding="utf-8"))
            return completed.returncode, report

    def test_good_fixture_passes_all_checks(self):
        code, report = self.check_mode("good")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "pass")
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(set(report["screenshot_sha256"]), {"initial", "moved", "acted", "hud"})
        self.assertEqual(len(report["case_sha256"]), 64)
        self.assertTrue(Path(report["capture_folder"]).name.startswith("replay-"))

    def test_regressions_are_rejected(self):
        faults = {
            "no_move": "position_moved",
            "no_action": "action_counter_advanced",
            "no_hud": "hud_updated",
            "no_pixels": "movement_visible",
        }
        for mode, failed_check in faults.items():
            with self.subTest(mode=mode):
                code, report = self.check_mode(mode)
                self.assertNotEqual(code, 0)
                self.assertEqual(report["status"], "fail")
                self.assertFalse(report["checks"][failed_check])

    def test_rejects_path_escape(self):
        code, report = self.check_mode("escape_path")
        self.assertNotEqual(code, 0)
        self.assertEqual(report["status"], "error")
        self.assertIn("escapes", report["error"])

    def test_rejects_replayed_or_forged_nonce(self):
        code, report = self.check_mode("wrong_nonce")
        self.assertNotEqual(code, 0)
        self.assertEqual(report["status"], "error")
        self.assertIn("challenge nonce", report["error"])

    def test_bounds_stdout_and_stderr_while_the_target_runs(self):
        for mode in ("flood_stdout", "flood_stderr"):
            with self.subTest(mode=mode):
                code, report = self.check_mode(mode)
                self.assertNotEqual(code, 0)
                self.assertEqual(report["status"], "error")
                self.assertIn("byte limit", report["error"])

    def test_kills_a_hung_adapter(self):
        code, report = self.check_mode("hang", timeout=1)
        self.assertNotEqual(code, 0)
        self.assertEqual(report["status"], "error")
        self.assertIn("timed out", report["error"])

    def test_saved_report_can_be_revalidated_and_tampering_is_detected(self):
        sys.path.insert(0, str(SCRIPT.parent))
        from independent_game_probe import ProbeError, run_probe
        from verify_probe_report import verify
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = run_probe(CASE, [sys.executable, str(FIXTURE)], root, 5)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(verify(CASE, report)["status"], "verified")
            altered_case = json.loads(json.dumps(CASE))
            altered_case["requirements"]["min_move"] = 100
            with self.assertRaisesRegex(ProbeError, "case changed"):
                verify(altered_case, report)
            screenshot = Path(report["capture_folder"]) / report["response"]["samples"][1]["screenshot"]
            altered = bytearray(screenshot.read_bytes())
            altered[-5] ^= 1
            screenshot.write_bytes(altered)
            with self.assertRaises(ProbeError):
                verify(CASE, report)

    def test_replay_and_verifier_cli_work_together(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case_path = root / "case.json"
            report_path = root / "report.json"
            case_path.write_text(json.dumps(CASE), encoding="utf-8")
            initial = subprocess.run(
                [sys.executable, str(SCRIPT), "--case", str(case_path),
                 "--output-folder", str(root), "--report", str(report_path),
                 "--", sys.executable, str(FIXTURE)],
                capture_output=True, text=True, timeout=10, check=False,
            )
            self.assertEqual(initial.returncode, 0, initial.stdout)
            verified = subprocess.run(
                [sys.executable, str(SCRIPT.parent / "verify_probe_report.py"),
                 "--case", str(case_path), "--report", str(report_path)],
                capture_output=True, text=True, timeout=10, check=False,
            )
            self.assertEqual(verified.returncode, 0, verified.stdout)
            self.assertEqual(json.loads(verified.stdout)["status"], "verified")

    def test_stale_screenshots_outside_the_fresh_run_cannot_pass(self):
        code, report = self.check_mode("no_images", preseed=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(report["status"], "error")
        self.assertIn("missing", report["error"])

    def test_rejects_missing_hud_requirement(self):
        bad_case = json.loads(json.dumps(CASE))
        bad_case["requirements"].pop("hud_contains")
        code, report = self.check_mode("good", bad_case)
        self.assertNotEqual(code, 0)
        self.assertEqual(report["status"], "error")

    def test_finite_bounds_and_crc(self):
        sys.path.insert(0, str(SCRIPT.parent))
        from independent_game_probe import ProbeError, png_rgb
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(
                [sys.executable, str(FIXTURE)],
                input=json.dumps({"protocol": 1, "steps": CASE["steps"]}),
                cwd=root, capture_output=True, text=True, check=True,
            )
            original = root / "frame_0.png"
            self.assertEqual(png_rgb(original)[:2], (32, 32))
            altered = bytearray(original.read_bytes())
            altered[-5] ^= 0x01
            original.write_bytes(altered)
            with self.assertRaises(ProbeError):
                png_rgb(original)


if __name__ == "__main__":
    unittest.main()
