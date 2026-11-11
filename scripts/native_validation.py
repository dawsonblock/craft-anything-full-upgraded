#!/usr/bin/env python3
"""Run a truthful, bounded automated release check for this Rust source fork.

No source is modified. Missing toolchains are BLOCKED, never PASS. Manual
gameplay and OS UI testing are excluded from the automated gate by design.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

from source_bundle_integrity import IntegrityError, verify_tree

SCHEMA_VERSION = 1
MIN_RUST = (1, 97, 0)
MAX_OUTPUT_BYTES = 2_000_000
MAX_EXCERPT = 8000
MAX_BINARY_BYTES = 512_000_000
CLI_HELP_TIMEOUT = 20

RUST_CHECKS = (
    ("fmt", ("fmt", "--all", "--", "--check")),
    ("i18n", ("xtask", "i18n-coverage")),
    ("tools", ("xtask", "tools", "--check")),
    ("schemas", ("xtask", "schemas", "--check")),
    ("layers", ("xtask", "layers")),
    ("locks", ("xtask", "locks")),
    ("engine_tests", ("test", "-p", "craft-anything-engine")),
    ("ui_tests", ("test", "-p", "craft-anything-ui-egui")),
    ("clippy", ("clippy", "--workspace", "--all-targets", "--", "-D", "warnings")),
    ("cli_build", ("build", "-p", "craft-anything-cli")),
)

# A shorter, actionable gate for Deep-mode Ghidra configuration and cache changes.
# It validates the authored Rust tests and desktop crate, but deliberately does
# not claim to run Ghidra or exercise the real interactive desktop.
DEEP_RUST_CHECKS = (
    ("fmt", ("fmt", "--all", "--", "--check")),
    ("deep_settings", ("test", "--locked", "-p", "craft-anything-engine", "deep_settings_")),
    ("deep_lifecycle", ("test", "--locked", "-p", "craft-anything-engine", "deep_lifecycle_")),
    ("target_reopen", ("test", "--locked", "-p", "craft-anything-engine", "target_reopen_")),
    ("ui_typecheck", ("check", "--locked", "-p", "craft-anything-ui-egui")),
)


def stop_process(process: subprocess.Popen) -> None:
    """Cancel the parent and, where available, the session's process group."""
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()


