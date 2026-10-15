# CultiveAI: context for Claude

Read this first. It is the shared brief for Advaith and Adit (co-founders) and any Claude session working in this repo.

## What Cultive is
Agri IoT + WhatsApp for polyhouse/nursery growers. Sensors on RS485 → Raspberry Pi → (later) cloud → grower approves actions on WhatsApp.
Beta site: family polyhouse in Trissur, Kerala. Potted anthuriums on ebb-and-flow trays. The test bed is **one tray**, not the whole polyhouse.

**Who controls what at the site:**
- **Cultive:** water only (test-tray valve, drip, pump).
- **Agrowtrack** (a competitor, already installed): misting, top vent fan, one other fan, lights. Never wire our relays to these.
- **NIDO ONE V2:** fertigation. Read-only, we never control it.
- **440V panel:** all wiring is done by the contractor's electrician. Our relay goes only into AUTO terminals.

## Repo layout
- `edge/`: Pi service. It polls Modbus sensors, stores readings in SQLite (WAL), and runs relays with safety limits.
  - `cultive_edge/bus.py`: ModbusBus (pymodbus 3.x) and SimBus (simulator, `data/sim_dry` flag file forces dry soil)
  - `cultive_edge/devices.py`: register decoding and tank level
  - `cultive_edge/store.py`: tables readings, device_events, actions, events, commands
  - `cultive_edge/relay.py`: all OFF at start/stop, max_on_s caps, deadlines, pump interlock (tank ≥ 15%; missing/stale data counts as unsafe)
  - `cultive_edge/main.py`: poll loop. Flags: `--simulate --interval N --cycles N --config`
  - `tools/`: `scan.py`, `setaddr.py` (one device at a time), `peek.py` (raw registers), `relay.py` (queues a command), `status.py`
  - `config.yaml`: devices, relays and the `expo:` block. Devices with `enabled: false` are skipped
- `expo/`: demo app for the 16 Oct 2026 agri-tech expo. Flask on :8080. It never touches serial; it queues commands to the edge service.
  - `/` TV dashboard, `/operator` laptop panel, `/webhook` for WhatsApp Cloud API buttons (approve:/ignore:)
  - Rules: pot moisture < `dry_below` (25%) → WhatsApp ask → approve → valve channel 0 for `water_s` (20 s) → measure the rise
  - Env (`.env`, git-ignored): `WA_TOKEN`, `WA_PHONE_ID`, `WA_TO`, `WA_VERIFY_TOKEN`

## Hardware and bus addresses (all 9600 8N1, one RS485 bus)
| Device | Address | Notes |
|---|---|---|
| MD02 (SHT20) air temperature/humidity | 2 | input regs 1–2, fc4; some batches need 2 stop bits |
| Wittelb WIN-SN-LUX-M lux | 1 | factory address 1 @ 9600, jumper-set. Reg 0 = plain lux (scale 1), reg 2 = 65000 range. **Also streams ASCII lux text ~10×/s on RS485**, which can garble a shared bus → give it its own adapter via `serial.extra_ports` in config.yaml (routing by address) |
| Renke 4-in-1 soil probe | 4 | factory default 1 @ 4800. Regs 0–3: moisture ×0.1, temp s16 ×0.1, EC, pH ×0.1 |
| DFRobot URM15 ultrasonic | 5 | factory default 15 @ 19200. Minimum range 30 cm, rated RH < 75%. Disabled for the expo |
| Renke water pH | 6 | disabled, stays at the polyhouse |
| Waveshare 8-ch Modbus relay | 10 | coils 0–7. CH1 on the board = channel 0 in code |
Pi 5, USB-RS485 adapter on /dev/ttyUSB0, 12V 5A supply, ¼" 12V solenoid valve, 120Ω resistors.

## Expo demo (16 Oct 2026)
Tabletop: potted anthurium plus soil probe, MD02 and lux on a stick, bottle → valve → pot, TV dashboard, laptop operator panel.
Audience: government, nurseries, polyhouses. Fallback if WhatsApp fails: "Approve here" on the operator panel.
Plan: Sat 10 Pi + addressing + chain + valve · Mon 12 WhatsApp (Meta test number + cloudflared tunnel) · Tue 13 poster/QR · Wed 14 rehearsal + fallback video · Thu 15 pack.
Known risks: gravity flow through the ¼" valve may only trickle (backup: 12V mini pump on the same relay channel); the WhatsApp temp token lasts ~24 h; the cloudflared URL changes on every restart.

