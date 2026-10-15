import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import whatsapp

def test_send_ask_payload(monkeypatch):
    monkeypatch.setenv("WA_TOKEN", "t"); monkeypatch.setenv("WA_PHONE_ID", "123"); monkeypatch.setenv("WA_TO", "919")
    seen = {}
    class R:
        def __enter__(s): return s
        def __exit__(s, *a): pass
        def read(s): return b'{"messages":[{"id":"x"}]}'
    def fake(req, timeout):
        seen["url"] = req.full_url; seen["body"] = json.loads(req.data); seen["auth"] = req.headers["Authorization"]
        return R()
    monkeypatch.setattr(whatsapp.urllib.request, "urlopen", fake)
    ok, _ = whatsapp.send_ask(7, "Water?")
    assert ok and seen["url"].endswith("/123/messages") and seen["auth"] == "Bearer t"
    btns = seen["body"]["interactive"]["action"]["buttons"]
    assert [b["reply"]["id"] for b in btns] == ["approve:7", "ignore:7"]
    assert all(len(b["reply"]["title"]) <= 20 for b in btns)   # Meta limit

def test_parse():
    body = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "9", "type": "interactive", "interactive": {"button_reply": {"id": "ignore:3"}}},
        {"from": "9", "type": "text", "text": {"body": " Approve "}},
        {"from": "9", "type": "text", "text": {"body": "hello"}}]}}]}]}
    assert list(whatsapp.parse_webhook(body)) == [("ignore", 3, "9"), ("approve", None, "9")]
    assert list(whatsapp.parse_webhook({"entry": [{"changes": [{"value": {"statuses": []}}]}]})) == []


def test_fan_rule_triggers_on_humidity_jump(tmp_path):
    import time
    import app as expo_app
    sys_path_edge = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "edge")
    sys.path.insert(0, sys_path_edge)
    from cultive_edge.store import Store
    db = str(tmp_path / "t.db")
    st = Store(db)
    now = time.time()
    st.add_readings([(now - 120 + i * 10, "c", "s", "z", "air-1", "air_humidity", 44.0, "%") for i in range(10)])
    eng = expo_app.Engine(db, {"fan_channel": 1, "fan_s": 20, "fan_rise": 8})
    eng.fan_tick(time.time())
    assert eng.fan_until == 0                                       # normal humidity: no fan
    st.add_readings([(time.time(), "c", "s", "z", "air-1", "air_humidity", 80.0, "%")])
    eng.fan_tick(time.time())
    assert eng.fan_until > time.time()                              # breath on the sensor: fan on
    cmds = st.pending_commands()
    assert any(c[1] == 1 and c[2] == "on" for c in cmds)
    ok, info = eng.fan_on("again")
    assert not ok                                                   # no double trigger while running


def test_led_strip_goes_off_on_torch_and_stays_off_while_bright(tmp_path):
    import time
    import app as expo_app
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "edge"))
    from cultive_edge.store import Store
    db = str(tmp_path / "t.db")
    st = Store(db)
    eng = expo_app.Engine(db, {"light_channel": 2, "light_off_s": 10, "light_bright_lux": 1500})
    st.add_readings([(time.time(), "c", "s", "z", "light-1", "light_lux", 300.0, "lux")])
    eng.light_tick(time.time())
    assert eng.light_until == 0 and not st.pending_commands()        # normal room: strip stays on
    st.add_readings([(time.time(), "c", "s", "z", "light-1", "light_lux", 9000.0, "lux")])
    now = time.time()
    eng.light_tick(now)
    assert abs(eng.light_until - (now + 10)) < 1                       # torch: strip off for 10 s
    assert [(c[1], c[2], c[3]) for c in st.pending_commands()] == [(2, "on", 10)]
    eng.light_tick(now + 2)
    assert len(st.pending_commands()) == 1                             # no repeat mid-period
    eng.light_tick(eng.light_until - 1)
    assert len(st.pending_commands()) == 2                             # still bright near the end: extended, no blink


def test_chat_answers_from_live_values():
    import assistant
    s = {"values": {"soil_moisture": 12.0, "air_temp": 30.4, "air_humidity": 76.0, "light_lux": 210.0,
                    "soil_ec": 640.0, "soil_ph": 6.1},
         "devices": {"pump": {"on": False, "left": 0}, "fan": {"on": True, "left": 7}, "led": {"on": True, "left": 0}},
         "dry_below": 25, "water_s": 4, "asks": [], "ask": None, "offline_devices": []}
    assert "12%" in assistant.answer("how dry is the soil?", s)
    assert "30.4°C" in assistant.answer("What's the temperature", s)
    assert "7s left" in assistant.answer("is the fan on", s)
    assert "6.1" in assistant.answer("pH?", s)
    opened = []
    assert "Approve" in assistant.answer("water it please", s, create_ask=opened.append) and opened
    assert "ask me about" in assistant.answer("asdfgh", s)


def test_chat_switches_fan_and_led():
    import assistant
    s = {"values": {}, "devices": {"fan": {"on": False, "left": 0}, "led": {"on": True, "left": 0}},
         "dry_below": 25, "water_s": 4, "asks": [], "ask": None, "offline_devices": []}
    calls = []
    ctl = {k: (lambda k=k: (calls.append(k), (True, k))[1]) for k in ("fan_on", "fan_off", "led_on", "led_off")}
    assistant.answer("turn on the fan", s, controls=ctl)
    assistant.answer("switch off the lights", s, controls=ctl)
    assistant.answer("LED on please", s, controls=ctl)
    assistant.answer("stop the fan", s, controls=ctl)
    assert calls == ["fan_on", "led_off", "led_on", "fan_off"]
    assert "off" in assistant.answer("is the fan on?", s, controls=ctl) and len(calls) == 4   # a question, not a command
