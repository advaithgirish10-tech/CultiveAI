"""Relay control with hard safety limits.

Rules that hold no matter who sends the command:
  * every channel is forced OFF when the service starts and when it stops
  * every ON has a deadline; the channel is switched OFF at the deadline even if nobody asks
  * no ON may exceed the channel's max_on_s from config
  * every switch is logged to the actions table
  * a channel with an interlock (e.g. pump needs tank_level_pct >= 15) refuses to start, and is
    stopped while running, if the reading is too low OR missing/stale (no data = not safe)
"""
import time

from .bus import BusError


class RelayController:
    def __init__(self, bus, store, cfg, log):
        self.bus, self.store, self.log = bus, store, log
        self.unit = cfg["address"]
        self.channels = {int(k): v for k, v in cfg["channels"].items()}
        self.deadlines = {}  # channel -> unix time to switch off

    def all_off(self, reason):
        for ch in range(8):
            try:
                self.bus.write_coil(self.unit, ch, False)
            except BusError as e:
                self.log.error("relay all_off ch%d failed: %s", ch, e)
        for ch in list(self.deadlines):
            self.store.log_action(ch, self.channels.get(ch, {}).get("name", f"ch{ch}"), "off", None, "system", reason)
        self.deadlines.clear()

    def interlock_problem(self, spec):
        il = spec.get("interlock")
        if not il:
            return None
        v = self.store.latest_value(il["param"])
        if v is None:
            return f"interlock: no recent {il['param']} reading"
        if v < il["min"]:
            return f"interlock: {il['param']}={v} below {il['min']}"
        return None

    def set(self, ch, on, duration_s=None, source="cli", reason=""):
        if ch not in self.channels:
            return f"rejected: channel {ch} not configured"
        spec = self.channels[ch]
        if on:
            problem = self.interlock_problem(spec)
            if problem:
                self.log.warning("%s refused: %s", spec["name"], problem)
                return f"rejected: {problem}"
            cap = spec["max_on_s"]
            duration_s = min(float(duration_s or cap), cap)
            self.bus.write_coil(self.unit, ch, True)
            self.deadlines[ch] = time.time() + duration_s
            self.store.log_action(ch, spec["name"], "on", duration_s, source, reason)
            self.log.info("%s ON for %.0fs (%s: %s)", spec["name"], duration_s, source, reason)
            return f"on for {duration_s:.0f}s"
        self.bus.write_coil(self.unit, ch, False)
        self.deadlines.pop(ch, None)
        self.store.log_action(ch, spec["name"], "off", None, source, reason)
        self.log.info("%s OFF (%s: %s)", spec["name"], source, reason)
        return "off"

    def enforce_deadlines(self):
        now = time.time()
        for ch, dl in list(self.deadlines.items()):
            problem = self.interlock_problem(self.channels[ch])
            if problem:
                try:
                    self.set(ch, False, source="system", reason=problem)
                except BusError as e:
                    self.log.error("interlock stop ch%d failed, will retry: %s", ch, e)
                continue
            if now >= dl:
                try:
                    self.set(ch, False, source="system", reason="max on-time reached")
                except BusError as e:
                    # keep the deadline so we retry next tick
                    self.log.error("auto-off ch%d failed, will retry: %s", ch, e)

    def process_commands(self):
        for cid, ch, state, dur, source, reason in self.store.pending_commands():
            try:
                result = self.set(int(ch), state == "on", dur, source or "queue", reason or "")
            except BusError as e:
                result = f"error: {e}"
            self.store.finish_command(cid, result)
