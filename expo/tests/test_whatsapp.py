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
