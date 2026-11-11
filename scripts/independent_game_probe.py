#!/usr/bin/env python3
"""Opt-in, black-box game replay evidence checker (Python standard library only).

The tested executable receives {"protocol":1,"steps":[{"id":...,"keys":[...]}]}
on stdin and emits {"protocol":1,"samples":[{"id":...,"x":0,"y":0,
"action_count":0,"hud_text":"...","screenshot":"relative.png"},...]} on stdout.

This is an INDEPENDENT checker of the reported telemetry and actual PNG pixels;
it cannot prove that an executable's own telemetry is truthful, nor that a
particular game design is correct. Invoke only on programs you trust to run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_IMAGE_PIXELS = 8_000_000
MAX_RAW_BYTES = 40_000_000
MAX_RESPONSE_BYTES = 1_000_000
MAX_STDERR_BYTES = 256_000
MAX_CASE_BYTES = 1_000_000
STEP_IDS = ("initial", "moved", "acted", "hud")


class ProbeError(Exception):
    """The independent checker rejected evidence or could not run the probe."""


def png_rgb(path: Path) -> tuple[int, int, bytes, str]:
    """Read a bounded PNG, verify CRCs and decode every 8-bit RGB/RGBA pixel."""
    if path.stat().st_size > MAX_RAW_BYTES + 1_000_000:
        raise ProbeError("PNG file exceeds the maximum encoded size")
    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ProbeError(f"Not a PNG: {path.name}")
    offset, ihdr, compressed, ended = len(PNG_SIGNATURE), None, bytearray(), False
    while offset < len(data):
        if len(data) - offset < 12:
            raise ProbeError("Truncated PNG chunk")
        count = struct.unpack_from(">I", data, offset)[0]
        end = offset + 12 + count
        if count > MAX_RAW_BYTES or end > len(data):
            raise ProbeError("Oversized or truncated PNG chunk")
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:end - 4]
        expected_crc = struct.unpack_from(">I", data, end - 4)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected_crc:
            raise ProbeError("Bad PNG CRC")
        if kind == b"IHDR":
            if ihdr is not None or len(payload) != 13:
                raise ProbeError("Invalid PNG header")
            width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            if not (0 < width * height <= MAX_IMAGE_PIXELS) or not (0 < width <= 8192 and 0 < height <= 8192):
                raise ProbeError("PNG image size outside bounds")
            if depth != 8 or colour not in (2, 6) or (compression, filtering, interlace) != (0, 0, 0):
                raise ProbeError("Only non-interlaced 8-bit RGB/RGBA PNG is supported")
            ihdr = (width, height, 3 if colour == 2 else 4)
        elif kind == b"IDAT":
            compressed.extend(payload)
            if len(compressed) > MAX_RAW_BYTES:
                raise ProbeError("PNG compressed data too large")
        elif kind == b"IEND":
            ended = True
            break
        offset = end
    if ihdr is None or not ended:
        raise ProbeError("PNG is missing its header or ending")
    width, height, channels = ihdr
    stride = width * channels
    expected = (stride + 1) * height
    if expected > MAX_RAW_BYTES:
        raise ProbeError("PNG decoded data too large")
    decompressor = zlib.decompressobj()
    try:
        raw = decompressor.decompress(bytes(compressed), expected + 1)
        if len(raw) > expected:
            raise ProbeError("PNG decoded stream exceeds the expected size")
        raw += decompressor.flush(expected + 1 - len(raw))
    except zlib.error as exc:
        raise ProbeError(f"Invalid PNG compression: {exc}") from exc
    if len(raw) != expected or not decompressor.eof or decompressor.unused_data:
        raise ProbeError("PNG decoded size or compression stream is invalid")
    pixels = bytearray()
    prior = bytearray(stride)
    for row in range(height):
        where = row * (stride + 1)
        filter_type = raw[where]
        values = bytearray(raw[where + 1:where + 1 + stride])
        if filter_type > 4:
            raise ProbeError("Unknown PNG row filter")
        for index in range(stride):
            left = values[index - channels] if index >= channels else 0
            above = prior[index]
            upper_left = prior[index - channels] if index >= channels else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            else:
                p = left + above - upper_left
                distances = (abs(p - left), abs(p - above), abs(p - upper_left))
                predictor = (left, above, upper_left)[distances.index(min(distances))]
            values[index] = (values[index] + predictor) & 255
        if channels == 3:
            pixels.extend(values)
        else:
            for index in range(0, len(values), 4):
                pixels.extend(values[index:index + 3])
        prior = values
    return width, height, bytes(pixels), hashlib.sha256(data).hexdigest()


def screenshot_in_directory(folder: Path, label: object) -> Path:
    if not isinstance(label, str) or not label or Path(label).is_absolute():
        raise ProbeError("Screenshot must be a nonempty relative path")
    base = folder.resolve()
    resolved = (base / label).resolve()
    if not resolved.is_relative_to(base) or not resolved.is_file():
        raise ProbeError("Screenshot is missing or escapes its output folder")
    return resolved


def validate(case: dict, response: dict, output_folder: Path, expected_nonce: str | None = None) -> dict:
    """Check independently computed movement, input action, HUD and image differences."""
    if case.get("protocol") != 1 or response.get("protocol") != 1:
        raise ProbeError("Protocol must be version 1")
    if expected_nonce is not None and response.get("nonce") != expected_nonce:
        raise ProbeError("Game probe did not echo this run's fresh challenge nonce")
    steps, samples = case.get("steps"), response.get("samples")
    if not isinstance(steps, list) or not isinstance(samples, list) or len(steps) != 4 or len(samples) != 4:
        raise ProbeError("Exactly four request steps and four evidence samples are required")
    if tuple(step.get("id") for step in steps if isinstance(step, dict)) != STEP_IDS:
        raise ProbeError("Request steps must be ordered: initial, moved, acted, hud")
    if tuple(sample.get("id") for sample in samples if isinstance(sample, dict)) != STEP_IDS:
        raise ProbeError("Evidence samples are missing or out of order")
    for item in steps:
        if not isinstance(item.get("keys"), list) or not all(isinstance(k, str) for k in item["keys"]):
            raise ProbeError("Every step requires a list of keys")
    def number(sample: dict, field: str) -> float:
        value = sample.get(field)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ProbeError(f"{field} must be a finite number")
        return float(value)
    initial, moved, acted, hud = samples
    x0, y0 = number(initial, "x"), number(initial, "y")
    x1, y1 = number(moved, "x"), number(moved, "y")
    move = math.hypot(x1 - x0, y1 - y0)
    action_delta = number(acted, "action_count") - number(moved, "action_count")
    hud_text = hud.get("hud_text")
    if not isinstance(hud_text, str):
        raise ProbeError("HUD text is missing")
    requirements = case.get("requirements")
    if not isinstance(requirements, dict):
        raise ProbeError("Case requirements must be an object")
    min_move = float(requirements.get("min_move", 1.0))
    min_action = float(requirements.get("min_action_delta", 1.0))
    min_changed = float(requirements.get("min_pixel_changed_fraction", 0.01))
    hud_contains = requirements.get("hud_contains", "")
    if not all(math.isfinite(n) and n >= 0 for n in (min_move, min_action, min_changed)) or min_changed > 1:
        raise ProbeError("Invalid numeric requirements")
    if not isinstance(hud_contains, str) or not hud_contains:
        raise ProbeError("Case must require a nonempty HUD text")
    screenshot_paths = [
        screenshot_in_directory(output_folder, sample.get("screenshot"))
        for sample in samples
    ]
    initial_png, moved_png, acted_png, hud_png = [png_rgb(path) for path in screenshot_paths]
    if not all((p[0], p[1]) == (initial_png[0], initial_png[1]) for p in (moved_png, acted_png, hud_png)):
        raise ProbeError("Screenshot dimensions changed during replay")
    def changed(a: bytes, b: bytes) -> float:
        return sum(x != y for x, y in zip(a, b)) / max(1, len(a))
    pixel_motion = changed(initial_png[2], moved_png[2])
    pixel_action = changed(moved_png[2], acted_png[2])
    pixel_hud = changed(acted_png[2], hud_png[2])
    checks = {
        "position_moved": move >= min_move,
        "action_counter_advanced": action_delta >= min_action,
        "hud_updated": hud_contains in hud_text,
        "movement_visible": pixel_motion >= min_changed,
        "action_visible": pixel_action >= min_changed,
        "hud_visible": pixel_hud >= min_changed,
    }
    return {
        "status": "pass" if all(checks.values()) else "fail",
        "protocol": 1,
        "checks": checks,
        "metrics": {
            "movement": round(move, 5),
            "action_delta": action_delta,
            "movement_pixel_changed_fraction": round(pixel_motion, 6),
            "action_pixel_changed_fraction": round(pixel_action, 6),
            "hud_pixel_changed_fraction": round(pixel_hud, 6),
        },
        "screenshot_sha256": {
            sample["id"]: decoded[3]
            for sample, decoded in zip(samples, (initial_png, moved_png, acted_png, hud_png))
        },
        "limitations": "Telemetry is game-reported; PNG pixels are independently decoded. This is not proof of correct gameplay or freedom from spoofing.",
    }


def case_digest(case: dict) -> str:
    """Stable digest of a parsed replay case, independent of JSON indentation."""
    return hashlib.sha256(
        json.dumps(case, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _stop_probe_process(process: subprocess.Popen) -> None:
    """Stop the target's process group where supported, including on timeout."""
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()


