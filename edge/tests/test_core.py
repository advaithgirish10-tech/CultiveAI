import os, sys, time, logging, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import yaml
from cultive_edge.devices import decode, read_device
from cultive_edge.bus import SimBus
from cultive_edge.store import Store
from cultive_edge.relay import RelayController

def test_decode():
    assert decode([0xFF9B], "s16") == -101
    assert decode([1, 2], "u32") == 65538
    assert decode([2, 1], "u32_lowfirst") == 65538

def test_devices_and_tank():
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml")))
    bus = SimBus(cfg["devices"])
    tank = next(d for d in cfg["devices"] if d["id"] == "tank-1")
    out = {p: v for p, v, _ in read_device(bus, tank)}
    assert 55 < out["tank_level_cm"] < 65 and "tank_level_pct" in out

# Relay safety is tested against a fixed channel map, independent of whatever config.yaml is wired for.
RELAYS = {"address": 10, "channels": {
    0: {"name": "valve", "max_on_s": 300},
    1: {"name": "drip", "max_on_s": 1800},
    2: {"name": "pump", "max_on_s": 1800, "interlock": {"param": "tank_level_pct", "min": 15}}}}


def test_relay_cap_and_autooff():
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml")))
    st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
    bus = SimBus(cfg["devices"])
    r = RelayController(bus, st, RELAYS, logging.getLogger())
    assert r.set(0, True, 9999) == "on for 300s"          # capped at max_on_s
    assert r.set(5, True, 10).startswith("rejected")      # unconfigured channel
    r.deadlines[0] = time.time() - 1
    r.enforce_deadlines()
    assert bus.coils[0] is False and 0 not in r.deadlines
    st.queue_command(1, "on", 5, "test", "q")
    r.process_commands()
    assert bus.coils[1] is True


def test_pump_interlock():
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml")))
    st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
    bus = SimBus(cfg["devices"])
    r = RelayController(bus, st, RELAYS, logging.getLogger())
    assert r.set(2, True, 60).startswith("rejected: interlock: no recent")   # no data = not safe
    now = time.time()
    st.add_readings([(now, "c", "s", "reservoir", "tank-1", "tank_level_pct", 10.0, "%")])
    assert "below" in r.set(2, True, 60)
    st.add_readings([(now, "c", "s", "reservoir", "tank-1", "tank_level_pct", 60.0, "%")])
    assert r.set(2, True, 60) == "on for 60s"
    st.add_readings([(now, "c", "s", "reservoir", "tank-1", "tank_level_pct", 12.0, "%")])
    r.enforce_deadlines()                                                      # tank dropped mid-run
    assert bus.coils[2] is False


def test_multibus_routes_by_address_and_dead_port_fails_cleanly():
    from cultive_edge.bus import BusError, DeadBus, MultiBus

    class Fake:
        def __init__(self, name):
            self.name, self.calls = name, []

        def read(self, unit, reg, count=1, fc=3):
            self.calls.append(unit)
            return [unit]

        def close(self):
            pass

    main, lux = Fake("main"), Fake("lux")
    mb = MultiBus(main, {1: lux})
    assert mb.read(1, 0) == [1] and lux.calls == [1]
    assert mb.read(4, 0) == [4] and main.calls == [4]

    mb2 = MultiBus(main, {1: DeadBus("/dev/x", "missing")})
    try:
        mb2.read(1, 0)
        assert False, "expected BusError"
    except BusError:
        pass
    mb.close()


def test_autobus_finds_devices_on_any_adapter():
    import logging as lg
    from cultive_edge.bus import AutoBus, BusError

    class Port:
        def __init__(self, units):
            self.units = set(units)

        def read(self, unit, reg, count=1, fc=3):
            if unit not in self.units:
                raise BusError("no answer")
            return [unit]

        def read_coils(self, unit, start, count):
            if unit not in self.units:
                raise BusError("no answer")
            return [False] * count

        def close(self):
            pass

    wiring = {"A": Port([2]), "B": Port([4]), "C": Port([1]), "D": Port([10])}   # shuffled sockets
    targets = [(4, 3, 0), (1, 3, 0), (2, 4, 1), (10, "coil", 0)]
    bus = AutoBus(list(wiring), lambda p: wiring[p], targets, lg.getLogger())
    assert bus.read(4, 0) == [4] and bus.read(1, 0) == [1] and bus.read(2, 1, 1, 4) == [2]
    assert bus.read_coils(10, 0, 1) == [False]
    # replug: probe moves from socket B to a new socket E
    wiring["B"].units.clear(); wiring["E"] = Port([4])
    bus.rediscover({4}, ports=["E"])
    assert bus.read(4, 0) == [4]
    bus.close()


def test_relay_switched_off_when_its_adapter_reappears():
    import logging as lg
    from cultive_edge.bus import AutoBus, BusError
    from cultive_edge.main import rescan

    class Relay:
        def __init__(self):
            self.present, self.coils = False, {0: True}       # board left the pump ON
        def read_coils(self, unit, start, count):
            if not self.present or unit != 10:
                raise BusError("no answer")
            return [self.coils.get(start, False)]
        def write_coil(self, unit, ch, on):
            if not self.present:
                raise BusError("no answer")
            self.coils[ch] = on
        def read(self, *a, **k):
            raise BusError("no answer")
        def close(self):
            pass

    board = Relay()
    bus = AutoBus(["R"], lambda p: board, [(10, "coil", 0)], lg.getLogger())
    st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
    relays = RelayController(bus, st, RELAYS, lg.getLogger())
    relays.all_off("service start")                            # fails: board not found yet
    assert board.coils[0] is True
    board.present = True                                       # adapter plugged back in
    rescan(bus, {"devices": [], "relays": RELAYS}, {}, relays, ports=["R"])
    assert board.coils[0] is False                             # pump switched off


def test_relay_found_again_after_moving_sockets():
    import logging as lg
    from cultive_edge.bus import AutoBus, BusError
    from cultive_edge.main import rescan

    class Port:
        def __init__(self, has_relay):
            self.has_relay, self.coils = has_relay, {}
        def read_coils(self, unit, start, count):
            if not self.has_relay:
                raise BusError("no answer")
            return [self.coils.get(start, False)]
        def write_coil(self, unit, ch, on):
            if not self.has_relay:
                raise BusError("no answer")
            self.coils[ch] = on
        def read(self, *a, **k):
            raise BusError("no answer")
        def close(self):
            pass

    ports = {"A": Port(True)}
    bus = AutoBus(["A"], lambda p: ports[p], [(10, "coil", 0)], lg.getLogger())
    st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
    relays = RelayController(bus, st, RELAYS, lg.getLogger())
    ports["A"].has_relay = False                       # unplugged from socket A ...
    ports["B"] = Port(True); ports["B"].coils[0] = True  # ... into socket B, pump channel on
    rescan(bus, {"devices": [], "relays": RELAYS}, {}, relays, ports=["B"])
    assert bus.routes[10] is ports["B"] and ports["B"].coils[0] is False
