# Expo demo: "a polyhouse on a table"

One potted anthurium, live sensors, a TV dashboard, and a valve that only opens when a person
taps **Approve** on WhatsApp. The loop: **sense → understand → ask → act → measure**.

```
sensors ─ RS485 ─ Pi: edge service ── SQLite ── expo app ──► TV  (http://<pi>:8080/)
                     (owns serial,      ▲          │  ▲      laptop (http://<pi>:8080/operator)
                      safety limits)    └─commands─┘  └──── WhatsApp webhook (via tunnel)
```

The expo app never touches the hardware. It queues valve commands; the edge service runs them
with its hard time caps. If the expo app crashes, the valve still closes on time.

## Try it now (no hardware)

```bash
cd CultiveAI
python3 -m venv edge/.venv && source edge/.venv/bin/activate
pip install -r edge/requirements.txt
(cd edge && python -m cultive_edge.main --simulate --interval 2) &
python expo/app.py --config edge/config.yaml
```
Open `http://localhost:8080/` (TV) and `http://localhost:8080/operator` (laptop).
Operator → **Simulator: dry the pot** → watch the ask → **Approve here** → valve runs 20 s → moisture rises.

## The table

| Part | Where | Note |
|---|---|---|
| Anthurium in a pot + 4-in-1 soil probe | Centre of a small tray | Probe fully in, lower half of the pot |
| MD02 air sensor + lux sensor | On a stick beside the pot | Where visitors can breathe on / cover them |
| Water bottle (2–5 L) | Raised 40–60 cm on a stand | Gravity feed |
| ¼" 12V solenoid valve | Between bottle and pot | **Outlet tube aimed at the top of the pot, next to the probe** so moisture rises within seconds |
| Ultrasonic (optional) | Above the bottle / a bucket, ≥30 cm from the water | Shows the level dropping |
| Pi + relay + 12V supply | In the box, behind the table | |
| TV (HDMI from laptop or Pi) | Facing the aisle | Full-screen the dashboard (F11) |

**Valve wiring:** 12V supply **+** → relay **CH1 COM**; relay **CH1 NO** → valve **+**; valve **−** → supply **−**.
Relay board power: 12V into its power terminal. Relay CH1 = `valve_channel: 0` in config.

**Test the flow first.** A ¼" valve on gravity can be slow [likely]. If 20 s gives barely a trickle,
raise the bottle, increase `water_s`, or put a 12V mini pump on the same relay channel instead.

**The trick that makes it repeatable:** a real pot doesn't dry out in minutes. Keep a cup of **dry
cocopeat**. To start a demo, move the probe into the dry cup → the screen goes amber → ask → approve →
water lands on the cup or the pot → moisture rises. Or just press **Send WhatsApp ask now**.
Bring 2–3 pots or cups and swap them when one is soaked. Put a tray or bucket under everything.

## WhatsApp setup (do it before the 14th)

1. **developers.facebook.com** → My Apps → Create app → type *Business* → add **WhatsApp**.
2. WhatsApp → **API Setup**: you get a free **test number**, its **Phone number ID**, and a
   **temporary access token** (expires in ~24 h; regenerate it on the expo morning).
3. In "To", **add your own phone number** and verify it with the code. Only verified numbers can
   receive messages from the test number (max 5).
4. Copy `.env.example` → `.env` and fill in `WA_TOKEN`, `WA_PHONE_ID`, `WA_TO`, `WA_VERIFY_TOKEN`.
5. **Webhook (for the Approve button):** Meta must reach the Pi over HTTPS. Use a free Cloudflare tunnel:
   ```bash
   # on the Pi, once
   curl -L -o cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64
   chmod +x cloudflared
   # every time
   ./cloudflared tunnel --url http://localhost:8080      # prints https://<random>.trycloudflare.com
   ```
   Meta → WhatsApp → **Configuration** → Webhook: Callback URL `https://<random>.trycloudflare.com/webhook`,
   Verify token = your `WA_VERIFY_TOKEN` → Verify and save → **subscribe to `messages`**.
   The random URL changes every time cloudflared restarts, so redo this step after any restart.
6. **From your phone, send "hi" to the test number.** Meta only allows button messages within 24 h of
   your last message to it. Do this every morning of the expo.
7. Operator panel → **Send WhatsApp test**. If it arrives, you're live.

If WhatsApp fails on the day, the demo still works: press **Approve here (fallback)** on the laptop.
Typing **yes** or **no** in the WhatsApp chat also works, as well as the buttons.

## Expo settings

In `edge/config.yaml` set `poll_interval_s: 2` (live feel), and tune the `expo:` block:
`dry_below` (when it asks), `water_s` (how long it waters), `cooldown_s`.

Run both as services so they restart by themselves:
```bash
sudo cp edge/systemd/cultive-edge.service expo/systemd/cultive-expo.service /etc/systemd/system/
sudo systemctl enable --now cultive-edge cultive-expo
```

## Day-of checklist

- [ ] Phone hotspot on, Pi connected (don't rely on venue WiFi)
- [ ] Regenerate the WhatsApp token → `.env` → `sudo systemctl restart cultive-expo`
- [ ] Start cloudflared, update the Meta webhook URL
- [ ] Send "hi" to the test number from your phone
- [ ] Operator → Send WhatsApp test ✔
- [ ] One full dry → ask → approve → water cycle before visitors arrive
- [ ] Water bottle full, bucket under the tray, spare dry cup ready
- [ ] Fallback video on the laptop (record it on the 14th)