def bounded_run(
    executable: list[str], capture: Path, request: dict, timeout: int,
) -> tuple[int, str, str]:
    """Run a trusted adapter with bounded, continuously drained stdout/stderr.

    The parent writes request bytes to a private temporary file, not to a pipe
    that can block forever when an adapter refuses to consume stdin.
    """
    exceeded = threading.Event()
    stdout_bytes = bytearray()
    stderr_bytes = bytearray()

    def drain(pipe, destination: bytearray, limit: int) -> None:
        try:
            while chunk := pipe.read1(8192):
                free = max(0, limit - len(destination))
                destination.extend(chunk[:free])
                if len(chunk) > free:
                    exceeded.set()
        finally:
            pipe.close()

    with tempfile.TemporaryFile(mode="w+b") as input_file:
        input_file.write(json.dumps(request).encode("utf-8"))
        input_file.seek(0)
        process = subprocess.Popen(
            executable, cwd=capture, stdin=input_file,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=(os.name != "nt"),
        )
        # Both pipes are configured by Popen; never assume they are non-None.
        if process.stdout is None or process.stderr is None:
            _stop_probe_process(process)
            raise ProbeError("Game probe did not create both output pipes")
        readers = [
            threading.Thread(target=drain, args=(process.stdout, stdout_bytes, MAX_RESPONSE_BYTES), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, stderr_bytes, MAX_STDERR_BYTES), daemon=True),
        ]
        for reader in readers:
            reader.start()
        timed_out = False
        try:
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if exceeded.is_set():
                    _stop_probe_process(process)
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    _stop_probe_process(process)
                    break
                time.sleep(0.02)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired as exc:
                _stop_probe_process(process)
                raise ProbeError("Game probe could not be stopped after cancellation") from exc
            for reader in readers:
                reader.join(timeout=2)
            if any(reader.is_alive() for reader in readers):
                _stop_probe_process(process)
                raise ProbeError("A child process still holds the game probe output streams open")
            if timed_out:
                raise ProbeError(f"Game probe timed out after {timeout} seconds")
            if exceeded.is_set():
                raise ProbeError("Game probe exceeded the stdout or stderr byte limit")
            return (
                process.returncode,
                stdout_bytes.decode("utf-8", errors="replace"),
                stderr_bytes.decode("utf-8", errors="replace"),
            )
        finally:
            if process.poll() is None:
                _stop_probe_process(process)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass


