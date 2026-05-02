#!/usr/bin/env python3
"""
XBee Pro S1 — Dual receiver: images + telemetry (API mode AP=1).

Runs on: Laptop with XBee USB adapter
Serial:  /dev/ttyUSB0  @ 9600

Listens for RX Packet 64-bit frames (0x80), discriminates packet type
by the leading marker byte:
    0x01 → image chunk  → reassemble JPEG and save to disk
    0x02 → telemetry    → parse binary struct and append to CSV

Supports receiving multiple sequential images.

Usage:
    python3 receive_image.py
    python3 receive_image.py --port /dev/ttyUSB0 --output received_foto.jpg
    python3 receive_image.py --csv telemetry.csv --num-images 3
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import serial

from xbee_frame import (
    PKT_TYPE_IMAGE,
    PKT_TYPE_TELEMETRY,
    TELEMETRY_FIELDS,
    parse_chunk_payload,
    parse_rx64,
    parse_telemetry_payload,
    read_frame,
)

# 60 seg considerando que el xbee pro s1 es lento y que estamos usando 9600 baudios
ASSEMBLY_TIMEOUT_S = 60.0   # max seconds to wait for all chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="XBee Pro S1 — Dual Receiver (Images + Telemetry)")
    parser.add_argument("--port",       default="/dev/ttyUSB0",      help="Serial port (default: /dev/ttyUSB0)")
    parser.add_argument("--baud",       type=int, default=9600,      help="Baudrate (default: 9600)")
    parser.add_argument("--output",     default="received_foto.jpg", help="Base output image path")
    parser.add_argument("--csv",        default="telemetry.csv",     help="Output CSV for telemetry data")
    parser.add_argument("--num-images", type=int, default=3,         help="Number of images to expect (default: 3)")
    args = parser.parse_args()

    output_base = Path(args.output)
    if not output_base.is_absolute():
        output_base = Path(__file__).parent / output_base

    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = Path(__file__).parent / csv_path

    print("=" * 60)
    print("  XBee Pro S1 — Dual Receiver (Images + Telemetry)")
    print("=" * 60)
    print(f"  Port       : {args.port} @ {args.baud}")
    print(f"  Output img : {output_base}")
    print(f"  Output CSV : {csv_path}")
    print(f"  Expecting  : {args.num_images} image(s)")
    print("  Waiting for packets...")
    print("=" * 60)
    print()

    # Storage: { image_id: { chunk_idx: data_bytes } }
    images: dict[int, dict[int, bytes]] = {}
    totals: dict[int, int] = {}               # image_id → total_chunks
    completed_images: set[int] = set()
    rssi_samples: list[int] = []
    img_packets_received = 0
    tel_packets_received = 0

    # ── Open CSV for telemetry ──────────────────────────────────────
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_file = open(csv_path, "w", newline="")
    csv_headers = ["recv_time"] + list(TELEMETRY_FIELDS)
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow(csv_headers)

    with serial.Serial(args.port, args.baud, timeout=1.0) as ser:
        ser.reset_input_buffer()
        t_start: float | None = None    # tiempo del primer paquete recibido

        try:
            while True:
                frame = read_frame(ser, timeout_s=0.5)
                if frame is None:
                    # Check assembly timeout
                    if t_start is not None and time.monotonic() - t_start > ASSEMBLY_TIMEOUT_S:
                        print(f"\n  Timeout ({ASSEMBLY_TIMEOUT_S}s) waiting for remaining packets.")
                        break
                    continue

                # Parse RX 64-bit frame
                rx = parse_rx64(frame)
                if rx is None:
                    continue            # not an RX 64-bit frame

                if len(rx.data) < 1:
                    continue            # no data

                rssi_samples.append(rx.rssi)

                if t_start is None:
                    t_start = time.monotonic()

                # ── Discriminate by type marker ─────────────────────
                pkt_type = rx.data[0]
                payload  = rx.data[1:]  # everything after the type byte

                if pkt_type == PKT_TYPE_IMAGE:
                    # ── Image chunk ─────────────────────────────────
                    parsed = parse_chunk_payload(payload)
                    if parsed is None:
                        continue

                    image_id, chunk_idx, total_chunks, chunk_data = parsed
                    img_packets_received += 1

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
                    if received >= total and image_id not in completed_images:
                        completed_images.add(image_id)
                        print(f"\n  Image {image_id} complete!")

                        # Build output path: received_foto_1.jpg, received_foto_2.jpg, ...
                        img_output = output_base.with_stem(f"{output_base.stem}_{image_id}")
                        _save_image(images[image_id], total, img_output)

                        # Reset timeout for next image
                        t_start = time.monotonic()

                        # Check if all expected images received
                        if len(completed_images) >= args.num_images:
                            print(f"\n  All {args.num_images} images received!")
                            break

                elif pkt_type == PKT_TYPE_TELEMETRY:
                    # ── Telemetry packet ────────────────────────────
                    tel_data = parse_telemetry_payload(payload)
                    if tel_data is None:
                        continue

                    tel_packets_received += 1

                    # Write to CSV
                    recv_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    row = [recv_time] + [tel_data[f] for f in TELEMETRY_FIELDS]
                    csv_writer.writerow(row)
                    csv_file.flush()

                    # Log every telemetry packet
                    elapsed = time.monotonic() - t_start
                    print(
                        f"  [TEL]  #{tel_packets_received}  "
                        f"alt={tel_data['alt_ms5611']:.1f}m  "
                        f"temp={tel_data['temperature']:.1f}°C  "
                        f"pres={tel_data['pressure']:.1f}hPa  "
                        f"v={tel_data['voltage']:.2f}V  "
                        f"rssi=-{rx.rssi}dBm  "
                        f"elapsed={elapsed:.1f}s"
                    )

                else:
                    # Unknown type — skip
                    continue

        except KeyboardInterrupt:
            print("\n  Interrupted by user.")

    # ── Close CSV ───────────────────────────────────────────────────
    csv_file.close()

    # ── Final report ────────────────────────────────────────────────
    print()
    print("=" * 60)
    print(f"  Image packets  : {img_packets_received}")
    print(f"  Telemetry pkts : {tel_packets_received}")
    print(f"  CSV saved      : {csv_path}")
    if rssi_samples:
        avg = sum(rssi_samples) / len(rssi_samples)
        worst = max(rssi_samples)
        best  = min(rssi_samples)
        print(f"  RSSI  best=-{best}dBm  avg=-{avg:.0f}dBm  worst=-{worst}dBm")

    for img_id, chunks in images.items():
        total = totals.get(img_id, 0)
        received = len(chunks)
        pct = 100.0 * received / total if total > 0 else 0
        status = "COMPLETE" if img_id in completed_images else "INCOMPLETE"
        print(f"  Image {img_id}: {received}/{total} chunks ({pct:.0f}%) [{status}]")

        # Save partial image if incomplete but has data
        if img_id not in completed_images and received > 0:
            partial_path = output_base.with_stem(f"{output_base.stem}_{img_id}_partial")
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
