"""WhatsApp Cloud API: send an Approve/Ignore question, parse the button reply.

Needs (in .env):
  WA_TOKEN         access token from developers.facebook.com (temporary tokens expire in ~24h)
  WA_PHONE_ID      "Phone number ID" of the test sender number
  WA_TO            your own WhatsApp number, digits only with country code, e.g. 919876543210
  WA_VERIFY_TOKEN  any string you choose; paste the same string into the Meta webhook setup
Rules Meta enforces:
  * the test number can only message phone numbers you added as recipients (up to 5)
  * free-form/interactive messages only work within 24h after YOUR phone last messaged the bot,
    so send "hi" to the test number on the morning of the expo
"""
import json
import os
import urllib.error
import urllib.request

API = "https://graph.facebook.com/v21.0"


def configured():
    return all(os.environ.get(k) for k in ("WA_TOKEN", "WA_PHONE_ID", "WA_TO"))


def _post(payload):
    req = urllib.request.Request(
        f"{API}/{os.environ['WA_PHONE_ID']}/messages",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {os.environ['WA_TOKEN']}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return True, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return False, e.read().decode(errors="replace")[:500]
    except Exception as e:  # offline, DNS, etc.
        return False, str(e)


def send_text(body):
    if not configured():
        return False, "whatsapp not configured"
    return _post({"messaging_product": "whatsapp", "to": os.environ["WA_TO"], "type": "text",
                  "text": {"body": body}})


def send_ask(ask_id, body):
    """Interactive message with two reply buttons. Button ids carry the ask id."""
    if not configured():
        return False, "whatsapp not configured"
    return _post({
        "messaging_product": "whatsapp", "to": os.environ["WA_TO"], "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {"type": "text", "text": "Cultive AI"},
            "body": {"text": body},
            "action": {"buttons": [
                {"type": "reply", "reply": {"id": f"approve:{ask_id}", "title": "Approve"}},
                {"type": "reply", "reply": {"id": f"ignore:{ask_id}", "title": "Ignore"}},
            ]},
        },
    })


def parse_webhook(body):
    """Yield (verb, ask_id, sender) for every button reply in a webhook payload.
    Also accepts a typed reply of 'yes'/'approve' or 'no'/'ignore' (ask_id None = latest)."""
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            for msg in change.get("value", {}).get("messages", []) or []:
                sender = msg.get("from")
                if msg.get("type") == "interactive":
                    rid = msg.get("interactive", {}).get("button_reply", {}).get("id", "")
                    verb, _, ask = rid.partition(":")
                    if verb in ("approve", "ignore") and ask.isdigit():
                        yield verb, int(ask), sender
                elif msg.get("type") == "text":
                    t = msg.get("text", {}).get("body", "").strip().lower()
                    if t in ("yes", "y", "approve", "ok"):
                        yield "approve", None, sender
                    elif t in ("no", "n", "ignore"):
                        yield "ignore", None, sender
