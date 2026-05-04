#!/usr/bin/env python3
"""
XBee Pro S1 — Dual sender: images + telemetry (API mode AP=1).

Runs on: Raspberry Pi Zero 2W
Serial:  /dev/serial0  @ 9600

Reads JPEG images, splits them into 95-byte chunks, and sends each one
inside a TX Request 64-bit frame (0x00).  Every 10 image chunks, one
telemetry packet is interleaved (ratio 10:1, image priority).

A background thread reads sensors at 5 Hz and keeps a shared
``current_telemetry`` dict that the main loop snapshots when it is time
to send a telemetry frame.

Usage:
    python3 send_image.py
    python3 send_image.py --port /dev/serial0 --ratio 10
    python3 send_image.py --images foto-1.jpg,foto-2.jpg,foto-3.jpg
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import threading
import time
from pathlib import Path

import serial

from xbee_frame import (
    CHUNK_DATA_SIZE,
    build_chunk_payload,
    build_telemetry_payload,
    build_tx64,
    parse_tx_status,
    read_frame,
)

MAX_RETRIES = 5          # retries per chunk at application level
TX_STATUS_TIMEOUT_S = 0.5


# ── Sensor thread ────────────────────────────────────────────────────

def _sensor_loop(
    stop_event: threading.Event,
    lock: threading.Lock,
    shared: dict,
    logger: logging.Logger,
    log_sensors: bool = True,
) -> None:
    """Background daemon: read sensors at 5 Hz and update *shared* dict."""
    # Late imports — only needed on the Raspberry Pi where the HW exists.
    from config import SensorConfig
    from sensors import SensorSuite

    cfg = SensorConfig.from_env()
    suite = SensorSuite(cfg, logger)
    logger.info(
        "Sensor thread started  (sensors: MS5611=%s  BME280=%s  BMI160=%s  "
        "INA226=%s  MMC56x3=%s)",
        suite.state.ms5611, suite.state.bme280, suite.state.bmi160,
        suite.state.ina226, suite.state.mmc56x3,
    )

    try:
        while not stop_event.is_set():
            try:
                data = suite.read_telemetry()
                with lock:
                    shared.update(data)

                if log_sensors:
                    logger.info(
                        "[SENSORS]  "
                        "alt_ms5611=%6.1fm  alt_bme280=%6.1fm  "
                        "pres=%7.2fhPa  temp=%5.2f°C  vz=%6.2fm/s  "
                        "ax=%6.2f  ay=%6.2f  az=%6.2f m/s²  "
                        "gz=%6.1f°/s  "
                        "V=%5.3fV  I=%7.2fmA",
                        data.get("alt_ms5611", 0.0),
                        data.get("alt_bme280",  0.0),
                        data.get("pressure",    0.0),
                        data.get("temperature", 0.0),
                        data.get("velocity_z",  0.0),
                        data.get("accel_x",     0.0),
                        data.get("accel_y",     0.0),
                        data.get("accel_z",     0.0),
                        data.get("gyro_z",      0.0),
                        data.get("voltage",     0.0),
                        data.get("current",     0.0),
                    )

            except Exception as exc:
                logger.warning("Sensor read error: %s", exc)
            stop_event.wait(0.2)  # 5 Hz
    finally:
        suite.close()
        logger.info("Sensor thread stopped")


# ── TX helpers ───────────────────────────────────────────────────────

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


def _send_with_retry(ser, frame_id: int, dest_addr: bytes,
                     payload: bytes, label: str) -> bool:
    """Send a payload inside a TX64 frame with ACK + retries.
    
    Returns True on success, False after MAX_RETRIES failures.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        api_frame = build_tx64(frame_id, dest_addr, payload)
        ser.reset_input_buffer()
        ser.write(api_frame)
        ser.flush()

        status = _wait_tx_status(ser, frame_id)

        if status is not None and status == 0x00:
            return True

        tag = f"0x{status:02X}" if status is not None else "timeout"
        print(f"  [RETRY] {label}  status={tag}  attempt={attempt}/{MAX_RETRIES}")

    print(f"  [FAIL]  {label}  after {MAX_RETRIES} attempts")
    return False


