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
# 03:40:57  INFO     [SENSORS]  t=12045123ms  pres_ms5611=101325.6Pa  ...
_SENSORS_RE = re.compile(
    r"(?P<time>\d{2}:\d{2}:\d{2})"         # HH:MM:SS
    r".*?\[SENSORS\]\s+"
    r"t=\s*(?P<time_ms>\d+)ms\s+"
    r"pres_ms5611=\s*(?P<pres_ms5611>-?[\d.]+)Pa\s+"
    r"pres_bme280=\s*(?P<pres_bme280>-?[\d.]+)Pa\s+"
    r"temp_bme280=\s*(?P<temp_bme280>-?[\d.]+)C\s+"
    r"hum_bme280=\s*(?P<hum_bme280>-?[\d.]+)%\s+"
    r"ax=\s*(?P<accel_x>-?[\d.]+)\s+"
    r"ay=\s*(?P<accel_y>-?[\d.]+)\s+"
    r"az=\s*(?P<accel_z>-?[\d.]+)\s+m/s2\s+"
    r"gx=\s*(?P<gyro_x>-?[\d.]+)\s+"
    r"gy=\s*(?P<gyro_y>-?[\d.]+)\s+"
    r"gz=\s*(?P<gyro_z>-?[\d.]+)\s+dps\s+"
    r"mx=\s*(?P<mag_x>-?[\d.]+)\s+"
    r"my=\s*(?P<mag_y>-?[\d.]+)\s+"
    r"mz=\s*(?P<mag_z>-?[\d.]+)\s+uT\s+"
    r"I=\s*(?P<current_ina226>-?[\d.]+)A\s+"
    r"P=\s*(?P<power_ina226>-?[\d.]+)W"
)

CSV_HEADERS = [
    "log_time",
    "time_ms",
    "pres_ms5611_Pa",
    "pres_bme280_Pa",
    "temp_bme280_C",
    "hum_bme280_pct",
    "accel_x_ms2",
    "accel_y_ms2",
    "accel_z_ms2",
    "gyro_x_dps",
    "gyro_y_dps",
    "gyro_z_dps",
    "mag_x_uT",
    "mag_y_uT",
    "mag_z_uT",
    "current_ina226_A",
    "power_ina226_W",
]


def parse_line(line: str) -> dict | None:
    """Return a dict of sensor values if the line contains [SENSORS], else None."""
    m = _SENSORS_RE.search(line)
    if m is None:
        return None
    g = m.groupdict()
    return {
        "log_time":         g["time"],
        "time_ms":          int(g["time_ms"]),
        "pres_ms5611_Pa":   float(g["pres_ms5611"]),
        "pres_bme280_Pa":   float(g["pres_bme280"]),
        "temp_bme280_C":    float(g["temp_bme280"]),
        "hum_bme280_pct":   float(g["hum_bme280"]),
        "accel_x_ms2":      float(g["accel_x"]),
        "accel_y_ms2":      float(g["accel_y"]),
        "accel_z_ms2":      float(g["accel_z"]),
        "gyro_x_dps":       float(g["gyro_x"]),
        "gyro_y_dps":       float(g["gyro_y"]),
        "gyro_z_dps":       float(g["gyro_z"]),
        "mag_x_uT":         float(g["mag_x"]),
        "mag_y_uT":         float(g["mag_y"]),
        "mag_z_uT":         float(g["mag_z"]),
        "current_ina226_A": float(g["current_ina226"]),
        "power_ina226_W":   float(g["power_ina226"]),
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
                    f"pres={row['pres_ms5611_Pa']:.1f}Pa  "
                    f"temp={row['temp_bme280_C']:.2f}C  "
                    f"hum={row['hum_bme280_pct']:.1f}%  "
                    f"ax={row['accel_x_ms2']:.2f}  "
                    f"ay={row['accel_y_ms2']:.2f}  "
                    f"az={row['accel_z_ms2']:.2f}"
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
