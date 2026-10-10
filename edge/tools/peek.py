"""Read raw registers from one device, repeatedly. Use it to sanity-check a sensor.

  python tools/peek.py 1                 # address 1, holding regs 0-3, every second
  python tools/peek.py 2 --fc 4 --start 1 --count 2
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cultive_edge.bus import BusError, ModbusBus  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("address", type=int)
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--fc", type=int, default=3, choices=[3, 4])
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--count", type=int, default=4)
    ap.add_argument("--stopbits", type=int, default=1)
    args = ap.parse_args()
    bus = ModbusBus(args.port, args.baud, "N", args.stopbits, timeout=0.5)
    print("Ctrl+C to stop")
    try:
        while True:
            try:
                regs = bus.read(args.address, args.start, args.count, args.fc)
                print("  ".join(f"r{args.start + i}={v}" for i, v in enumerate(regs)))
            except BusError as e:
                print("no answer:", e)
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
