#!/bin/bash
# Install dependencies for Linux

cd test
python -m venv .venv
source .venv/bin/activate

pip install \
    pyserial \
    smbus2 \
    adafruit-circuitpython-mmc56x3 \
    adafruit-circuitpython-bme280