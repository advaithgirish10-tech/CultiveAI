"""Queue a relay command for the running edge service (it owns the serial port).

  python tools/relay.py on 0 60 "bench test"     # channel 0 on for 60s (capped by max_on_s)
  python tools/relay.py off 0
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yaml  # noqa: E402

from cultive_edge.store import Store  # noqa: E402


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("on", "off"):
        sys.exit(__doc__)
    state, ch = sys.argv[1], int(sys.argv[2])
    dur = float(sys.argv[3]) if state == "on" and len(sys.argv) > 3 else None
    reason = sys.argv[4] if len(sys.argv) > 4 else "manual"
    cfg = yaml.safe_load(open(os.environ.get("CULTIVE_CONFIG", "config.yaml")))
    store = Store(cfg.get("db_path", "data/cultive.db"))
    store.queue_command(ch, state, dur, "cli", reason)
    print("queued; waiting for the service to run it...")
    for _ in range(10):
        time.sleep(1)
        row = store.db.execute("SELECT done, result FROM commands ORDER BY id DESC LIMIT 1").fetchone()
        if row and row[0]:
            print("result:", row[1])
            return
    print("not picked up yet — is the service running? (sudo systemctl status cultive-edge)")


if __name__ == "__main__":
    main()