# ── Main ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="XBee Pro S1 — Dual Sender (Images + Telemetry)")
    parser.add_argument("--port",     default="/dev/serial0",     help="Serial port (default: /dev/serial0)")
    parser.add_argument("--baud",     type=int, default=9600,     help="Baudrate (default: 9600)")
    parser.add_argument("--dest",     default="0013A200406EFB43", help="Destination 64-bit address hex")
    parser.add_argument("--images",   default="foto-1.jpg,foto-2.jpg,foto-3.jpg",
                        help="Comma-separated list of image files to send")
    parser.add_argument("--ratio",    type=int, default=10,
                        help="Image-to-telemetry ratio (default: 10 image chunks per 1 telemetry)")
    parser.add_argument("--log-sensors", action=argparse.BooleanOptionalAction, default=True,
                        help="Print sensor readings to console at each 5 Hz cycle (default: on). "
                             "Use --no-log-sensors to silence.")
    args = parser.parse_args()

    # ── Logging ─────────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger("dual_sender")
    if args.log_sensors:
        logger.info("Sensor logging enabled (--no-log-sensors to disable)")

    # ── Locate images ───────────────────────────────────────────────
    image_names = [n.strip() for n in args.images.split(",") if n.strip()]
    image_list: list[tuple[int, Path, bytes]] = []  # (image_id, path, data)

    for idx, name in enumerate(image_names, start=1):
        path = Path(name)
        if not path.exists():
            path = Path(__file__).parent / name
        if not path.exists():
            print(f"ERROR: image not found: {name}")
            sys.exit(1)

        data = path.read_bytes()
        total = math.ceil(len(data) / CHUNK_DATA_SIZE)
        if total > 255:
            print(f"ERROR: image too large ({len(data)}B = {total} chunks, max 255)")
            sys.exit(1)

        image_list.append((idx, path, data))

    dest_addr = bytes.fromhex(args.dest)

    # ── Start sensor thread ─────────────────────────────────────────
    current_telemetry: dict[str, float | int] = {}
    telemetry_lock = threading.Lock()
    stop_event = threading.Event()

    sensor_thread = threading.Thread(
        target=_sensor_loop,
        args=(stop_event, telemetry_lock, current_telemetry, logger, args.log_sensors),
        daemon=True,
        name="sensor-5hz",
    )
    sensor_thread.start()
    time.sleep(0.5)  # let the sensor thread do at least one read

    # ── Summary ─────────────────────────────────────────────────────
    print("=" * 60)
    print("  XBee Pro S1 — Dual Sender (Images + Telemetry)")
    print("=" * 60)
    for img_id, img_path, img_data in image_list:
        total_ch = math.ceil(len(img_data) / CHUNK_DATA_SIZE)
        print(f"  Image {img_id}: {img_path.name} ({len(img_data)}B, {total_ch} chunks)")
    print(f"  Ratio   : {args.ratio}:1 (image chunks : telemetry)")
    print(f"  Dest    : {args.dest}")
    print(f"  Port    : {args.port} @ {args.baud}")
    print("=" * 60)
    print()

    # ── Open serial and transmit ────────────────────────────────────
    with serial.Serial(args.port, args.baud, timeout=1.0) as ser:
        ser.reset_input_buffer()
        time.sleep(0.1)

        frame_id = 1
        total_img_ok = 0
        total_img_fail = 0
        total_tel_ok = 0
        total_tel_fail = 0
        t_global_start = time.perf_counter()

        try:
            for img_id, img_path, image_data in image_list:
                total_chunks = math.ceil(len(image_data) / CHUNK_DATA_SIZE)

                print(f"\n{'─' * 60}")
                print(f"  Sending image {img_id}: {img_path.name}  ({total_chunks} chunks)")
                print(f"{'─' * 60}")

                chunk_counter = 0
                t_img_start = time.perf_counter()

                for idx in range(total_chunks):
                    # ── Send image chunk ────────────────────────
                    start = idx * CHUNK_DATA_SIZE
                    end   = start + CHUNK_DATA_SIZE
                    chunk = image_data[start:end]

                    app_payload = build_chunk_payload(
                        image_id=img_id,
                        chunk_idx=idx,
                        total_chunks=total_chunks,
                        data=chunk,
                    )

                    label = f"img={img_id} chunk {idx}/{total_chunks}"
                    ok = _send_with_retry(ser, frame_id, dest_addr, app_payload, label)
                    frame_id = (frame_id % 255) + 1

                    if ok:
                        total_img_ok += 1
                    else:
                        total_img_fail += 1

                    chunk_counter += 1

                    # ── Interleave telemetry every <ratio> chunks ──
                    if chunk_counter >= args.ratio:
                        with telemetry_lock:
                            snapshot = dict(current_telemetry)

                        if snapshot:
                            tel_payload = build_telemetry_payload(snapshot)
                            tel_label = f"telemetry (after img={img_id} chunk {idx})"
                            tok = _send_with_retry(ser, frame_id, dest_addr, tel_payload, tel_label)
                            frame_id = (frame_id % 255) + 1

                            if tok:
                                total_tel_ok += 1
                            else:
                                total_tel_fail += 1

                        chunk_counter = 0

                    # ── Progress log ────────────────────────────
                    if (idx + 1) % 10 == 0 or idx == total_chunks - 1:
                        elapsed = time.perf_counter() - t_img_start
                        pct  = 100.0 * (idx + 1) / total_chunks
                        rate = (idx + 1) / elapsed if elapsed > 0 else 0
                        print(
                            f"  [{pct:5.1f}%]  img={img_id}  chunk {idx+1}/{total_chunks}  "
                            f"img_ok={total_img_ok}  img_fail={total_img_fail}  "
                            f"tel_ok={total_tel_ok}  tel_fail={total_tel_fail}  "
                            f"elapsed={elapsed:.1f}s  rate={rate:.1f} chunks/s"
                        )

                img_elapsed = time.perf_counter() - t_img_start
                print(f"\n  Image {img_id} done in {img_elapsed:.1f}s")

        except KeyboardInterrupt:
            print("\n  Interrupted by user.")

        # ── Stop sensor thread ──────────────────────────────────────
        stop_event.set()
        sensor_thread.join(timeout=2.0)

        elapsed = time.perf_counter() - t_global_start
        print()
        print("=" * 60)
        print(f"  DONE")
        print(f"  Images     : ok={total_img_ok}  fail={total_img_fail}")
        print(f"  Telemetry  : ok={total_tel_ok}  fail={total_tel_fail}")
        print(f"  Elapsed    : {elapsed:.1f}s")
        print("=" * 60)


if __name__ == "__main__":
    main()
