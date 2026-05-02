"""
Sensor configuration with environment-variable overrides.

Default I2C addresses and calibration values for the CanSat sensor suite.
Each value can be overridden via an environment variable of the same name
(upper-case).  Hex addresses are read as base-16 integers.

Usage:
    from config import SensorConfig
    cfg = SensorConfig()          # all defaults
    cfg = SensorConfig.from_env() # defaults + env overrides
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return int(raw, 0)  # base 0 → auto-detect hex (0x...) or decimal


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return float(raw)


@dataclass
class SensorConfig:
    ms5611_addr: int = 0x77
    ina226_addr: int = 0x40
    bmi160_addr: int = 0x69
    mmc56x3_addr: int = 0x30
    bme280_addr: int = 0x76

    ina226_r_shunt_ohm: float = 0.1
    sea_level_pressure_hpa: float = 1013.25

    @classmethod
    def from_env(cls) -> SensorConfig:
        """Create a SensorConfig with values overridden by env vars."""
        return cls(
            ms5611_addr=_env_int("MS5611_ADDR", 0x77),
            ina226_addr=_env_int("INA226_ADDR", 0x40),
            bmi160_addr=_env_int("BMI160_ADDR", 0x69),
            mmc56x3_addr=_env_int("MMC56X3_ADDR", 0x30),
            bme280_addr=_env_int("BME280_ADDR", 0x76),
            ina226_r_shunt_ohm=_env_float("INA226_R_SHUNT_OHM", 0.1),
            sea_level_pressure_hpa=_env_float("SEA_LEVEL_PRESSURE_HPA", 1013.25),
        )
