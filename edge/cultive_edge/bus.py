"""RS485 Modbus RTU bus access, plus a simulated bus for working without hardware."""
import math
import random
import time


def _with_unit(fn, unit, *args, **kw):
    """pymodbus renamed the unit kwarg across versions (unit -> slave -> device_id)."""
    last = None
    for key in ("device_id", "slave", "unit"):
        try:
            return fn(*args, **{key: unit}, **kw)
        except TypeError as e:
            last = e
            if key not in str(e):
                raise
    raise last


class BusError(Exception):
    pass


class ModbusBus:
    def __init__(self, port, baudrate=9600, parity="N", stopbits=1, timeout=1.0):
        from pymodbus.client import ModbusSerialClient

        self.client = ModbusSerialClient(
            port=port, baudrate=baudrate, parity=parity, stopbits=stopbits,
            bytesize=8, timeout=timeout, retries=1,
        )
        if not self.client.connect():
            raise BusError(f"Cannot open serial port {port}")

    def read(self, unit, reg, count=1, fc=3):
        fn = self.client.read_holding_registers if fc == 3 else self.client.read_input_registers
        rr = _with_unit(fn, unit, reg, count=count)
        if rr.isError():
            raise BusError(f"unit {unit} reg {reg:#06x}: {rr}")
        return list(rr.registers)

    def write_register(self, unit, reg, value):
        rr = _with_unit(self.client.write_register, unit, reg, value)
        if rr.isError():
            raise BusError(f"unit {unit} write reg {reg:#06x}: {rr}")

    def write_coil(self, unit, coil, on):
        rr = _with_unit(self.client.write_coil, unit, coil, bool(on))
        if rr.isError():
            raise BusError(f"unit {unit} coil {coil}: {rr}")

    def read_coils(self, unit, start, count):
        rr = _with_unit(self.client.read_coils, unit, start, count=count)
        if rr.isError():
            raise BusError(f"unit {unit} coils: {rr}")
        return list(rr.bits[:count])

    def close(self):
        self.client.close()


class SimBus:
    """Fake bus with plausible polyhouse readings (day/night cycle, drying soil)."""

    def __init__(self, devices):
        self.by_unit = {d["address"]: d for d in devices}
        self.coils = {}
        self.soil = 32.0
        self.t0 = time.time()

    def _value(self, param):
        h = (time.localtime().tm_hour + time.localtime().tm_min / 60)
        day = max(0.0, math.sin((h - 6) / 12 * math.pi))
        valve_on = any(self.coils.values())
        self.soil += 0.4 if valve_on else -0.01
        self.soil = min(max(self.soil, 12), 45)
        n = random.gauss
        return {
            "air_temp": 24 + 9 * day + n(0, 0.3),
            "air_humidity": 88 - 25 * day + n(0, 1),
            "light_lux": 40000 * day + n(0, 200),
            "soil_moisture": self.soil + n(0, 0.3),
            "soil_temp": 23 + 4 * day + n(0, 0.2),
            "soil_ec": 650 + n(0, 15),
            "soil_ph": 6.4 + n(0, 0.05),
            "tank_distance_cm": 40 + n(0, 0.5),
            "reservoir_ph": 6.0 + n(0, 0.05),
            "reservoir_temp": 26 + 2 * day + n(0, 0.2),
        }.get(param, 0)

    def read(self, unit, reg, count=1, fc=3):
        dev = self.by_unit.get(unit)
        if not dev:
            raise BusError(f"unit {unit}: no response (sim)")
        for r in dev["registers"]:
            if r["reg"] == reg:
                raw = int(round(self._value(r["param"]) / r.get("scale", 1)))
                if r.get("type", "u16") in ("u32", "u32_lowfirst"):
                    hi, lo = (raw >> 16) & 0xFFFF, raw & 0xFFFF
                    return [lo, hi] if r["type"] == "u32_lowfirst" else [hi, lo]
                return [raw & 0xFFFF]
        return [0] * count

    def write_register(self, unit, reg, value):
        pass

    def write_coil(self, unit, coil, on):
        self.coils[coil] = bool(on)

    def read_coils(self, unit, start, count):
        return [self.coils.get(start + i, False) for i in range(count)]

    def close(self):
        pass
