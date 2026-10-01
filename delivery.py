"""Confirm Discord delivery before persisting the next comparison value."""
import json
import os


class Delivery:
    def __init__(self, state, key):
        self.state, self.key = state, key

    def __call__(self, payload, *, state_updates=None):
        import requests
        url = os.environ.get("WEBHOOK_URL")
        if not url:
            raise RuntimeError("WEBHOOK_URL is missing")
        options = {"params": {"wait": "true"}, "timeout": (10, 40), "json": payload}
        self.state.before_send(self.key)
        try:
            response = requests.post(url, **options)
        except requests.RequestException as exc:
            self.state.fail(self.key, f"Discord transport: {type(exc).__name__}")
            raise RuntimeError("Discord delivery is unconfirmed; automatic resend suppressed") from None
        if not 200 <= response.status_code < 300:
            self.state.fail(self.key, f"Discord HTTP {response.status_code}",
                            rejected=400 <= response.status_code < 500)
            raise RuntimeError(f"Discord HTTP {response.status_code}")
        message_id = None
        if response.content:
            try:
                message_id = response.json().get("id")
            except (ValueError, AttributeError):
                pass
        self.state.sent(self.key, message_id, state_updates or {})
        print(f"DELIVERED {self.key}", flush=True)


def preview(payload, *, state_updates=None):
    print(json.dumps({"dry_run": True, "payload": payload,
                      "state_updates": state_updates or {}}, ensure_ascii=False), flush=True)
