from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

try:
    import smbus2 as smbus
except Exception:
    smbus = None

try:
    import board
    import busio
except Exception:
    board = None
    busio = None

try:
    import adafruit_mmc56x3
except Exception:
    adafruit_mmc56x3 = None

try:
    import adafruit_bme280.basic as adafruit_bme280
except Exception:
    adafruit_bme280 = None

CMD_CONVERT_D1 = 0x48
CMD_CONVERT_D2 = 0x50

BMI160_REG_CMD = 0x7E
BMI160_CMD_ACC_NORMAL = 0x11
BMI160_CMD_GYR_NORMAL = 0x15
BMI160_REG_ACC_RANGE = 0x41
BMI160_REG_GYR_RANGE = 0x43

G_TO_MS2 = 9.80665


@dataclass
class SensorState:
    ms5611: bool = False
    mmc56x3: bool = False
    ina226: bool = False
    bmi160: bool = False
    bme280: bool = False


def signed_16(lsb: int, msb: int) -> int:
    value = (msb << 8) | lsb
    if value >= 0x8000:
        value -= 0x10000
    return value


def ms5611_reset(bus: Any, addr: int) -> None:
    bus.write_byte(addr, 0x1E)
    time.sleep(0.01)


def ms5611_read_prom(bus: Any, addr: int) -> list[int]:
    coeffs = []
    for i in range(8):
        data = bus.read_i2c_block_data(addr, 0xA0 + (i * 2), 2)
        coeffs.append((data[0] << 8) | data[1])
    return coeffs


def ms5611_read_adc(bus: Any, addr: int, cmd: int) -> int:
    bus.write_byte(addr, cmd)
    time.sleep(0.01)
    data = bus.read_i2c_block_data(addr, 0x00, 3)
    return (data[0] << 16) | (data[1] << 8) | data[2]


def ms5611_calculate(d1: int, d2: int, coeffs: list[int]) -> tuple[float, float]:
    d_t = d2 - coeffs[5] * 256
    temp = 2000 + d_t * coeffs[6] / 8388608
    off = coeffs[2] * 65536 + (coeffs[4] * d_t) / 128
    sens = coeffs[1] * 32768 + (coeffs[3] * d_t) / 256
    pressure = (d1 * sens / 2097152 - off) / 32768
    return temp / 100.0, pressure / 100.0


def pressure_to_altitude(pressure_hpa: float, sea_level_hpa: float) -> float:
    if pressure_hpa <= 0:
        return 0.0
    return 44330.0 * (1.0 - math.pow(pressure_hpa / sea_level_hpa, 1.0 / 5.255))


def read_ina226(bus: Any, addr: int, r_shunt_ohm: float) -> tuple[float, float]:
    data_v = bus.read_i2c_block_data(addr, 0x02, 2)
    raw_v = (data_v[0] << 8) | data_v[1]
    voltage_v = raw_v * 0.00125

    data_s = bus.read_i2c_block_data(addr, 0x01, 2)
    raw_s = (data_s[0] << 8) | data_s[1]
    if raw_s > 32767:
        raw_s -= 65536

    shunt_v = raw_s * 0.0000025
    current_a = shunt_v / r_shunt_ohm
    return voltage_v, current_a


def init_bmi160(bus: Any, addr: int) -> None:
    bus.write_byte_data(addr, BMI160_REG_CMD, BMI160_CMD_ACC_NORMAL)
    time.sleep(0.05)
    bus.write_byte_data(addr, BMI160_REG_CMD, BMI160_CMD_GYR_NORMAL)
    time.sleep(0.05)
    bus.write_byte_data(addr, BMI160_REG_ACC_RANGE, 0x03)
    bus.write_byte_data(addr, BMI160_REG_GYR_RANGE, 0x00)


