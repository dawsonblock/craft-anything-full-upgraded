"""Tests for cross-view native metadata parsing and privacy isolation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import registry_smoke as registry


def entries(count: int = 200) -> list[dict]:
    return [
        {
            "command": f"tool-{index}", "tool": f"tool_{index}",
            "toolset": f"set_{index % 5}", "title": f"Tool {index}",
            "summary": "Read evidence", "needs_target": index % 2 == 0,
        }
        for index in range(count)
    ]


def capabilities_for(commands: list[dict]) -> dict:
    grouped = {}
    for entry in commands:
        grouped.setdefault(entry["toolset"], []).append({
            "tool": entry["tool"], "available": True,
            "needs_target": entry["needs_target"], "reason": None,
        })
    return {
        "version": "0.4.0", "analysis_mode": "clean-room",
        "host": "synthetic-test-platform", "tools": len(commands),
        "toolsets": [
            {"id": name, "title": f"Toolset {name}", "tools": tools}
            for name, tools in grouped.items()
        ],
        "engines": [{"id": "synthetic", "name": "Synthetic Engine", "needed_for": "Synthetic test", "found": False,
                     "installable_here": False, "path": None}],
    }


class RegistrySmokeTests(unittest.TestCase):
    def test_valid_registry_has_stable_digest(self) -> None:
        raw = json.dumps(entries(), sort_keys=False)
        result = registry.validate_registry(raw)
        self.assertEqual(result["command_count"], 200)
        self.assertEqual(result["toolset_count"], 5)
        self.assertEqual(len(result["registry_sha256"]), 64)
        self.assertEqual(result["registry_sha256"], registry.validate_registry(json.dumps(entries(), sort_keys=True))["registry_sha256"])

    def test_rejects_non_json_and_non_array(self) -> None:
        for raw, reason in (("not json", "not_json"), ('{"name":"bad"}', "not_array")):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, reason):
                registry.validate_registry(raw)

    def test_rejects_too_few_commands(self) -> None:
        with self.assertRaisesRegex(ValueError, "too_few_commands"):
            registry.validate_registry(json.dumps(entries(199)))

    def test_rejects_duplicate_tool_id_and_duplicate_command(self) -> None:
        for field in ("tool", "command"):
            rows = entries()
            rows[3][field] = rows[0][field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "duplicate_identifier"):
                registry.validate_registry(json.dumps(rows))

    def test_rejects_invalid_metadata(self) -> None:
        rows = entries()
        rows[12]["title"] = ""
        with self.assertRaisesRegex(ValueError, "invalid_fields"):
            registry.validate_registry(json.dumps(rows))

    def test_rejects_non_boolean_target_flag(self) -> None:
        rows = entries()
        rows[12]["needs_target"] = "false"
        with self.assertRaisesRegex(ValueError, "invalid_target_flag"):
            registry.validate_registry(json.dumps(rows))

    def test_rejects_missing_toolset_diversity(self) -> None:
        rows = entries()
        for row in rows:
            row["toolset"] = "one"
        with self.assertRaisesRegex(ValueError, "too_few_toolsets"):
            registry.validate_registry(json.dumps(rows))

    def test_valid_capabilities_cross_check_hashes_and_counts(self) -> None:
        commands = entries()
        raw = json.dumps(capabilities_for(commands), indent=2)
        result = registry.validate_capabilities(raw, json.dumps(commands))
        self.assertEqual(result["capability_toolset_count"], 5)
        self.assertEqual(result["capability_engine_count"], 1)
        self.assertEqual(result["cli_version"], "0.4.0")
        self.assertEqual(len(result["capabilities_sha256"]), 64)
        self.assertEqual(result["capabilities_sha256"], registry.validate_capabilities(
            json.dumps(capabilities_for(commands), sort_keys=True), json.dumps(commands)
        )["capabilities_sha256"])

    def test_capabilities_rejects_invalid_shape_and_counts(self) -> None:
        commands = entries()
        valid = capabilities_for(commands)
        cases = [
            ("not json", "not_json"),
            (json.dumps([]), "root_is_not_object"),
            (json.dumps({**valid, "tools": True}), "tool_count_invalid"),
            (json.dumps({**valid, "tools": len(commands) - 1}), "tool_count_mismatch"),
            (json.dumps({**valid, "version": ""}), "version_invalid"),
            (json.dumps({**valid, "analysis_mode": "unknown"}), "mode_invalid"),
            (json.dumps({**valid, "host": ""}), "host_invalid"),
            (json.dumps({**valid, "engines": {}}), "collections_invalid"),
        ]
        for raw, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                registry.validate_capabilities(raw, json.dumps(commands))

    def test_capabilities_rejects_missing_duplicate_and_extra_tools(self) -> None:
        commands = entries()
        for mutation in ("missing", "duplicate", "extra"):
            caps = capabilities_for(commands)
            group = caps["toolsets"][0]["tools"]
            if mutation == "missing":
                group.pop()
            elif mutation == "duplicate":
                group.append(dict(group[0]))
            else:
                group.append({"tool": "unknown", "available": True, "needs_target": False})
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, "tool_missing_extra_or_duplicate"):
                registry.validate_capabilities(json.dumps(caps), json.dumps(commands))

    def test_capabilities_rejects_misplaced_tool_and_wrong_target_flag(self) -> None:
        commands = entries()
        for mutation, reason in (("toolset", "toolset_assignment_mismatch"),
                                 ("target", "target_requirement_mismatch")):
            caps = capabilities_for(commands)
            group = caps["toolsets"][0]["tools"]
            if mutation == "toolset":
                group.append(caps["toolsets"][1]["tools"].pop())
            else:
                group[0]["needs_target"] = not group[0]["needs_target"]
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, reason):
                registry.validate_capabilities(json.dumps(caps), json.dumps(commands))

    def test_capabilities_rejects_bad_availability_and_engines(self) -> None:
        commands = entries()
        for mutation, reason in (("available", "availability_invalid"),
                                 ("reason", "availability_reason_invalid"),
                                 ("engine_flags", "engine_0_flags_invalid"),
                                 ("engine_path", "engine_0_path_inconsistent"),
                                 ("engine_purpose", "engine_0_purpose_invalid"),
                                 ("duplicate_engine", "engine_1_duplicate_or_invalid"),
                                 ("duplicate_toolset", "toolset_5_tools_invalid")):
            caps = capabilities_for(commands)
            first = caps["toolsets"][0]["tools"][0]
            if mutation == "available":
                first["available"] = "yes"
            elif mutation == "reason":
                first["reason"] = 123
            elif mutation == "engine_flags":
                caps["engines"][0]["found"] = "yes"
            elif mutation == "engine_path":
                caps["engines"][0]["found"] = True
            elif mutation == "engine_purpose":
                caps["engines"][0]["needed_for"] = 123
            elif mutation == "duplicate_engine":
                caps["engines"].append(dict(caps["engines"][0]))
            else:
                caps["toolsets"].append({"id": caps["toolsets"][0]["id"], "title": "Duplicate", "tools": []})
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, reason):
                registry.validate_capabilities(json.dumps(caps), json.dumps(commands))

    def test_runner_uses_isolated_environment_and_redacts_output(self) -> None:
        binary = Path(sys.executable).resolve()
        observed: list[tuple[dict, list[str]]] = []

        def fake_execute(name, command, root, timeout, *, env, capture_full_output):
            self.assertTrue(capture_full_output)
            self.assertTrue(Path(env["CRAFT_ANYTHING_CONFIG_DIR"]).is_dir())
            self.assertEqual(env["CRAFT_ANYTHING_LOG"], "off")
            self.assertEqual(env["HOME"], env["APPDATA"])
            self.assertEqual(env["APPDATA"], env["LOCALAPPDATA"])
            self.assertEqual(env["TEMP"], env["HOME"])
            self.assertEqual(env["TMPDIR"], env["HOME"])
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertIn(command[1], ("commands", "capabilities"))
            observed.append((env, command))
            return {
                "name": name, "command": command, "status": "pass", "returncode": 0,
                "full_output": json.dumps(entries()) if command[1] == "commands"
                else json.dumps(capabilities_for(entries())),
                "output_excerpt": "private-registry-output",
            }

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test", "GITHUB_TOKEN": "synthetic-test-token"}), \
                patch.object(registry, "cli_binary_path", return_value=binary), \
                patch.object(registry, "execute", side_effect=fake_execute):
            report = registry.smoke_registry(ROOT, 100)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["command_count"], 200)
        self.assertEqual(report["commands"], [["<built-cli>", "commands"], ["<built-cli>", "capabilities"]])
        self.assertEqual(report["capability_toolset_count"], 5)
        self.assertEqual(report["capability_engine_count"], 1)
        self.assertNotIn("full_output", report)
        self.assertNotIn("output_excerpt", report)
        self.assertNotIn("command", report)
        self.assertEqual(len(observed), 2)
        self.assertIs(observed[0][0], observed[1][0])
        self.assertEqual(observed[0][1][1], "commands")
        self.assertEqual(observed[1][1][1], "capabilities")

    def test_runner_rejects_failed_capabilities_and_hides_output(self) -> None:
        binary = Path(sys.executable).resolve()

        def fake_execute(name, command, root, timeout, *, env, capture_full_output):
            if command[1] == "commands":
                return {"name": name, "status": "pass", "full_output": json.dumps(entries())}
            return {"name": name, "status": "fail", "reason": "timed_out",
                    "full_output": "secret paths", "output_excerpt": "secret paths"}

        with patch.object(registry, "cli_binary_path", return_value=binary), \
                patch.object(registry, "execute", side_effect=fake_execute):
            report = registry.smoke_registry(ROOT, 5)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["reason"], "capabilities_timed_out")
        self.assertNotIn("secret paths", json.dumps(report))

    def test_runner_rejects_non_json_capabilities(self) -> None:
        binary = Path(sys.executable).resolve()

        def fake_execute(name, command, root, timeout, *, env, capture_full_output):
            return {"name": name, "status": "pass", "full_output":
                    json.dumps(entries()) if command[1] == "commands" else "not json"}

        with patch.object(registry, "cli_binary_path", return_value=binary), \
                patch.object(registry, "execute", side_effect=fake_execute):
            report = registry.smoke_registry(ROOT, 5)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["reason"], "capabilities_output_is_not_json")

    def test_runner_rejects_malformed_native_output(self) -> None:
        binary = Path(sys.executable).resolve()
        with patch.object(registry, "cli_binary_path", return_value=binary), \
                patch.object(registry, "execute", return_value={
                    "name": "registry_smoke", "status": "pass", "full_output": "not json",
                    "output_excerpt": "should not persist",
                }):
            report = registry.smoke_registry(ROOT, 5)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["reason"], "registry_output_is_not_json")
        self.assertNotIn("full_output", report)
        self.assertNotIn("output_excerpt", report)

    def test_runner_detects_executable_mutation(self) -> None:
        binary = Path(sys.executable).resolve()

        def fake_execute(name, command, root, timeout, *, env, capture_full_output):
            return {"name": name, "status": "pass", "full_output":
                    json.dumps(entries()) if command[1] == "commands"
                    else json.dumps(capabilities_for(entries()))}

        with patch.object(registry, "cli_binary_path", return_value=binary), \
                patch.object(registry, "execute", side_effect=fake_execute), \
                patch.object(registry, "hash_binary", side_effect=[
                    {"binary_sha256": "a" * 64, "binary_bytes": 100},
                    {"binary_sha256": "b" * 64, "binary_bytes": 100},
                ]):
            report = registry.smoke_registry(ROOT, 5)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["reason"], "cli_binary_changed_during_registry_smoke")

    def test_runner_preserves_process_failure(self) -> None:
        binary = Path(sys.executable).resolve()
        with patch.object(registry, "cli_binary_path", return_value=binary), \
                patch.object(registry, "execute", return_value={
                    "name": "registry_smoke", "status": "fail", "reason": "timed_out",
                    "full_output": "partial", "output_excerpt": "partial",
                }):
            report = registry.smoke_registry(ROOT, 5)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["reason"], "timed_out")
        self.assertNotIn("full_output", report)


if __name__ == "__main__":
    unittest.main()
