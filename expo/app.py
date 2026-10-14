"""Cultive expo app: rules -> WhatsApp ask -> approve -> valve -> measured outcome.

Runs beside the edge service and shares its SQLite database. It never touches the serial port:
valve commands go into the `commands` queue, which the edge service executes with its safety limits.

  python expo/app.py --config edge/config.yaml          (from the repo root)
  TV:        http://<pi-ip>:8080/
  Operator:  http://<pi-ip>:8080/operator
"""
import argparse
import json
import logging
import os
import sqlite3
import sys
import threading
import time

import yaml
from flask import Flask, jsonify, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import whatsapp  # noqa: E402

log = logging.getLogger("expo")

DEFAULTS = {"valve_channel": 0, "water_s": 20, "dry_below": 25.0, "cooldown_s": 120,
            "ask_expire_s": 300, "settle_s": 30, "zone_name": "Anthurium demo tray",
            # fan rule: humidity jumps above its recent normal (e.g. someone breathes on the sensor)
            "fan_channel": None, "fan_s": 20, "fan_rise": 8.0, "fan_cooldown_s": 40}

ASK_SCHEMA = """
CREATE TABLE IF NOT EXISTS asks (
  id INTEGER PRIMARY KEY, ts REAL, reason TEXT, message TEXT,
  moisture_before REAL, status TEXT,          -- pending | watering | done | ignored | expired
  resolved_ts REAL, resolved_by TEXT, moisture_after REAL, wa_result TEXT
);
"""


