"""
Poke notification client (outbound only).

Sends a text to a hospital's doctor via the Poke inbound API. Per-hospital keys
(POKE_API_KEY_HOSPITAL_A / _B) fall back to a shared POKE_API_KEY. With NO key it
logs the message (links included) instead of sending — so the whole approval flow
is testable from a browser/curl without a phone.
"""

import logging
import os

import httpx
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("stockpile.poke")

_POKE_URL = "https://poke.com/api/v1/inbound/api-message"

# Poke's inbound message is a prompt to its AI (it "evaluates and responds"), not
# a verbatim push. Phrase it as a clear ACTION — "send me a text" — so Poke's
# action IS messaging us. (An earlier "do not act on it" wrapper backfired: it
# told the agent to do nothing.)
_RELAY_PREFIX = (
    "Send me a text message with exactly the following content, links included:\n\n"
)


def _key_for(hospital_id: str) -> str | None:
    """Per-hospital key (POKE_API_KEY_HOSPITAL_A) falling back to POKE_API_KEY."""
    specific = os.getenv(f"POKE_API_KEY_{hospital_id.upper()}")
    return (specific or os.getenv("POKE_API_KEY") or "").strip() or None


def notify(hospital_id: str, message: str) -> bool:
    """Text the doctor at `hospital_id`. Returns True if sent over the network,
    False if no-op'd (logged only). Never raises — a notify must not break the flow.
    """
    key = _key_for(hospital_id)
    if not key:
        log.info("[poke:%s] (no key — message NOT sent, shown for testing)\n%s",
                 hospital_id, message)
        return False
    try:
        resp = httpx.post(
            _POKE_URL,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"message": _RELAY_PREFIX + message},
            timeout=10,
        )
        resp.raise_for_status()
        log.info("[poke:%s] sent ✅", hospital_id)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("[poke:%s] send failed (%s) — message was:\n%s",
                    hospital_id, exc, message)
        return False
