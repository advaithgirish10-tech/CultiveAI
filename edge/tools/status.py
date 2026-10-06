"""Print the latest value of every parameter, recent device health and recent actions."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yaml  # noqa: E402

from cultive_edge.store import Store  # noqa: E402


def t(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%d %b %H:%M:%S")


cfg = yaml.safe_load(open(os.environ.get("CULTIVE_CONFIG", "config.yaml")))
s = Store(cfg.get("db_path", "data/cultive.db"))
print("LATEST READINGS")
for device, param, value, unit, ts in s.latest():
    print(f"  {device:10} {param:18} {value:>10} {unit:7} {t(ts)}")
print("\nDEVICE EVENTS (last 10)")
for ts, dev, st, d in s.db.execute("SELECT ts,device,status,detail FROM device_events ORDER BY id DESC LIMIT 10"):
    print(f"  {t(ts)} {dev:10} {st:8} {d[:60]}")
print("\nACTIONS (last 10)")
for ts, name, st, dur, src, why in s.db.execute(
        "SELECT ts,name,state,duration_s,source,reason FROM actions ORDER BY id DESC LIMIT 10"):
    print(f"  {t(ts)} {name:8} {st:4} {dur or '':>6} {src:7} {why}")
n = s.db.execute("SELECT COUNT(*), SUM(synced=0) FROM readings").fetchone()
print(f"\n{n[0]} readings stored, {n[1] or 0} not yet uploaded")
