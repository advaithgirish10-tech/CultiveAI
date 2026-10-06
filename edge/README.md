# cultive-edge

Raspberry Pi 5 edge service for Cultive AI. It reads every RS485 Modbus sensor on one bus, stores
readings locally in SQLite (nothing is lost when the internet drops), runs relays with hard safety
limits, and uploads to the cloud when an upload URL is set.

```
sensors + relay board ── RS485 bus ── USB-RS485 ── Pi 5 ── SQLite ── (later) cloud ── WhatsApp
```

## Try it now, no hardware

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m cultive_edge.main --simulate --interval 5     # fake sensors, Ctrl+C to stop
python tools/status.py                                   # latest values
```

## Address plan (everything ends up at 9600 baud, 8N1)

| Device | Factory default (as documented) | Set to | How |
|---|---|---|---|
| MD02 temp/humidity | addr 1, 9600 | **2** | `setaddr.py md02` |
| WIN-SN-LUX-M light | addr 1, 9600 | **3** | DIP switch: 1+2 UP, 3+4 DOWN, press reset |
| Renke 4-in-1 soil | addr 1, **4800** | **4** | `setaddr.py renke` |
| URM15 ultrasonic | addr **15**, **19200** | **5** | `setaddr.py urm15`, then power-cycle |
| Waveshare 8ch relay | addr 1, 9600 | **10** | `setaddr.py relay`, then power-cycle |
| Renke water pH (reservoir) | addr 1, 4800 | **6** | `setaddr.py renke --to 6` |

Defaults come from vendor docs; your batch can differ. `tools/scan.py` tells you the truth.

## On-site day 1 (in this order)

**1. Pi setup** (do this at home if the Pi is with you)
```bash
sudo apt update && sudo apt install -y python3-venv git
cd ~ && git clone https://github.com/advaithgirish10-tech/CultiveAI.git && cd CultiveAI/edge
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
sudo usermod -aG dialout pi        # serial port permission, then log out and in
ls /dev/ttyUSB*                    # adapter should show as /dev/ttyUSB0
```

**2. Address each device ALONE on the bus.** One device connected at a time: 12V +, GND, A, B.
```bash
python tools/scan.py                                          # find it
python tools/setaddr.py md02  --from 1  --from-baud 9600  --to 2
python tools/setaddr.py renke --from 1  --from-baud 4800  --to 4
python tools/setaddr.py urm15 --from 15 --from-baud 19200 --to 5
python tools/setaddr.py relay --from 1  --from-baud 9600  --to 10
python tools/setaddr.py lux                                    # prints DIP instructions
```
Label each device with its new address on tape. You will forget.

**3. Chain them.** Daisy-chain A→A→A, B→B→B, GND shared. One 120Ω resistor across A–B at
each physical **end** of the cable (two total). Then:
```bash
python tools/scan.py --baud 9600        # expect 2, 3, 4, 5, 6, 10
python -m cultive_edge.main --interval 5 --cycles 3
```

**4. Relay bench test.** Test-tray valve on relay channel 1 (index 0). Drip (index 1) and submersible pump (index 2) only after the electrician wires the AUTO terminals. The pump refuses to run if the tank is below 15% or the tank reading is missing.
```bash
python -m cultive_edge.main --interval 30 &      # service owns the port
python tools/relay.py on 0 20 "bench test"        # valve opens, auto-closes after 20s
python tools/status.py                            # action logged
```

**5. Run it 24/7**
```bash
sudo cp systemd/cultive-edge.service /etc/systemd/system/
sudo systemctl enable --now cultive-edge
journalctl -u cultive-edge -f
```

## Must verify on site

- **Lux above 6,553 lux.** The datasheet says the value is lux × 10, but polyhouse midday is
  30–60k lux, which doesn't fit in one 16-bit register. Check a reading in direct sun. If it
  flatlines near 6553 or jumps around, change `type` in `config.yaml` to `u32` or `u32_lowfirst`.
- **MD02 stop bits.** Some batches want 8N2. If it won't answer, try `--stopbits 2`.
- **URM15 humidity rating is RH < 75%.** Don't mount it inside the humid polyhouse. Mount it over
  the water tank, where it's meant to be.
- **Tank height.** Set `tank_height_cm` = sensor face to tank bottom.
- **Soil probe.** Insert vertically, all pins fully in, soil pressed firm. Air gaps read low.

## Safety rules (built in)

- All relays OFF at service start and stop.
- Every ON has a deadline. A channel can't stay on past `max_on_s`, even if the command
  asks longer or the network dies.
- Every switch is logged to the `actions` table (source + reason). This is the start of the
  outcome dataset.
- Only the service touches the serial port; everything else queues commands in SQLite.

## Layout

```
cultive_edge/  bus.py (Modbus + simulator) · devices.py (decoding) · store.py (SQLite)
               relay.py (safety) · main.py (service)
tools/         scan.py · setaddr.py · relay.py · status.py
tests/         pytest
```
