"""Chat answers for the demo phone. Rule-based on purpose: it reads the live sensor state and answers
in plain words. No internet, no LLM, nothing made up. If it doesn't know, it says what it can answer."""
import re

HELP = ("You can ask me about: soil moisture, temperature, humidity, light, EC, pH, the pump, "
        "the fan, the LED strip, a full status report, or what Cultive AI is. "
        "You can also say \"fan on\", \"fan off\", \"lights on\" or \"lights off\", "
        "and \"water it\" to ask for approval to water.")


def _n(v, d=0, unit=""):
    return "no reading" if v is None else f"{v:.{d}f}{unit}"


def _has(q, *words):
    return any(re.search(r"\b" + w, q) for w in words)


def answer(q, s, create_ask=None, controls=None):
    """q: the question; s: Engine.state() dict; create_ask: callable(reason) to open a watering ask."""
    q = (q or "").lower().strip()
    q = re.sub(r"cultive\s*ai", "cultive", q)   # the brand name shouldn't trigger the "is it AI?" answer
    v, D = s["values"], s.get("devices", {})
    m, dry = v.get("soil_moisture"), s["dry_below"]
    off = s.get("offline_devices") or []

    def moist():
        if m is None:
            return "I can't read the soil probe right now. Check its power and wires."
        word = "dry" if m < dry else ("very wet" if m > 70 else "fine")
        tail = f" It's below {dry:.0f}%, so I'll ask you before watering." if m < dry else \
               f" I'll ask you to water when it drops below {dry:.0f}%."
        return f"Pot moisture is {m:.0f}%, which is {word}.{tail}"

    def dev(k, name):
        d = D.get(k)
        if not d:
            return f"The {name} isn't connected in this demo."
        if k == "led":
            return f"The LED strip is {'on' if d['on'] else 'off'}."
        return f"The {name} is {'running, ' + str(d['left']) + 's left' if d['on'] else 'off'}."

    def last_water():
        done = [a for a in s.get("asks", []) if a.get("status") == "done" and a.get("before") is not None]
        if not done:
            return ""
        a = done[0]
        return f" Last watering took the pot from {a['before']:.0f}% to {_n(a.get('after'), 0, '%')}."

    if not q:
        return HELP
    if _has(q, "hi\\b", "hello", "hey\\b", "namaste", "good (morning|evening|afternoon)"):
        return "Hi! I'm watching the Anthurium demo tray. " + HELP
    # switch the fan / LED strip ("turn on the fan", "lights off"). Questions like "is the fan on?" fall through.
    controls = controls or {}
    question = re.match(r"(is|are|was|what|whats|what's|how|why|does|do|can you tell)\b", q)
    if not question and _has(q, "fan", "led", "lights?\\b", "strip", "lamp"):
        off = _has(q, "off", "stop", "disable", "kill", "shut")
        on = _has(q, "on\\b", "start", "enable", "run")
        if off or on:
            dev_key = "fan" if _has(q, "fan") else "led"
            fn = controls.get(f"{dev_key}_{'off' if off else 'on'}")
            if not fn:
                return "I can't switch that from here right now."
            ok, info = fn()
            name = "fan" if dev_key == "fan" else "LED strip"
            if not ok:
                return f"Couldn't switch the {name}: {info}."
            if dev_key == "fan" and not off:
                return "Fan on. It runs for 20 seconds, then stops by itself."
            return f"Done, the {name} is {'off' if off else 'on'}."
    if _has(q, "water (it|now|the|my)", "irrigate", "start watering", "give (it )?water"):
        if s.get("ask") and s["ask"]["status"] in ("pending", "watering"):
            return "There's already a watering request open. Tap Approve above to go ahead."
        if create_ask:
            create_ask("asked from chat")
            return f"{moist()} I've opened a watering request for {s['water_s']} seconds. Tap Approve to start."
        return moist()
    if _has(q, "status", "report", "summary", "everything", "how is", "how's", "overall", "update", "plant", "doing"):
        parts = [moist(),
                 f"Air is {_n(v.get('air_temp'), 1, '°C')} with {_n(v.get('air_humidity'), 0, '%')} humidity.",
                 f"Light is {_n(v.get('light_lux'), 0, ' lux')}.",
                 f"Soil EC {_n(v.get('soil_ec'), 0, ' µS/cm')}, pH {_n(v.get('soil_ph'), 1)}."]
        if off:
            parts.append("Offline right now: " + ", ".join(off) + ".")
        return " ".join(parts)
    if _has(q, "ai\\b", "artificial", "machine learning", "smart", "intelligen"):
        return ("Honestly, today it's rule-based automation: sensors, clear thresholds and your approval. "
                "The AI layer comes next: learning each crop's thresholds, predicting when a pot will dry out, "
                "spotting failures like a blocked dripper, and answering questions like this one in any language.")
    if _has(q, "price", "cost", "how much", "pricing", "subscription", "₹", "rupee"):
        return ("Setup is about ₹55,000 for the box and one zone, ₹14,000 per extra zone, then ₹1,999 a month. "
                "There's a 60-day pilot on one bay for ₹9,999.")
    if _has(q, "cultive", "who are you", "what are you", "about (you|cultive|this|the company)", "company", "what do you do", "polyhouse", "nursery"):
        return ("Cultive AI watches a polyhouse with soil and climate sensors, asks the grower on WhatsApp before it "
                "acts, waters only when they approve, then measures the result and logs it. Built for Kerala "
                "polyhouses and nurseries, starting with anthuriums in Thrissur.")
    if _has(q, "how (does|do) (it|this|you) work", "how it works", "sensor", "connected", "hardware", "raspberry", "pi\\b"):
        return ("A soil probe, an air sensor and a light sensor talk to a Raspberry Pi over RS485 every 2 seconds. "
                "The Pi runs the rules and switches a relay for the pump, fan and LED strip. "
                "Every reading and every action is saved as data.")
    if _has(q, "moist", "soil", "dry", "wet", "water", "thirst"):
        return moist() + last_water()
    if _has(q, "temp", "hot", "cold", "heat", "warm"):
        return f"Air temperature is {_n(v.get('air_temp'), 1, '°C')}."
    if _has(q, "humid", "rh\\b", "mist"):
        h = v.get("air_humidity")
        tip = "" if h is None else (" That's in the anthurium range of 70–85%." if 70 <= h <= 85 else
                                    " Anthuriums like 70–85%.")
        return f"Humidity is {_n(h, 0, '%')}.{tip}"
    if _has(q, "led", "strip", "lamp", "light", "lux", "sun", "bright", "dark"):
        return f"Light level is {_n(v.get('light_lux'), 0, ' lux')}. {dev('led', 'LED strip')}"
    if _has(q, "ec\\b", "fertili", "nutrient", "salt", "conductiv", "feed"):
        return (f"Soil EC is {_n(v.get('soil_ec'), 0, ' µS/cm')}. It shows how strong the fertiliser is in the pot. "
                "It only reads properly when the soil is moist.")
    if _has(q, "ph\\b", "acid", "alkalin"):
        return f"Soil pH is {_n(v.get('soil_ph'), 1)}. Anthuriums like about 5.5 to 6.5."
    if _has(q, "pump", "motor", "valve"):
        return dev("pump", "pump") + last_water()
    if _has(q, "fan", "air flow", "airflow", "vent"):
        return dev("fan", "fan") + " It runs by itself for 20 s when humidity jumps, like when someone breathes on the sensor."
    if _has(q, "thank", "great", "nice", "cool", "awesome", "ok\\b", "okay"):
        return "Happy to help. Ask me anything else about the tray."
    return "I didn't catch that. " + HELP