def run_probe(case: dict, executable: list[str], output_folder: Path, timeout: int) -> dict:
    if not executable:
        raise ProbeError("Supply a trusted executable after --")
    if not 1 <= timeout <= 120:
        raise ProbeError("Timeout must be between 1 and 120 seconds")
    nonce = secrets.token_hex(16)
    # Each invocation gets its own exclusively created image directory. A previous run's
    # screenshots cannot accidentally count as evidence for this run.
    capture = Path(tempfile.mkdtemp(prefix="replay-", dir=output_folder))
    try:
        returncode, stdout, stderr = bounded_run(
            executable, capture,
            {"protocol": 1, "steps": case["steps"], "nonce": nonce}, timeout,
        )
    except OSError as exc:
        raise ProbeError(f"Could not run the game probe: {exc}") from exc
    if returncode != 0:
        raise ProbeError(f"Game probe exited with code {returncode}: {stderr[-500:]}")
    try:
        response = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"Game probe returned invalid JSON: {exc}") from exc
    if not isinstance(response, dict):
        raise ProbeError("Game probe must return an object")
    report = validate(case, response, capture, expected_nonce=nonce)
    report["capture_folder"] = str(capture)
    report["case_sha256"] = case_digest(case)
    # Persist the exact declared telemetry alongside the independently checked
    # screenshot hashes so it can be revalidated *without rerunning* a target.
    report["challenge_nonce"] = nonce
    report["response"] = response
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True, help="JSON replay case")
    parser.add_argument("--output-folder", type=Path, required=True, help="existing screenshot output directory")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--report", type=Path, help="optional location for a JSON evidence report")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- followed by trusted game executable and arguments")
    args = parser.parse_args(argv)
    try:
        if args.case.stat().st_size > MAX_CASE_BYTES:
            raise ProbeError("Replay case JSON is too large")
        case = json.loads(args.case.read_text(encoding="utf-8"))
        if not isinstance(case, dict):
            raise ProbeError("Case must be a JSON object")
        folder = args.output_folder.resolve(strict=True)
        if not folder.is_dir():
            raise ProbeError("Output folder must be a directory")
        report = run_probe(case, args.command[1:] if args.command[:1] == ["--"] else args.command, folder, args.timeout)
    except (ProbeError, OSError, ValueError, KeyError) as exc:
        report = {"status": "error", "error": str(exc)}
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
