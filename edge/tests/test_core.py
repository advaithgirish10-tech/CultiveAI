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

def test_relay_cap_and_autooff():
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml")))
    st = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
    bus = SimBus(cfg["devices"])
    r = RelayController(bus, st, cfg["relays"], logging.getLogger())
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
    r = RelayController(bus, st, cfg["relays"], logging.getLogger())
    assert r.set(2, True, 60).startswith("rejected: interlock: no recent")   # no data = not safe
    now = time.time()
    st.add_readings([(now, "c", "s", "reservoir", "tank-1", "tank_level_pct", 10.0, "%")])
    assert "below" in r.set(2, True, 60)
    st.add_readings([(now, "c", "s", "reservoir", "tank-1", "tank_level_pct", 60.0, "%")])
    assert r.set(2, True, 60) == "on for 60s"
    st.add_readings([(now, "c", "s", "reservoir", "tank-1", "tank_level_pct", 12.0, "%")])
    r.enforce_deadlines()                                                      # tank dropped mid-run
    assert bus.coils[2] is False
