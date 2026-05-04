#!/usr/bin/env python3
"""
Parse [SENSORS] log lines from send_image.py output and write to CSV.

Accepts input from a file or stdin (real-time pipe).

Usage:
    # Parse a saved log file
    python3 parse_sensors_log.py sensors.log

    # Real-time pipe from sender
    python3 send_image.py 2>&1 | python3 parse_sensors_log.py

    # Custom output CSV
    python3 parse_sensors_log.py sensors.log --output my_data.csv

    # Capture sender output to file AND parse it live
    python3 send_image.py 2>&1 | tee sensors.log | python3 parse_sensors_log.py
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

# ── Regex pattern ──────────────────────────────────────────────────────
# Matches lines like:
# 03:40:57  INFO     [SENSORS]  alt_ms5611=7570.4m  alt_bme280=2398.4m  ...
_SENSORS_RE = re.compile(
    r"(?P<time>\d{2}:\d{2}:\d{2})"         # HH:MM:SS
    r".*?\[SENSORS\]\s+"
    r"alt_ms5611=\s*(?P<alt_ms5611>-?[\d.]+)m\s+"
    r"alt_bme280=\s*(?P<alt_bme280>-?[\d.]+)m\s+"
    r"pres=\s*(?P<pressure>-?[\d.]+)hPa\s+"
    r"temp=\s*(?P<temperature>-?[\d.]+).C\s+"      # °C (any char before C)
    r"vz=\s*(?P<velocity_z>-?[\d.]+)m/s\s+"
    r"ax=\s*(?P<accel_x>-?[\d.]+)\s+"
    r"ay=\s*(?P<accel_y>-?[\d.]+)\s+"
    r"az=\s*(?P<accel_z>-?[\d.]+)\s+m/s"          # m/s² (² may vary by encoding)
    r".?\s+"
    r"gz=\s*(?P<gyro_z>-?[\d.]+).+?/s\s+"          # °/s
    r"V=\s*(?P<voltage>-?[\d.]+)V\s+"
    r"I=\s*(?P<current>-?[\d.]+)mA"
)

CSV_HEADERS = [
    "log_time",
    "alt_ms5611_m",
    "alt_bme280_m",
    "pressure_hPa",
    "temperature_C",
    "velocity_z_ms",
    "accel_x_ms2",
    "accel_y_ms2",
    "accel_z_ms2",
    "gyro_z_dps",
    "voltage_V",
    "current_mA",
]


def parse_line(line: str) -> dict | None:
    """Return a dict of sensor values if the line contains [SENSORS], else None."""
    m = _SENSORS_RE.search(line)
    if m is None:
        return None
    g = m.groupdict()
    return {
        "log_time":       g["time"],
        "alt_ms5611_m":   float(g["alt_ms5611"]),
        "alt_bme280_m":   float(g["alt_bme280"]),
        "pressure_hPa":   float(g["pressure"]),
        "temperature_C":  float(g["temperature"]),
        "velocity_z_ms":  float(g["velocity_z"]),
        "accel_x_ms2":    float(g["accel_x"]),
        "accel_y_ms2":    float(g["accel_y"]),
        "accel_z_ms2":    float(g["accel_z"]),
        "gyro_z_dps":     float(g["gyro_z"]),
        "voltage_V":      float(g["voltage"]),
        "current_mA":     float(g["current"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse [SENSORS] log lines and write to CSV."
    )
    parser.add_argument(
        "input", nargs="?", default="-",
        help="Log file to parse. Use '-' or omit for stdin (default: stdin).",
    )
    parser.add_argument(
        "--output", "-o", default="sensors_parsed.csv",
        help="Output CSV file (default: sensors_parsed.csv).",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress per-row console output.",
    )
    args = parser.parse_args()

    out_path = Path(args.output)

    # Open input
    if args.input == "-":
        source = sys.stdin
        source_name = "stdin"
    else:
        in_path = Path(args.input)
        if not in_path.exists():
            print(f"ERROR: file not found: {in_path}", file=sys.stderr)
            sys.exit(1)
        source = open(in_path, encoding="utf-8", errors="replace")
        source_name = str(in_path)

    # Open output CSV
    out_path.parent.mkdir(parents=True, exist_ok=True)
    csv_file = open(out_path, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(csv_file, fieldnames=CSV_HEADERS)
    writer.writeheader()

    parsed = 0
    skipped = 0

    print(f"Parsing '{source_name}' → '{out_path}' ...", file=sys.stderr)
    print("(Ctrl+C to stop if reading from stdin)\n", file=sys.stderr)

    try:
        for raw_line in source:
            line = raw_line.rstrip()

            # Pass through non-sensor lines to stdout so you still see them
            if "[SENSORS]" not in line:
                if not args.quiet:
                    print(line)
                skipped += 1
                continue

            row = parse_line(line)
            if row is None:
                print(f"  [WARN] could not parse: {line}", file=sys.stderr)
                skipped += 1
                continue

            writer.writerow(row)
            csv_file.flush()
            parsed += 1

            if not args.quiet:
                print(
                    f"  [CSV #{parsed:04d}]  "
                    f"alt={row['alt_ms5611_m']:.1f}m  "
                    f"temp={row['temperature_C']:.2f}°C  "
                    f"pres={row['pressure_hPa']:.2f}hPa  "
                    f"ax={row['accel_x_ms2']:.2f}  "
                    f"ay={row['accel_y_ms2']:.2f}  "
                    f"az={row['accel_z_ms2']:.2f}  "
                    f"gz={row['gyro_z_dps']:.1f}°/s"
                )

    except KeyboardInterrupt:
        print("\n  Interrupted by user.", file=sys.stderr)
    finally:
        csv_file.close()
        if source is not sys.stdin:
            source.close()

    print(f"\nDone. Parsed {parsed} sensor rows → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