def execute(
    name: str, command: list[str], root: Path, timeout: int,
    *, env: dict[str, str] | None = None, capture_full_output: bool = False,
) -> dict:
    """Drain bounded output; optionally return it internally for JSON parsing.

    Callers that parse full output must remove it before persisting receipts.
    The same 2 MB process-output ceiling applies in either mode.
    """
    output = bytearray()
    excessive = threading.Event()
    start = time.monotonic()
    try:
        process = subprocess.Popen(
            command, cwd=root, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=env,
            start_new_session=(os.name != "nt"),
            creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0),
        )
    except OSError as exc:
        return {
            "name": name, "command": command, "status": "fail",
            "reason": f"Could not start command: {exc}",
            "duration_seconds": round(time.monotonic() - start, 3),
        }

    def drain(pipe) -> None:
        try:
            while chunk := pipe.read1(16 * 1024):
                free = max(0, MAX_OUTPUT_BYTES - len(output))
                output.extend(chunk[:free])
                if len(chunk) > free:
                    excessive.set()
        finally:
            pipe.close()

    if process.stdout is None:
        stop_process(process)
        return {"name": name, "command": command, "status": "fail", "reason": "Output pipe unavailable"}
    reader = threading.Thread(target=drain, args=(process.stdout,), daemon=True)
    reader.start()
    timed_out = False
    stuck = False
    try:
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if excessive.is_set():
                stop_process(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                stop_process(process)
                break
            time.sleep(0.03)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            stuck = True
            stop_process(process)
        reader.join(timeout=3)
        if reader.is_alive():
            stuck = True
            stop_process(process)
    finally:
        if process.poll() is None:
            stop_process(process)
    reason = (
        "process_or_descendant_stuck" if stuck else
        "timed_out" if timed_out else
        "output_limit_exceeded" if excessive.is_set() else
        "nonzero_exit" if process.returncode != 0 else None
    )
    decoded = output.decode("utf-8", errors="replace")
    result = {
        "name": name,
        "command": command,
        "status": "fail" if reason else "pass",
        "reason": reason,
        "returncode": process.returncode,
        "duration_seconds": round(time.monotonic() - start, 3),
        "output_excerpt": decoded[-MAX_EXCERPT:],
    }
    if capture_full_output:
        result["full_output"] = decoded
    return result


def check_toolchain(cargo: str | None, rustc: str | None, root: Path) -> tuple[str | None, str]:
    if not cargo or not rustc:
        return None, "cargo or rustc is not installed"
    result = execute("rustc_version", [rustc, "--version"], root, 10)
    if result["status"] != "pass":
        return None, "rustc --version failed"
    version = result.get("output_excerpt", "").strip()
    match = re.search(r"rustc (\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return None, "rustc version could not be parsed"
    if tuple(map(int, match.groups())) < MIN_RUST:
        return version, "Rust 1.97 or newer is required"
    return version, ""


def cli_binary_path(root: Path) -> Path:
    """Locate Cargo's default debug CLI, respecting the standard target vars."""
    target_arg = os.environ.get("CARGO_TARGET_DIR", "")
    target = Path(target_arg) if target_arg else root / "target"
    if not target.is_absolute():
        target = root / target
    triple = os.environ.get("CARGO_BUILD_TARGET", "")
    if triple:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", triple) or triple in (".", ".."):
            raise ValueError("Unsafe CARGO_BUILD_TARGET value")
        target = target / triple
    name = "craft-anything-cli.exe" if os.name == "nt" else "craft-anything-cli"
    return target / "debug" / name


def hash_binary(binary: Path) -> dict:
    """Hash a bounded binary through an open stream, never loading it all."""
    if binary.is_symlink() or not binary.is_file():
        raise ValueError("Built CLI executable is missing or is a symbolic link")
    digest = hashlib.sha256()
    count = 0
    with binary.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            count += len(chunk)
            if count > MAX_BINARY_BYTES:
                raise ValueError("Built CLI executable exceeds the 512 MB safety limit")
            digest.update(chunk)
    if count == 0:
        raise ValueError("Built CLI executable is empty")
    return {"binary_sha256": digest.hexdigest(), "binary_bytes": count}


def smoke_cli(root: Path, timeout: int) -> dict:
    """Run the built CLI's no-side-effect help and hash it before and after."""
    start = time.monotonic()
    try:
        binary = cli_binary_path(root)
        before = hash_binary(binary)
    except (ValueError, OSError) as exc:
        return {
            "name": "cli_smoke", "command": ["<built-cli>", "--help"],
            "status": "fail", "reason": str(exc),
            "duration_seconds": round(time.monotonic() - start, 3),
        }
    result = execute("cli_smoke", [str(binary), "--help"], root, min(timeout, CLI_HELP_TIMEOUT))
    # Keep local usernames or configured target-directory paths out of receipts.
    result["command"] = ["<built-cli>", "--help"]
    if result["status"] == "pass" and "usage:" not in result.get("output_excerpt", "").lower():
        result["status"] = "fail"
        result["reason"] = "cli_help_missing_usage"
    try:
        after = hash_binary(binary)
    except (ValueError, OSError) as exc:
        result["status"] = "fail"
        result["reason"] = f"cli_binary_unavailable_after_smoke: {exc}"
        return result
    if before != after:
        result["status"] = "fail"
        result["reason"] = "cli_binary_changed_during_smoke"
    result.update(after)
    return result


def run_gate(root: Path, timeout: int, cargo: str | None = None, rustc: str | None = None, *, profile: str = "full") -> dict:
    if not 1 <= timeout <= 3600:
        raise ValueError("Per-command timeout must be between 1 and 3600 seconds")
    if profile not in ("full", "deep"):
        raise ValueError("Unknown validation profile")
    if not root.is_dir():
        raise ValueError("Source root must exist")
    rust_checks = DEEP_RUST_CHECKS if profile == "deep" else RUST_CHECKS
    results = []
    try:
        manifest = verify_tree(root)
        results.append({"name": "source_integrity", "status": "pass", "file_count": manifest["file_count"]})
    except (IntegrityError, OSError, ValueError) as exc:
        results.append({"name": "source_integrity", "status": "fail", "reason": str(exc)})
    version = None
    if results[0]["status"] == "fail":
        # Do not execute any bundled source if its inventory has changed unexpectedly.
        results.append({"name": "python_tests", "status": "blocked", "reason": "source_integrity_failed"})
        for name, argv in rust_checks:
            results.append({"name": name, "status": "blocked", "reason": "source_integrity_failed",
                            "command": ["cargo", *argv]})
        if profile == "full":
            results.append({"name": "cli_smoke", "status": "blocked", "reason": "source_integrity_failed"})
    else:
        results.append(execute(
            "python_tests",
            [sys.executable, "-m", "unittest", "discover", "-s", "scripts/tests", "-p", "test_*.py", "-v"],
            root, timeout,
        ))
        if cargo is None:
            cargo = shutil.which("cargo")
        if rustc is None:
            rustc = shutil.which("rustc")
        version, toolchain_problem = check_toolchain(cargo, rustc, root)
        for name, argv in rust_checks:
            if toolchain_problem:
                results.append({
                    "name": name, "status": "blocked",
                    "reason": toolchain_problem,
                    "command": ["cargo", *argv],
                })
            else:
                results.append(execute(name, [cargo, *argv], root, timeout))
        if profile == "full":
            cli_build = next(item for item in results if item["name"] == "cli_build")
            if cli_build["status"] == "pass":
                results.append(smoke_cli(root, timeout))
            else:
                results.append({
                    "name": "cli_smoke", "status": "blocked",
                    "reason": "cli_build_" + cli_build["status"],
                    "command": ["<built-cli>", "--help"],
                })
    statuses = [item["status"] for item in results]
    status = "fail" if "fail" in statuses else "blocked" if "blocked" in statuses else "pass"
    return {
        "schema": SCHEMA_VERSION,
        "profile": profile,
        "status": status,
        "automated_gate": status,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "rust_version": version,
        "checks": results,
        "manual_validation": "not_run",
        "live_ghidra_validation": "not_run",
        "limitations": [
            "A green automated gate is not a production certification.",
            "Real-game behavior, permission dialogs and native UI must be tested separately.",
            "The Deep profile runs Rust tests and a desktop type check; it does not start Ghidra or verify decompilation.",
            "Windows grandchildren may outlive a cancelled process; use controlled CI or a Job Object.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--report", type=Path, help="JSON receipt path OUTSIDE the source tree")
    parser.add_argument("--timeout", type=int, default=1800, help="per-check timeout in seconds")
    parser.add_argument("--profile", choices=("full", "deep"), default="full", help="full release checks or focused Deep configuration checks")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    destination = args.report.resolve() if args.report else None
    if destination is not None and destination.is_relative_to(root):
        print(json.dumps({
            "schema": SCHEMA_VERSION, "status": "fail",
            "error": "Place the report outside the source tree so its checksum inventory stays valid",
        }, indent=2))
        return 1
    if destination is not None and destination.exists():
        print(json.dumps({
            "schema": SCHEMA_VERSION, "status": "fail",
            "error": "Refusing to overwrite an existing validation receipt",
        }, indent=2))
        return 1
    try:
        report = run_gate(root, args.timeout, profile=args.profile)
    except (ValueError, OSError) as exc:
        report = {"schema": SCHEMA_VERSION, "status": "fail", "error": str(exc)}
    text = json.dumps(report, indent=2, sort_keys=True)
    if destination:
        try:
            # Exclusive create protects receipts even if another process writes the same
            # name between the early existence check and this final write.
            with destination.open("x", encoding="utf-8") as output:
                output.write(text + "\n")
        except OSError as exc:
            print(json.dumps({"schema": SCHEMA_VERSION, "status": "fail", "error": f"Could not create receipt: {exc}"}, indent=2))
            return 1
    print(text)
    return {"pass": 0, "fail": 1, "blocked": 2}.get(report["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