## Status (13 Oct 2026)
- Pi 5 re-flashed 11 Oct (Raspberry Pi OS Lite 64-bit, Debian 13 Trixie, Python 3.13, pymodbus 3.15); hostname `cultive`, user `pi`; repo cloned; simulator + dashboard verified.
- All 4 devices addressed and verified alone on the bus (addresses live inside the devices, not on the Pi): lux 1, MD02 2, soil probe 4, relay 10. All at 9600.
- Lux and MD02 stream ASCII text on RS485, so every device has its own CH340 adapter (4 adapters: probe 4, lux 1, MD02 2, relay 10). `serial.port: auto` finds each device on whichever adapter/socket it is in at startup, and re-searches missing devices every 30 s. Verified 13 Oct: all four answer.
- Expo uses the mini PUMP on relay CH1 (channel 0, 10 s per approval) and a mini FAN on CH2 (channel 1). Fan rule: humidity >= 8 points above its 3-min normal (someone breathes on the MD02) -> fan 20 s, 40 s cooldown. No tank sensor at the expo: bucket must stay full. Relay safety tests use their own channel map, not config.yaml.
- Demo phone (14 Oct): the TV shows a phone-style chat panel only while a watering ask is open (Approve/Ignore work there), then the result, then it slides away. `/phone` is the same chat for a real phone on the same WiFi. Labelled "demo view", no WhatsApp branding. `poll_interval_s: 2` for the expo (set back to 60 after).
- LED strip (15 Oct): relay CH3 = channel 2, wired to the NC contact (relay ON = strip OFF). Torch on the lux sensor (>= `light_bright_lux`, 1500) -> strip off 10 s, kept off while it stays bright. Pump tested in water 15 Oct: works.
- 16 Oct: TV dashboard squeezes left while the phone is open (nothing hidden); phone has a minimise button (comes back for the next ask, or via the 'Grower's phone' pill). Operator panel: 'Pump on 10 s (manual)' (`/api/pump`, capped 30 s). Auto-start (systemd) installed and verified on the Pi.
- 16 Oct (later): water_s 4 s per approval (also the manual pump). Operator panel has LED strip ON/OFF (OFF holds relay CH3 on for up to 1 h; LED channel cap raised to 3600 s since relay ON only darkens the strip). TV shows soil EC and pH tiles, plus a Devices tile (pump/fan/LED on-off, read from the relay action log). Operator panel shows the same. TV restyled 16 Oct to a light theme (leaf logo, action banner, 0-100 moisture chart with threshold, icon tiles, phone frame). Operator panel restyled to match (sidebar, status cards, icon buttons, coloured status pills). Chat on the demo phone (TV + /phone): `expo/assistant.py` answers from live readings, rule-based, offline; "water it" opens an ask; "fan on/off", "lights on/off" switch the fan and LED strip (pump still needs Approve).
- Next: rehearsal. After the expo: real WhatsApp, poll_interval_s back to 60, relay hardware timed-off safety.

## Later (after the expo)
Cloud: Supabase Postgres, Mumbai region (no TimescaleDB, use partitions/rollups). Supabase Auth + RLS, Edge Functions, the Pi pulls commands (no open ports), Vercel dashboard. Multi-tenant org → site → zone → device.

## Rules for any Claude working here
- Never `git push -f`, `git reset --hard`, or rewrite history on main. If a push is rejected, stop and ask.
- Never commit `.env`, `data/`, `*.db`, `.venv`.
- Only the edge service owns the serial port. Everything else queues commands.
- Keep relay safety intact: caps, deadlines, the interlock, all-off at start/stop. Run `pytest edge/tests expo/tests` before committing.
- Advaith isn't a strong coder. Explain simply and give exact commands, and say whether each one runs on the **Mac** or the **Pi**.
