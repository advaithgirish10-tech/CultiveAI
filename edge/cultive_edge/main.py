"""Edge service: poll sensors, store locally, run relay commands safely, upload when online.

Run:  python -m cultive_edge.main --config config.yaml [--simulate]
"""
import argparse
import glob
import json
import logging
import os
import signal
import time
import urllib.request

import yaml

from .bus import AutoBus, BusError, DeadBus, ModbusBus, MultiBus, SimBus
from .devices import read_device
from .relay import RelayController
from .store import Store

log = logging.getLogger("cultive")


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


def make_bus(cfg):
    if cfg.get("simulate"):
        log.warning("SIMULATION MODE — readings are fake")
        return SimBus(cfg["devices"])
    s = cfg["serial"]

    def open_port(port):
        return ModbusBus(port, s["baudrate"], s.get("parity", "N"), s.get("stopbits", 1), s.get("timeout", 1.0))

    if s.get("port") == "auto":
        return make_autobus(cfg, open_port)

    main = open_port(s["port"])
    extra = s.get("extra_ports") or []
    if not extra:
        return main
    routes = {}
    for x in extra:
        try:
            b = open_port(x["port"])
        except BusError as e:
            log.warning("extra port %s not available (%s); its devices will show offline", x["port"], e)
            b = DeadBus(x["port"], e)
        for unit in x.get("units", []):
            routes[int(unit)] = b
    return MultiBus(main, routes)


def list_adapters():
    return sorted(glob.glob("/dev/serial/by-path/*-usb-0:*")) or sorted(glob.glob("/dev/ttyUSB*"))


def make_autobus(cfg, open_port):
    targets = []
    for dev in cfg["devices"]:
        if dev.get("enabled", True) and dev.get("registers"):
            r = dev["registers"][0]
            targets.append((int(dev["address"]), r.get("fc", 3), r["reg"]))
    if cfg.get("relays"):
        targets.append((int(cfg["relays"]["address"]), "coil", 0))
    ports = list_adapters()
    log.info("auto-detect: %d adapter(s): %s", len(ports), ", ".join(ports) or "none")
    return AutoBus(ports, open_port, targets, log)


def upload(store, cfg):
    up = cfg.get("upload") or {}
    if not up.get("url"):
        return
    for table in ("readings", "device_events", "actions"):
        rows = store.unsynced(table, up.get("batch_size", 200))
        if not rows:
            continue
        body = json.dumps({"site": cfg["site"], "table": table, "rows": rows}).encode()
        req = urllib.request.Request(up["url"], data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {os.environ.get('CULTIVE_API_KEY') or up.get('api_key', '')}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if 200 <= resp.status < 300:
                    store.mark_synced(table, [r["id"] for r in rows])
        except Exception as e:  # offline is normal; data waits in SQLite
            log.info("upload skipped (%s): %s", table, e)
            return


def poll_once(bus, store, cfg, health):
    site = cfg["site"]
    now = time.time()
    rows = []
    for dev in cfg["devices"]:
        if not dev.get("enabled", True):
            continue
        try:
            for param, value, unit in read_device(bus, dev):
                rows.append((now, site["customer"], site["site"], dev.get("zone"), dev["id"], param, value, unit))
            if health.get(dev["id"], 0) >= cfg.get("offline_after_failures", 3):
                store.device_event(dev["id"], "online")
                log.info("%s back online", dev["id"])
            health[dev["id"]] = 0
        except BusError as e:
            health[dev["id"]] = health.get(dev["id"], 0) + 1
            log.warning("%s read failed (%d in a row): %s", dev["id"], health[dev["id"]], e)
            if health[dev["id"]] == cfg.get("offline_after_failures", 3):
                store.device_event(dev["id"], "offline", str(e))
    if rows:
        store.add_readings(rows)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--interval", type=float, help="override poll interval (seconds)")
    ap.add_argument("--cycles", type=int, default=0, help="stop after N polls (0 = run forever)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    if args.simulate:
        cfg["simulate"] = True
    interval = args.interval or cfg.get("poll_interval_s", 60)

    store = Store(cfg.get("db_path", "data/cultive.db"))
    bus = make_bus(cfg)
    relays = RelayController(bus, store, cfg["relays"], log) if cfg.get("relays") else None
    if relays:
        relays.all_off("service start")

    running = {"on": True}
    signal.signal(signal.SIGTERM, lambda *_: running.update(on=False))
    signal.signal(signal.SIGINT, lambda *_: running.update(on=False))

    health, cycles, next_poll, next_scan = {}, 0, 0.0, time.time() + 30
    try:
        while running["on"]:
            if relays:
                relays.process_commands()
                relays.enforce_deadlines()
            if time.time() >= next_poll:
                rows = poll_once(bus, store, cfg, health)
                log.info("polled: %s", ", ".join(f"{p}={v}{u}" for *_, p, v, u in rows) or "no data")
                upload(store, cfg)
                next_poll = time.time() + interval
                cycles += 1
                if args.cycles and cycles >= args.cycles:
                    break
            if isinstance(bus, AutoBus) and time.time() >= next_scan:
                # devices offline or never found: look again on every adapter (handles replugging)
                limit = cfg.get("offline_after_failures", 3)
                lost = {int(d["address"]) for d in cfg["devices"] if health.get(d["id"], 0) >= limit}
                lost |= {t[0] for t in bus.targets if t[0] not in bus.routes}
                if lost:
                    bus.rediscover(lost, ports=list_adapters())
                next_scan = time.time() + 30
            time.sleep(1)
    finally:
        if relays:
            relays.all_off("service stop")
        bus.close()
        log.info("stopped")


if __name__ == "__main__":
    main()
