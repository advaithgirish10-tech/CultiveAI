"""RS485 Modbus RTU bus access, plus a simulated bus for working without hardware."""
import math
import os
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


def _call(fn, unit, *args, **kw):
    """Newer pymodbus raises on timeouts instead of returning an error; turn both into BusError."""
    from pymodbus.exceptions import ModbusException
    try:
        return _with_unit(fn, unit, *args, **kw)
    except ModbusException as e:
        raise BusError(f"unit {unit}: {e}") from e


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
        rr = _call(fn, unit, reg, count=count)
        if rr.isError():
            raise BusError(f"unit {unit} reg {reg:#06x}: {rr}")
        return list(rr.registers)

    def write_register(self, unit, reg, value):
        rr = _call(self.client.write_register, unit, reg, value)
        if rr.isError():
            raise BusError(f"unit {unit} write reg {reg:#06x}: {rr}")

    def write_coil(self, unit, coil, on):
        rr = _call(self.client.write_coil, unit, coil, bool(on))
        if rr.isError():
            raise BusError(f"unit {unit} coil {coil}: {rr}")

    def read_coils(self, unit, start, count):
        rr = _call(self.client.read_coils, unit, start, count=count)
        if rr.isError():
            raise BusError(f"unit {unit} coils: {rr}")
        return list(rr.bits[:count])

    def close(self):
        self.client.close()


class DeadBus:
    """Stands in for an adapter that isn't plugged in: every call fails like a silent device."""

    def __init__(self, port, why):
        self.port, self.why = port, why

    def _fail(self, unit, *_a, **_k):
        raise BusError(f"unit {unit}: port {self.port} unavailable ({self.why})")

    read = write_register = write_coil = read_coils = _fail

    def close(self):
        pass


class MultiBus:
    """Several RS485 adapters, one per port. Calls are routed by device address
    (addresses are unique across the whole site). Unlisted addresses use the main port."""

    def __init__(self, main, routes):
        self.main, self.routes = main, routes          # routes: {unit: bus}

    def _bus(self, unit):
        return self.routes.get(unit, self.main)

    def read(self, unit, *a, **k):
        return self._bus(unit).read(unit, *a, **k)

    def write_register(self, unit, *a, **k):
        return self._bus(unit).write_register(unit, *a, **k)

    def write_coil(self, unit, *a, **k):
        return self._bus(unit).write_coil(unit, *a, **k)

    def read_coils(self, unit, *a, **k):
        return self._bus(unit).read_coils(unit, *a, **k)

    def close(self):
        for b in {id(b): b for b in [self.main, *self.routes.values()]}.values():
            b.close()


class AutoBus(MultiBus):
    """Finds which adapter each device sits on, so any adapter can go in any USB socket.

    targets: list of (unit, kind, reg) where kind is 3/4 (register read) or "coil" (relay).
    Devices not found yet go to the first port and simply fail until rediscover() finds them."""

    def __init__(self, ports, open_port, targets, log):
        self.open_port, self.targets, self.log = open_port, targets, log
        self.buses = {}
        self.ports = list(ports)
        for p in self.ports:
            self._open(p)
        first = next(iter(self.buses.values()), None) or DeadBus("none", "no USB-RS485 adapters found")
        super().__init__(first, {})
        self.rediscover()

    def _open(self, p):
        if p in self.buses:
            return
        try:
            self.buses[p] = self.open_port(p)
        except BusError as e:
            self.log.warning("adapter %s could not be opened: %s", p, e)

    def _probe(self, bus, unit, kind, reg):
        try:
            if kind == "coil":
                bus.read_coils(unit, reg, 1)
            else:
                bus.read(unit, reg, 1, kind)
            return True
        except BusError:
            return False

    def rediscover(self, units=None, ports=None):
        """Probe every adapter for the given units (default: all not yet found)."""
        for p in ports or []:
            if p not in self.ports:
                self.ports.append(p)
            self._open(p)
        if self.main is not None and isinstance(self.main, DeadBus) and self.buses:
            self.main = next(iter(self.buses.values()))
        want = [t for t in self.targets if (units is None and t[0] not in self.routes) or (units and t[0] in units)]
        for unit, kind, reg in want:
            for p, bus in self.buses.items():
                if self._probe(bus, unit, kind, reg):
                    if self.routes.get(unit) is not bus:
                        self.log.info("found address %s on %s", unit, p)
                    self.routes[unit] = bus
                    break
            else:
                self.routes.pop(unit, None)
                self.log.warning("address %s not found on any adapter", unit)
        return dict(self.routes)

    def close(self):
        for b in self.buses.values():
            b.close()


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
        if os.path.exists("data/sim_dry"):  # expo operator panel: "simulate a dry pot"
            os.remove("data/sim_dry")
            self.soil = 15.0
        valve_on = any(self.coils.values())
        self.soil += 0.4 if valve_on else -0.005
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
