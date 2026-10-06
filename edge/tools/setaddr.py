"""Give one device a new Modbus address and move it to 9600 baud.
Connect ONLY that device to the bus while running this.

  python tools/setaddr.py md02  --from 1  --from-baud 9600  --to 2
  python tools/setaddr.py renke --from 1  --from-baud 4800  --to 4
  python tools/setaddr.py urm15 --from 15 --from-baud 19200 --to 5     # then power-cycle it
  python tools/setaddr.py relay --from 1  --from-baud 9600  --to 10    # then power-cycle it
  python tools/setaddr.py lux   (prints DIP-switch instructions; no software setting)
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cultive_edge.bus import BusError, ModbusBus  # noqa: E402

# addr_reg, baud_reg, baud_codes, needs power-cycle, register used to verify (fc, reg)
MODELS = {
    "renke": dict(addr=0x07D0, baud=0x07D1, codes={2400: 0, 4800: 1, 9600: 2}, restart=False, verify=(3, 0x0000)),
    "md02":  dict(addr=0x0101, baud=0x0102, codes={9600: 0, 14400: 1, 19200: 2}, restart=False, verify=(4, 0x0001)),
    "urm15": dict(addr=0x0002, baud=0x0003, codes={2400: 1, 9600: 3, 14400: 4, 19200: 5}, restart=True,
                  verify=(3, 0x0005)),
    "relay": dict(addr=0x4000, baud=None, codes={}, restart=True, verify=(1, 0), broadcast=True),
}

LUX_HELP = """WIN-SN-LUX-M sets its address with the 4-position DIP switch (switch 1 = LSB, switch 4 = MSB).
Address 3 = switches 1 and 2 UP, 3 and 4 DOWN.  Baud: leave the baud DIP switches DOWN for 9600.
Press the reset button after changing switches. Then verify with:
  python tools/scan.py --baud 9600 --max 15"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model", choices=list(MODELS) + ["lux"])
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--from", dest="src", type=int)
    ap.add_argument("--from-baud", type=int)
    ap.add_argument("--to", type=int)
    ap.add_argument("--to-baud", type=int, default=9600)
    ap.add_argument("--stopbits", type=int, default=1, help="some MD02 batches want 2")
    args = ap.parse_args()

    if args.model == "lux":
        print(LUX_HELP)
        return
    if None in (args.src, args.from_baud, args.to):
        ap.error("--from, --from-baud and --to are required")

    m = MODELS[args.model]
    bus = ModbusBus(args.port, args.from_baud, "N", args.stopbits, timeout=1.0)
    fc, vreg = m["verify"]

    def probe(b, addr):
        return b.read_coils(addr, 0, 8) if fc == 1 else b.read(addr, vreg, 1, fc)

    try:
        print("current reading:", probe(bus, args.src))
    except BusError as e:
        sys.exit(f"Device not answering at address {args.src} / {args.from_baud} baud: {e}\n"
                 "Run tools/scan.py first to find it.")

    # Address first (at the old baud), then baud. Renke/MD02 apply changes instantly, so the baud
    # write must go to the NEW address; URM15/relay only apply after a power-cycle, so use the old one.
    if m.get("broadcast"):
        # Waveshare relay takes its new address via broadcast (unit 0); broadcasts get no reply.
        try:
            bus.write_register(0, m["addr"], args.to)
        except BusError:
            pass
    else:
        bus.write_register(args.src, m["addr"], args.to)
    if m["baud"] is not None and args.to_baud != args.from_baud:
        code = m["codes"].get(args.to_baud)
        if code is None:
            sys.exit(f"{args.model} doesn't support {args.to_baud} baud")
        time.sleep(0.3)
        bus.write_register(args.src if m["restart"] else args.to, m["baud"], code)
        print(f"baud set to {args.to_baud}")
    print(f"address set to {args.to}")
    bus.close()

    if m["restart"]:
        print("\n>>> Power-cycle the device now (unplug 12V for 3s), then press Enter.")
        input()
    else:
        time.sleep(1)

    bus = ModbusBus(args.port, args.to_baud, "N", args.stopbits, timeout=1.0)
    try:
        print(f"VERIFIED: address {args.to} @ {args.to_baud} ->", probe(bus, args.to))
    except BusError as e:
        print(f"Could not verify ({e}). Run: python tools/scan.py --baud {args.to_baud}")
    bus.close()


if __name__ == "__main__":
    main()
