#!/usr/bin/env python3
"""
XBee Pro S1 — Image receiver (API mode AP=1).

Runs on: Laptop with XBee USB adapter
Serial:  /dev/ttyUSB0  @ 9600

Listens for RX Packet 64-bit frames (0x80), reassembles the JPEG
image from chunks, and saves it to disk.

Usage:
    python3 receive_image.py
    python3 receive_image.py --port /dev/ttyUSB0 --output received_foto.jpg
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import serial

from xbee_frame import (
    parse_chunk_payload,
    parse_rx64,
    read_frame,
)

ASSEMBLY_TIMEOUT_S = 120.0   # max seconds to wait for all chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="XBee Pro S1 — Image Receiver")
    parser.add_argument("--port",    default="/dev/ttyUSB0",      help="Serial port (default: /dev/ttyUSB0)")
    parser.add_argument("--baud",    type=int, default=9600,      help="Baudrate (default: 9600)")
    parser.add_argument("--output",  default="received_foto.jpg", help="Output image path")
    args = parser.parse_args()

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = Path(__file__).parent / output_path

    print("=" * 60)
    print("  XBee Pro S1 — Image Receiver")
    print("=" * 60)
    print(f"  Port   : {args.port} @ {args.baud}")
    print(f"  Output : {output_path}")
    print("  Waiting for image chunks...")
    print("=" * 60)
    print()

    # Storage: { image_id: { chunk_idx: data_bytes } }
    images: dict[int, dict[int, bytes]] = {}
    totals: dict[int, int] = {}               # image_id → total_chunks
    rssi_samples: list[int] = []
    packets_received = 0

    with serial.Serial(args.port, args.baud, timeout=1.0) as ser:
        ser.reset_input_buffer()
        t_start: float | None = None

        try:
            while True:
                frame = read_frame(ser, timeout_s=2.0)
                if frame is None:
                    # Check assembly timeout
                    if t_start is not None and time.monotonic() - t_start > ASSEMBLY_TIMEOUT_S:
                        print(f"\n  Timeout ({ASSEMBLY_TIMEOUT_S}s) waiting for remaining chunks.")
                        break
                    continue

                rx = parse_rx64(frame)
                if rx is None:
                    continue            # not an RX 64-bit frame

                parsed = parse_chunk_payload(rx.data)
                if parsed is None:
                    continue            # payload too short

                image_id, chunk_idx, total_chunks, chunk_data = parsed
                packets_received += 1
                rssi_samples.append(rx.rssi)

                if t_start is None:
                    t_start = time.monotonic()

                # Store chunk
                if image_id not in images:
                    images[image_id] = {}
                    totals[image_id] = total_chunks
                    print(f"  [NEW]  image_id={image_id}  total_chunks={total_chunks}")

                images[image_id][chunk_idx] = chunk_data

                received = len(images[image_id])
                total    = totals[image_id]

                # Progress every 10 chunks
                if received % 10 == 0 or received == total:
                    elapsed = time.monotonic() - t_start
                    avg_rssi = sum(rssi_samples) / len(rssi_samples)
                    pct = 100.0 * received / total
                    print(
                        f"  [{pct:5.1f}%]  image_id={image_id}  "
                        f"chunks={received}/{total}  "
                        f"rssi=-{rx.rssi}dBm  avg_rssi=-{avg_rssi:.0f}dBm  "
                        f"elapsed={elapsed:.1f}s"
                    )

                # Check if image is complete
                if received >= total:
                    print(f"\n  Image {image_id} complete!")
                    _save_image(images[image_id], total, output_path)
                    break

        except KeyboardInterrupt:
            print("\n  Interrupted by user.")

    # ── Final report ────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(f"  Packets received: {packets_received}")
    if rssi_samples:
        avg = sum(rssi_samples) / len(rssi_samples)
        worst = max(rssi_samples)
        best  = min(rssi_samples)
        print(f"  RSSI  best=-{best}dBm  avg=-{avg:.0f}dBm  worst=-{worst}dBm")

    for img_id, chunks in images.items():
        total = totals.get(img_id, 0)
        received = len(chunks)
        pct = 100.0 * received / total if total > 0 else 0
        status = "COMPLETE" if received >= total else "INCOMPLETE"
        print(f"  Image {img_id}: {received}/{total} chunks ({pct:.0f}%) [{status}]")

        # Save partial image if incomplete but has data
        if received < total and received > 0:
            partial_path = output_path.with_stem(f"{output_path.stem}_partial")
            _save_image(chunks, total, partial_path)
    print("=" * 60)


def _save_image(chunks: dict[int, bytes], total: int, path: Path) -> None:
    """Assemble chunks and write to file."""
    image_data = b""
    missing = []
    for i in range(total):
        if i in chunks:
            image_data += chunks[i]
        else:
            missing.append(i)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image_data)
    print(f"  Saved: {path}  ({len(image_data)} bytes)")

    if missing:
        print(f"  WARNING: {len(missing)} missing chunks: {missing[:20]}{'...' if len(missing)>20 else ''}")


if __name__ == "__main__":
    main()