def read_bmi160(bus: Any, addr: int) -> tuple[float, float, float, float, float, float]:
    raw = bus.read_i2c_block_data(addr, 0x0C, 12)
    gyro_x_raw = signed_16(raw[0], raw[1])
    gyro_y_raw = signed_16(raw[2], raw[3])
    gyro_z_raw = signed_16(raw[4], raw[5])
    acc_x_raw = signed_16(raw[6], raw[7])
    acc_y_raw = signed_16(raw[8], raw[9])
    acc_z_raw = signed_16(raw[10], raw[11])

    gyro_x_dps = gyro_x_raw / 16.4
    gyro_y_dps = gyro_y_raw / 16.4
    gyro_z_dps = gyro_z_raw / 16.4
    acc_x = (acc_x_raw / 16384.0) * G_TO_MS2
    acc_y = (acc_y_raw / 16384.0) * G_TO_MS2
    acc_z = (acc_z_raw / 16384.0) * G_TO_MS2
    return acc_x, acc_y, acc_z, gyro_x_dps, gyro_y_dps, gyro_z_dps


class SensorSuite:
    def __init__(self, cfg, logger) -> None:
        self.cfg = cfg
        self.logger = logger

        self.bus = None
        self.i2c = None

        self.ms_coeffs: list[int] | None = None
        self.mmc_sensor = None
        self.bme_sensor = None

        self.state = SensorState()
        self._last_warn_time: dict[str, float] = {}

        self._setup_interfaces()
        self._setup_sensors()

    def _warn_missing(self, sensor_name: str, exc: Exception | None = None) -> None:
        if exc is None:
            self.logger.warning(
                "Sensor %s no disponible, se enviaran ceros", sensor_name
            )
        else:
            self.logger.warning(
                "Sensor %s no disponible (%s), se enviaran ceros", sensor_name, exc
            )

    def _warn_runtime(self, key: str, msg: str) -> None:
        now = time.monotonic()
        last = self._last_warn_time.get(key, 0.0)
        if now - last >= 15.0:
            self.logger.warning(msg)
            self._last_warn_time[key] = now

    def _setup_interfaces(self) -> None:
        if smbus is not None:
            try:
                self.bus = smbus.SMBus(1)
            except Exception as exc:
                self.logger.warning("No se pudo abrir SMBus(1): %s", exc)
        else:
            self.logger.warning(
                "smbus2 no instalado, sensores I2C SMBus quedaran en cero"
            )

        if board is not None and busio is not None:
            try:
                self.i2c = busio.I2C(board.SCL, board.SDA)
            except Exception as exc:
                self.logger.warning("No se pudo inicializar busio I2C: %s", exc)
        else:
            self.logger.warning(
                "board/busio no instalado, sensores Adafruit quedaran en cero"
            )

    def _setup_sensors(self) -> None:
        if self.bus is not None:
            try:
                ms5611_reset(self.bus, self.cfg.ms5611_addr)
                self.ms_coeffs = ms5611_read_prom(self.bus, self.cfg.ms5611_addr)
                self.state.ms5611 = True
                self.logger.info("MS5611 detectado")
            except Exception as exc:
                self._warn_missing("MS5611", exc)

            try:
                _voltage, _current = read_ina226(
                    self.bus, self.cfg.ina226_addr, self.cfg.ina226_r_shunt_ohm
                )
                self.state.ina226 = True
                self.logger.info("INA226 detectado")
            except Exception as exc:
                self._warn_missing("INA226", exc)

            try:
                init_bmi160(self.bus, self.cfg.bmi160_addr)
                self.state.bmi160 = True
                self.logger.info("BMI160 detectado")
            except Exception as exc:
                self._warn_missing("BMI160", exc)

        if self.i2c is not None and adafruit_mmc56x3 is not None:
            try:
                self.mmc_sensor = adafruit_mmc56x3.MMC5603(
                    self.i2c, address=self.cfg.mmc56x3_addr
                )
                self.state.mmc56x3 = True
                self.logger.info("MMC56x3 detectado")
            except Exception as exc:
                self._warn_missing("MMC56x3", exc)
        else:
            self._warn_missing("MMC56x3")

        if self.i2c is not None and adafruit_bme280 is not None:
            try:
                self.bme_sensor = adafruit_bme280.Adafruit_BME280_I2C(
                    self.i2c, address=self.cfg.bme280_addr
                )
                self.bme_sensor.sea_level_pressure = self.cfg.sea_level_pressure_hpa
                self.state.bme280 = True
                self.logger.info("BME280 detectado")
            except Exception as exc:
                self._warn_missing("BME280", exc)
        else:
            self._warn_missing("BME280")

    def read_telemetry(self) -> dict[str, float | int]:
        now_ms = int(time.time() * 1000)
        payload: dict[str, float | int] = {
            "time": now_ms,
            "pres_ms5611": 0.0,
            "pres_bme280": 0.0,
            "temp_bme280": 0.0,
            "hum_bme280": 0.0,
            "accel_x": 0.0,
            "accel_y": 0.0,
            "accel_z": 0.0,
            "gyro_x": 0.0,
            "gyro_y": 0.0,
            "gyro_z": 0.0,
            "mag_x": 0.0,
            "mag_y": 0.0,
            "mag_z": 0.0,
            "current_ina226": 0.0,
            "power_ina226": 0.0,
        }

        if self.state.ms5611 and self.bus is not None and self.ms_coeffs is not None:
            try:
                d1 = ms5611_read_adc(self.bus, self.cfg.ms5611_addr, CMD_CONVERT_D1)
                d2 = ms5611_read_adc(self.bus, self.cfg.ms5611_addr, CMD_CONVERT_D2)
                _temp_c, pressure_hpa = ms5611_calculate(d1, d2, self.ms_coeffs)
                payload["pres_ms5611"] = float(pressure_hpa * 100.0)
            except Exception as exc:
                self._warn_runtime(
                    "ms5611", f"Lectura MS5611 fallida, manteniendo ceros: {exc}"
                )

        if self.state.bme280 and self.bme_sensor is not None:
            try:
                temp_bme = float(self.bme_sensor.temperature)
                pressure_bme = float(self.bme_sensor.pressure)
                humidity_bme = float(getattr(self.bme_sensor, "humidity", 0.0))

                payload["temp_bme280"] = temp_bme
                payload["pres_bme280"] = pressure_bme * 100.0
                payload["hum_bme280"] = humidity_bme
            except Exception as exc:
                self._warn_runtime(
                    "bme280", f"Lectura BME280 fallida, manteniendo ceros: {exc}"
                )

        if self.state.bmi160 and self.bus is not None:
            try:
                acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z = read_bmi160(
                    self.bus, self.cfg.bmi160_addr
                )
                payload["accel_x"] = float(acc_x)
                payload["accel_y"] = float(acc_y)
                payload["accel_z"] = float(acc_z)
                payload["gyro_x"] = float(gyro_x)
                payload["gyro_y"] = float(gyro_y)
                payload["gyro_z"] = float(gyro_z)
            except Exception as exc:
                self._warn_runtime(
                    "bmi160", f"Lectura BMI160 fallida, manteniendo ceros: {exc}"
                )

        if self.state.ina226 and self.bus is not None:
            try:
                voltage, current_a = read_ina226(
                    self.bus, self.cfg.ina226_addr, self.cfg.ina226_r_shunt_ohm
                )
                payload["current_ina226"] = float(current_a)
                payload["power_ina226"] = float(voltage * current_a)
            except Exception as exc:
                self._warn_runtime(
                    "ina226", f"Lectura INA226 fallida, manteniendo ceros: {exc}"
                )

        if self.state.mmc56x3 and self.mmc_sensor is not None:
            try:
                mx, my, mz = self.mmc_sensor.magnetic
                payload["mag_x"] = float(mx)
                payload["mag_y"] = float(my)
                payload["mag_z"] = float(mz)
            except Exception as exc:
                self._warn_runtime("mmc56x3", f"Lectura MMC56x3 fallida: {exc}")

        return payload

    def close(self) -> None:
        if self.bus is not None:
            try:
                self.bus.close()
            except Exception:
                pass

        if self.i2c is not None:
            try:
                self.i2c.deinit()
            except Exception:
                pass
