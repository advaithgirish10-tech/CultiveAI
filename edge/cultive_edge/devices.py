"""Turn raw Modbus registers into readings."""

WORDS = {"u16": 1, "s16": 1, "u32": 2, "u32_lowfirst": 2}


def decode(regs, typ="u16"):
    if typ == "u16":
        return regs[0]
    if typ == "s16":
        v = regs[0]
        return v - 0x10000 if v & 0x8000 else v
    if typ == "u32":
        return (regs[0] << 16) | regs[1]
    if typ == "u32_lowfirst":
        return (regs[1] << 16) | regs[0]
    raise ValueError(f"unknown register type {typ}")


def read_device(bus, dev):
    """Return a list of (param, value, unit) for one device. Raises BusError if it doesn't answer."""
    out = []
    for r in dev["registers"]:
        typ = r.get("type", "u16")
        regs = bus.read(dev["address"], r["reg"], count=WORDS[typ], fc=r.get("fc", 3))
        raw = decode(regs, typ)
        if "invalid" in r and raw == r["invalid"]:
            continue  # sensor reports "no valid measurement"
        out.append((r["param"], round(raw * r.get("scale", 1), 3), r.get("unit", "")))

    # Derived: tank water level from ultrasonic distance
    if dev.get("tank_height_cm"):
        dist = next((v for p, v, _ in out if p == "tank_distance_cm"), None)
        if dist is not None:
            h = dev["tank_height_cm"]
            level = max(0.0, h - dist)
            out.append(("tank_level_cm", round(level, 1), "cm"))
            out.append(("tank_level_pct", round(100 * level / h, 1), "%"))
    return out
