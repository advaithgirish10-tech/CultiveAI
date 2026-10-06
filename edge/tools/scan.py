"""Find what's on the bus. Connect ONE device at a time when using this on new hardware.

  python tools/scan.py                       # try common bauds, addresses 1-20
  python tools/scan.py --baud 9600 --max 247 # full address sweep at one baud
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cultive_edge.bus import BusError, ModbusBus  # noqa: E402

PROBES = [(3, 0x0000), (4, 0x0001), (3, 0x0005), (3, 0x4000)]  # covers Renke/lux, MD02, URM15, relay


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, action="append", help="repeatable; default 4800,9600,19200")
    ap.add_argument("--max", type=int, default=20, help="highest address to try")
    ap.add_argument("--stopbits", type=int, default=1)
    args = ap.parse_args()

    found = []
    for baud in args.baud or [4800, 9600, 19200]:
        print(f"-- {baud} baud --")
        bus = ModbusBus(args.port, baud, "N", args.stopbits, timeout=0.15)
        for addr in range(1, args.max + 1):
            for fc, reg in PROBES:
                try:
                    regs = bus.read(addr, reg, 1, fc)
                    print(f"  FOUND address {addr} @ {baud} (fc{fc} reg {reg:#06x} = {regs[0]})")
                    found.append((addr, baud))
                    break
                except BusError as e:
                    if "Exception Response" in str(e) or "ExceptionResponse" in str(e):
                        # device answered with a Modbus error -> it exists, just not that register
                        print(f"  FOUND address {addr} @ {baud} (replied with exception on fc{fc} {reg:#06x})")
                        found.append((addr, baud))
                        break
        bus.close()
    print("\nResult:", found or "nothing answered — check A/B wiring (try swapping), power, and GND")


if __name__ == "__main__":
    main()
