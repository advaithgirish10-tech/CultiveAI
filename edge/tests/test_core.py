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