def load_env(path):
    """Minimal .env loader (KEY=value lines) so secrets stay out of git."""
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class Engine:
    def __init__(self, db_path, opts):
        self.db_path = db_path
        self.o = {**DEFAULTS, **(opts or {})}
        self.lock = threading.Lock()
        self.last_error = ""
        self.fan_until = 0.0
        self.fan_reason = ""
        with self.conn() as c:
            c.executescript(ASK_SCHEMA)

    def conn(self):
        c = sqlite3.connect(self.db_path, timeout=10)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    # ---------- reads ----------
    def latest(self, c, param, max_age=30):
        r = c.execute("SELECT value, ts FROM readings WHERE param=? ORDER BY id DESC LIMIT 1", (param,)).fetchone()
        if not r or time.time() - r[1] > max_age:
            return None
        return r[0]

    def current_ask(self, c):
        return c.execute("SELECT id, ts, reason, message, moisture_before, status, resolved_ts, resolved_by, "
                         "moisture_after, wa_result FROM asks ORDER BY id DESC LIMIT 1").fetchone()

    # ---------- actions ----------
    def create_ask(self, reason, force=False):
        with self.lock, self.conn() as c:
            last = self.current_ask(c)
            if last and last[5] in ("pending", "watering"):
                return last[0], "an ask is already open"
            m = self.latest(c, "soil_moisture")
            mtxt = f"{m:.0f}%" if m is not None else "unknown"
            msg = (f"{self.o['zone_name']}: pot moisture is {mtxt} (below {self.o['dry_below']:.0f}%).\n"
                   f"Recommendation: water for {self.o['water_s']} seconds.\nApprove?")
            cur = c.execute("INSERT INTO asks(ts,reason,message,moisture_before,status) VALUES (?,?,?,?,?)",
                            (time.time(), reason, msg, m, "pending"))
            ask_id = cur.lastrowid
        ok, res = whatsapp.send_ask(ask_id, msg)
        wa = "sent" if ok else f"not sent: {res}"
        if not ok:
            self.last_error = f"WhatsApp: {res}"
            log.warning("WhatsApp send failed: %s", res)
        with self.conn() as c:
            c.execute("UPDATE asks SET wa_result=? WHERE id=?", (wa, ask_id))
        log.info("ask #%d created (%s); whatsapp %s", ask_id, reason, wa)
        return ask_id, wa

    def resolve(self, verb, ask_id=None, by="dashboard"):
        with self.lock, self.conn() as c:
            row = (c.execute("SELECT id, status FROM asks WHERE id=?", (ask_id,)).fetchone() if ask_id
                   else c.execute("SELECT id, status FROM asks WHERE status='pending' ORDER BY id DESC LIMIT 1").fetchone())
            if not row:
                return False, "no open ask"
            if row[1] != "pending":
                return False, f"ask #{row[0]} is already {row[1]}"
            if verb == "approve":
                c.execute("INSERT INTO commands(ts,channel,state,duration_s,source,reason) VALUES (?,?,?,?,?,?)",
                          (time.time(), self.o["valve_channel"], "on", self.o["water_s"], by,
                           f"approved ask #{row[0]}"))
                c.execute("UPDATE asks SET status='watering', resolved_ts=?, resolved_by=? WHERE id=?",
                          (time.time(), by, row[0]))
            else:
                c.execute("UPDATE asks SET status='ignored', resolved_ts=?, resolved_by=? WHERE id=?",
                          (time.time(), by, row[0]))
        log.info("ask #%d %s by %s", row[0], verb, by)
        return True, f"ask #{row[0]} {verb}d"

    def stop_valve(self, by="operator"):
        with self.conn() as c:
            c.execute("INSERT INTO commands(ts,channel,state,duration_s,source,reason) VALUES (?,?,?,?,?,?)",
                      (time.time(), self.o["valve_channel"], "off", None, by, "manual stop"))
            # an open watering ask moves straight to "measuring" so the TV doesn't show a stale countdown
            c.execute("UPDATE asks SET resolved_ts=? WHERE status='watering'", (time.time() - self.o["water_s"],))

    def fan_on(self, reason, by="rule"):
        if self.o.get("fan_channel") is None:
            return False, "no fan configured"
        now = time.time()
        if now < self.fan_until:
            return False, "fan already running"
        with self.conn() as c:
            c.execute("INSERT INTO commands(ts,channel,state,duration_s,source,reason) VALUES (?,?,?,?,?,?)",
                      (now, self.o["fan_channel"], "on", self.o["fan_s"], by, reason))
        self.fan_until, self.fan_reason = now + self.o["fan_s"], reason
        log.info("fan on %ss (%s, by %s)", self.o["fan_s"], reason, by)
        return True, f"fan on for {self.o['fan_s']}s"

    def fan_tick(self, now):
        """Humidity well above its normal of the last few minutes -> run the fan once, then cool down."""
        if self.o.get("fan_channel") is None or now < self.fan_until + self.o["fan_cooldown_s"]:
            return
        with self.conn() as c:
            cur = self.latest(c, "air_humidity", max_age=15)
            base = [r[0] for r in c.execute(
                "SELECT value FROM readings WHERE param='air_humidity' AND ts BETWEEN ? AND ? ORDER BY value",
                (now - 180, now - 15)).fetchall()]
        if cur is None or len(base) < 5:
            return
        normal = base[len(base) // 2]
        if cur - normal >= self.o["fan_rise"]:
            self.fan_on(f"humidity {cur:.0f}%, {cur - normal:.0f} points above normal")

    # ---------- the rules loop ----------
    def tick(self):
        now = time.time()
        self.fan_tick(now)
        with self.conn() as c:
            ask = self.current_ask(c)
            m = self.latest(c, "soil_moisture")
        # close the loop: measure the outcome after watering + settle time
        if ask and ask[5] == "watering" and now > ask[6] + self.o["water_s"] + self.o["settle_s"]:
            with self.conn() as c:
                c.execute("UPDATE asks SET status='done', moisture_after=? WHERE id=?", (m, ask[0]))
                c.execute("INSERT INTO events(ts,kind,zone,note,source) VALUES (?,?,?,?,?)",
                          (now, "watering_outcome", self.o["zone_name"],
                           json.dumps({"ask": ask[0], "before": ask[4], "after": m}), "expo"))
            if ask[4] is not None and m is not None:
                whatsapp.send_text(f"Done. Pot moisture {ask[4]:.0f}% -> {m:.0f}% after "
                                   f"{self.o['water_s']}s of water. Logged.")
            return
        if ask and ask[5] == "pending" and now - ask[1] > self.o["ask_expire_s"]:
            with self.conn() as c:
                c.execute("UPDATE asks SET status='expired', resolved_ts=? WHERE id=?", (now, ask[0]))
            return
        if m is None or (ask and ask[5] in ("pending", "watering")):
            return
        if m < self.o["dry_below"] and (not ask or now - ask[1] > self.o["cooldown_s"]):
            self.create_ask("pot below threshold")

    def run(self, stop):
        while not stop.is_set():
            try:
                self.tick()
            except Exception as e:  # never let the loop die during the expo
                self.last_error = f"rules: {e}"
                log.exception("rules tick failed")
            stop.wait(2)

    # ---------- state for the screens ----------
    def state(self):
        now = time.time()
        with self.conn() as c:
            vals = {p: self.latest(c, p) for p in
                    ("soil_moisture", "soil_temp", "soil_ec", "air_temp", "air_humidity", "light_lux",
                     "tank_level_pct", "reservoir_ph")}
            hist = c.execute("SELECT ts, value FROM readings WHERE param='soil_moisture' AND ts>? ORDER BY ts",
                             (now - 900,)).fetchall()
            hum = c.execute("SELECT ts, value FROM readings WHERE param='air_humidity' AND ts>? ORDER BY ts",
                            (now - 900,)).fetchall()
            acts = c.execute("SELECT ts, name, state, duration_s, source, reason FROM actions "
                             "ORDER BY id DESC LIMIT 6").fetchall()
            asks = c.execute("SELECT id, ts, status, moisture_before, moisture_after, resolved_by, wa_result "
                             "FROM asks ORDER BY id DESC LIMIT 6").fetchall()
            ask = self.current_ask(c)
            dev = c.execute("SELECT device, status, MAX(ts) FROM device_events GROUP BY device").fetchall()
        phase, headline = "ok", "All good"
        if vals["soil_moisture"] is None:
            phase, headline = "offline", "Waiting for sensor data"
        elif ask and ask[5] == "pending":
            phase, headline = "asking", "Pot is dry. Asking the grower on WhatsApp"
        elif ask and ask[5] == "watering":
            left = ask[6] + self.o["water_s"] - now
            phase = "watering" if left > 0 else "measuring"
            headline = (f"Approved by {ask[7]}. Watering, {left:.0f}s left" if left > 0
                        else "Watering done. Measuring the result")
        elif ask and ask[5] == "done" and now - (ask[6] or 0) < self.o["water_s"] + self.o["settle_s"] + 90:
            phase = "done"
            b, a = ask[4], ask[8]
            headline = (f"Watered. Moisture {b:.0f}% to {a:.0f}%" if b is not None and a is not None and a - b >= 2
                        else "Watered and logged")
        elif vals["soil_moisture"] < self.o["dry_below"]:
            phase, headline = "dry", "Pot is drying"

        def thin(rows, n=120):
            step = max(1, len(rows) // n)
            return [[round(t), round(v, 1)] for t, v in rows[::step]]

        return {
            "now": now, "phase": phase, "headline": headline, "values": vals,
            "dry_below": self.o["dry_below"], "zone": self.o["zone_name"],
            "moisture_history": thin(hist), "humidity_history": thin(hum),
            "actions": [dict(ts=a[0], name=a[1], state=a[2], dur=a[3], source=a[4], reason=a[5]) for a in acts],
            "asks": [dict(id=a[0], ts=a[1], status=a[2], before=a[3], after=a[4], by=a[5], wa=a[6]) for a in asks],
            "offline_devices": [d[0] for d in dev if d[1] == "offline"],
            "ask": (dict(id=ask[0], ts=ask[1], message=ask[3], before=ask[4], status=ask[5],
                         resolved_ts=ask[6], by=ask[7], after=ask[8]) if ask else None),
            "water_s": self.o["water_s"], "settle_s": self.o["settle_s"],
            "fan": {"configured": self.o.get("fan_channel") is not None,
                    "on": now < self.fan_until, "left": max(0, round(self.fan_until - now)),
                    "reason": self.fan_reason},
            "whatsapp": "ready" if whatsapp.configured() else "not configured",
            "last_error": self.last_error,
        }


def create_app(engine):
    app = Flask(__name__, static_folder=os.path.join(HERE, "static"))

    @app.get("/")
    def tv():
        return send_from_directory(app.static_folder, "dashboard.html")

    @app.get("/phone")
    def phone():
        return send_from_directory(app.static_folder, "phone.html")

    @app.get("/operator")
    def operator():
        return send_from_directory(app.static_folder, "operator.html")

    @app.get("/api/state")
    def api_state():
        return jsonify(engine.state())

    @app.post("/api/ask")
    def api_ask():
        ask_id, info = engine.create_ask("manual trigger (operator)")
        return jsonify(ok=True, ask=ask_id, info=info)

    @app.post("/api/approve")
    def api_approve():
        ok, info = engine.resolve("approve", request.json.get("ask") if request.is_json else None, by="dashboard")
        return jsonify(ok=ok, info=info)

    @app.post("/api/ignore")
    def api_ignore():
        ok, info = engine.resolve("ignore", None, by="dashboard")
        return jsonify(ok=ok, info=info)

    @app.post("/api/stop")
    def api_stop():
        engine.stop_valve()
        return jsonify(ok=True)

    @app.post("/api/fan")
    def api_fan():
        ok, info = engine.fan_on("manual (operator)", by="operator")
        return jsonify(ok=ok, info=info)

    @app.post("/api/sim/dry")
    def api_sim_dry():
        os.makedirs(os.path.dirname(engine.db_path) or ".", exist_ok=True)
        open(os.path.join(os.path.dirname(engine.db_path) or ".", "sim_dry"), "w").close()
        return jsonify(ok=True)

    @app.post("/api/wa/test")
    def api_wa_test():
        ok, res = whatsapp.send_text("Cultive AI test message: the expo demo can reach your WhatsApp.")
        return jsonify(ok=ok, info=str(res)[:300])

    @app.get("/webhook")
    def wa_verify():
        if (request.args.get("hub.mode") == "subscribe"
                and request.args.get("hub.verify_token") == os.environ.get("WA_VERIFY_TOKEN")):
            return request.args.get("hub.challenge", "")
        return "forbidden", 403

    @app.post("/webhook")
    def wa_incoming():
        allowed = os.environ.get("WA_TO")
        for verb, ask_id, sender in whatsapp.parse_webhook(request.get_json(silent=True) or {}):
            if allowed and sender != allowed:
                log.warning("ignored reply from unknown number %s", sender)
                continue
            ok, info = engine.resolve(verb, ask_id, by="WhatsApp")
            if not ok:
                whatsapp.send_text(f"Nothing to do: {info}.")
        return "ok"

    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="edge/config.yaml")
    ap.add_argument("--env", default=".env")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env(args.env)
    cfg = yaml.safe_load(open(args.config))
    db = cfg.get("db_path", "data/cultive.db")
    if not os.path.isabs(db):
        db = os.path.join(os.path.dirname(os.path.abspath(args.config)), db)
    engine = Engine(db, cfg.get("expo"))
    stop = threading.Event()
    threading.Thread(target=engine.run, args=(stop,), daemon=True).start()
    log.info("whatsapp: %s | db: %s", "ready" if whatsapp.configured() else "NOT configured", db)
    create_app(engine).run(host="0.0.0.0", port=args.port, threaded=True)


if __name__ == "__main__":
    main()
