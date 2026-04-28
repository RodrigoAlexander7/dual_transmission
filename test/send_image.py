#!/usr/bin/env python3
"""
XBee Pro S1 — Image sender (API mode AP=1).

Runs on: Raspberry Pi Zero 2W
Serial:  /dev/serial0  @ 9600

Reads a JPEG image, splits it into 96-byte chunks, and sends each one
inside a TX Request 64-bit frame (0x00).  Waits for TX Status (0x89)
after every frame before proceeding to the next chunk.

Usage:
    python3 send_image.py
    python3 send_image.py --port /dev/serial0 --image foto.jpg --image-id 1
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import serial

from xbee_frame import (
    CHUNK_DATA_SIZE,
    build_chunk_payload,
    build_tx64,
    parse_tx_status,
    read_frame,
)

MAX_RETRIES = 5          # retries per chunk at application level
TX_STATUS_TIMEOUT_S = 2.0


def main() -> None:
    parser = argparse.ArgumentParser(description="XBee Pro S1 — Image Sender")
    parser.add_argument("--port",     default="/dev/serial0",     help="Serial port (default: /dev/serial0)")
    parser.add_argument("--baud",     type=int, default=9600,     help="Baudrate (default: 9600)")
    parser.add_argument("--dest",     default="0013A200406EFB43", help="Destination 64-bit address hex")
    parser.add_argument("--image",    default="foto.jpg",         help="Image file to send")
    parser.add_argument("--image-id", type=int, default=1,        help="Image ID 1–65535")
    args = parser.parse_args()

    # ── Locate image ────────────────────────────────────────────────
    image_path = Path(args.image)
    if not image_path.exists():
        image_path = Path(__file__).parent / args.image
    if not image_path.exists():
        print(f"ERROR: image not found: {args.image}")
        sys.exit(1)

    image_data = image_path.read_bytes()
    dest_addr  = bytes.fromhex(args.dest)

    total_chunks = math.ceil(len(image_data) / CHUNK_DATA_SIZE)
    if total_chunks > 255:
        print(f"ERROR: image too large ({len(image_data)}B = {total_chunks} chunks, max 255)")
        sys.exit(1)

    # ── Summary ─────────────────────────────────────────────────────
    print("=" * 60)
    print("  XBee Pro S1 — Image Sender")
    print("=" * 60)
    print(f"  Image   : {image_path.name} ({len(image_data)} bytes)")
    print(f"  Chunks  : {total_chunks}  ({CHUNK_DATA_SIZE}B data + 4B header = 100B payload)")
    print(f"  Image ID: {args.image_id}")
    print(f"  Dest    : {args.dest}")
    print(f"  Port    : {args.port} @ {args.baud}")
    print("=" * 60)
    print()

    # ── Open serial ─────────────────────────────────────────────────
    with serial.Serial(args.port, args.baud, timeout=1.0) as ser:
        ser.reset_input_buffer()
        time.sleep(0.1)

        frame_id = 1
        sent_ok  = 0
        sent_fail = 0
        t_start = time.perf_counter()

        try:
            for idx in range(total_chunks):
                # Extract chunk data
                start = idx * CHUNK_DATA_SIZE
                end   = start + CHUNK_DATA_SIZE
                chunk = image_data[start:end]

                app_payload = build_chunk_payload(
                    image_id=args.image_id,
                    chunk_idx=idx,
                    total_chunks=total_chunks,
                    data=chunk,
                )

                # Send with retries
                success = False
                for attempt in range(1, MAX_RETRIES + 1):
                    api_frame = build_tx64(frame_id, dest_addr, app_payload)
                    ser.reset_input_buffer()
                    ser.write(api_frame)
                    ser.flush()

                    # Wait for TX Status
                    status = _wait_tx_status(ser, frame_id)

                    if status is not None and status == 0x00:
                        success = True
                        break

                    tag = f"0x{status:02X}" if status is not None else "timeout"
                    print(f"  [RETRY] chunk {idx}/{total_chunks}  status={tag}  attempt={attempt}/{MAX_RETRIES}")

                # Advance frame_id for next transmission
                frame_id = (frame_id % 255) + 1

                if success:
                    sent_ok += 1
                else:
                    sent_fail += 1
                    print(f"  [FAIL]  chunk {idx}/{total_chunks}  after {MAX_RETRIES} attempts")

                # Progress log
                if (idx + 1) % 10 == 0 or idx == total_chunks - 1:
                    elapsed = time.perf_counter() - t_start
                    pct  = 100.0 * (idx + 1) / total_chunks
                    rate = (idx + 1) / elapsed if elapsed > 0 else 0
                    print(
                        f"  [{pct:5.1f}%]  chunk {idx+1}/{total_chunks}  "
                        f"ok={sent_ok}  fail={sent_fail}  "
                        f"elapsed={elapsed:.1f}s  rate={rate:.1f} chunks/s"
                    )

        except KeyboardInterrupt:
            print("\n  Interrupted by user.")

        elapsed = time.perf_counter() - t_start
        print()
        print("=" * 60)
        print(f"  DONE  ok={sent_ok}  fail={sent_fail}  total={total_chunks}  elapsed={elapsed:.1f}s")
        print("=" * 60)


def _wait_tx_status(ser, expected_frame_id: int) -> int | None:
    """Read frames until we get TX Status matching expected_frame_id, or timeout."""
    deadline = time.monotonic() + TX_STATUS_TIMEOUT_S
    while time.monotonic() < deadline:
        remaining = max(deadline - time.monotonic(), 0.05)
        frame = read_frame(ser, timeout_s=remaining)
        if frame is None:
            return None
        result = parse_tx_status(frame)
        if result is not None and result.frame_id == expected_frame_id:
            return result.status
        # Ignore non-matching frames (e.g. modem status 0x8A) and keep reading
    return None


if __name__ == "__main__":
    main()
