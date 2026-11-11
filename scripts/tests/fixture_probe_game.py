#!/usr/bin/env python3
"""Synthetic replay-protocol fixture; not a real game or a gameplay benchmark."""

import argparse
import json
from pathlib import Path
import struct
import sys
import time
import zlib


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def make_png(filename: str, frame: int) -> None:
    width, height = 32, 32
    image = bytearray()
    for row in range(height):
        image.append(0)
        for column in range(width):
            on = 8 <= column < 24 and 8 <= row < 24
            pixel = ((255, 0, 0), (0, 0, 255), (0, 255, 0))[frame - 1] if on and frame else (0, 0, 0)
            image.extend(pixel)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(image))) + chunk(b"IEND", b"")
    Path(filename).write_bytes(png)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("good", "no_move", "no_action", "no_hud", "no_pixels", "no_images", "escape_path", "wrong_nonce", "flood_stdout", "flood_stderr", "hang"), default="good")
    args = parser.parse_args()
    request = json.load(sys.stdin)
    if request["protocol"] != 1:
        raise RuntimeError("protocol mismatch")
    if args.mode == "flood_stdout":
        sys.stdout.write("x" * 1_200_000)
        return
    if args.mode == "flood_stderr":
        sys.stderr.write("x" * 350_000)
        return
    if args.mode == "hang":
        time.sleep(15)
        return
    samples = []
    for index, step in enumerate(request["steps"]):
        filename = f"frame_{index}.png"
        if args.mode != "no_images":
            make_png(filename, 0 if args.mode == "no_pixels" else index)
        samples.append({
            "id": step["id"],
            "x": 0 if index == 0 or args.mode == "no_move" else 12,
            "y": 0,
            "action_count": 0 if index < 2 or args.mode == "no_action" else 1,
            "hud_text": "Score: 1" if index == 3 and args.mode != "no_hud" else "Score: 0",
            "screenshot": "../outside.png" if index == 0 and args.mode == "escape_path" else filename,
        })
    nonce = "forged-challenge" if args.mode == "wrong_nonce" else request.get("nonce")
    json.dump({"protocol": 1, "samples": samples, "nonce": nonce}, sys.stdout)


if __name__ == "__main__":
    main()
