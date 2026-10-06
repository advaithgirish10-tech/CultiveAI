"""Local SQLite store. Everything lands here first, so internet drops lose nothing."""
import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,            -- unix seconds
  customer TEXT, site TEXT, zone TEXT,
  device TEXT, param TEXT, value REAL, unit TEXT,
  synced INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_readings_unsynced ON readings(synced, id);
CREATE INDEX IF NOT EXISTS idx_readings_param_ts ON readings(param, ts);

-- device health transitions (online/offline), for sensor-uptime metrics
CREATE TABLE IF NOT EXISTS device_events (
  id INTEGER PRIMARY KEY, ts REAL, device TEXT, status TEXT, detail TEXT, synced INTEGER DEFAULT 0
);

-- every actuator action: who asked, why, what actually happened (the outcome dataset starts here)
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY, ts REAL, channel INTEGER, name TEXT, state TEXT,
  duration_s REAL, source TEXT, reason TEXT, synced INTEGER DEFAULT 0
);

-- things a person saw or did, logged from the dashboard ("fogger ran", "flood ran", "leaf tips brown")
-- this is the answer key for the test and the start of labelled outcome data
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, ts REAL, kind TEXT, zone TEXT, note TEXT, source TEXT, synced INTEGER DEFAULT 0
);

-- command queue: CLI, WhatsApp bot or cloud insert here; the edge service is the only bus owner
CREATE TABLE IF NOT EXISTS commands (
  id INTEGER PRIMARY KEY, ts REAL, channel INTEGER, state TEXT,
  duration_s REAL, source TEXT, reason TEXT, done INTEGER DEFAULT 0, result TEXT
);
"""


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def add_readings(self, rows):
        self.db.executemany(
            "INSERT INTO readings(ts,customer,site,zone,device,param,value,unit) VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        self.db.commit()

    def device_event(self, device, status, detail=""):
        self.db.execute("INSERT INTO device_events(ts,device,status,detail) VALUES (?,?,?,?)",
                        (time.time(), device, status, detail))
        self.db.commit()

    def log_action(self, channel, name, state, duration_s, source, reason):
        self.db.execute(
            "INSERT INTO actions(ts,channel,name,state,duration_s,source,reason) VALUES (?,?,?,?,?,?,?)",
            (time.time(), channel, name, state, duration_s, source, reason))
        self.db.commit()

    def queue_command(self, channel, state, duration_s=None, source="cli", reason=""):
        self.db.execute(
            "INSERT INTO commands(ts,channel,state,duration_s,source,reason) VALUES (?,?,?,?,?,?)",
            (time.time(), channel, state, duration_s, source, reason))
        self.db.commit()

    def pending_commands(self):
        return self.db.execute(
            "SELECT id,channel,state,duration_s,source,reason FROM commands WHERE done=0 ORDER BY id").fetchall()

    def finish_command(self, cid, result):
        self.db.execute("UPDATE commands SET done=1, result=? WHERE id=?", (result, cid))
        self.db.commit()

    def unsynced(self, table, limit):
        cur = self.db.execute(f"SELECT * FROM {table} WHERE synced=0 ORDER BY id LIMIT ?", (limit,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def mark_synced(self, table, ids):
        if ids:
            self.db.execute(f"UPDATE {table} SET synced=1 WHERE id IN ({','.join('?' * len(ids))})", ids)
            self.db.commit()

    def add_event(self, kind, zone, note, source="dashboard", ts=None):
        self.db.execute("INSERT INTO events(ts,kind,zone,note,source) VALUES (?,?,?,?,?)",
                        (ts or time.time(), kind, zone, note, source))
        self.db.commit()

    def latest_value(self, param, max_age_s=600):
        row = self.db.execute("SELECT value, ts FROM readings WHERE param=? ORDER BY id DESC LIMIT 1",
                              (param,)).fetchone()
        if not row or time.time() - row[1] > max_age_s:
            return None
        return row[0]

    def latest(self):
        return self.db.execute(
            "SELECT device, param, value, unit, MAX(ts) FROM readings GROUP BY device, param ORDER BY device"
        ).fetchall()
