"""
iMessage notification client (outbound only) — Poke API replacement.

Sends a real blue-bubble iMessage by driving Messages.app via AppleScript
(`osascript`). Per-hospital recipients (IMESSAGE_TO_HOSPITAL_A / _B) fall back to
a shared IMESSAGE_TO for single-phone testing. With NO recipient for a hospital
it logs the message (links included) instead of sending — so the whole approval
flow is still testable without a phone.

Drop-in for the old poke_client: same `notify(hospital_id, message) -> bool`
signature, so service.py and the A<->B state machine are unchanged.

Requirements: runs on macOS, with Messages.app signed into an iMessage account.
The text is sent from THIS Mac's Apple ID. Tappable http links in the body open
the FastAPI accept/reject endpoints directly (no reply parsing needed).
"""

import logging
import os
import subprocess

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("stockpile.imessage")

# Read the script from stdin (`osascript -`) and take recipient + body from argv,
# so the message text (emoji, newlines, URLs, quotes) never gets string-interpolated
# into the script — no escaping bugs. Falls back to SMS service if no iMessage.
_APPLESCRIPT = """
on run argv
    set targetPhone to item 1 of argv
    set targetMessage to item 2 of argv
    tell application "Messages"
        try
            set targetService to 1st account whose service type = iMessage
            set targetBuddy to participant targetPhone of targetService
            send targetMessage to targetBuddy
        on error
            send targetMessage to participant targetPhone
        end try
    end tell
end run
"""


def _recipient_for(hospital_id: str) -> str | None:
    """Per-hospital recipient (IMESSAGE_TO_HOSPITAL_A) falling back to a shared
    IMESSAGE_TO for single-phone demos. Returns None if nothing is configured."""
    specific = os.getenv(f"IMESSAGE_TO_{hospital_id.upper()}")
    return (specific or os.getenv("IMESSAGE_TO") or "").strip() or None


def notify(hospital_id: str, message: str) -> bool:
    """Text the doctor at `hospital_id` over iMessage. Returns True if handed to
    Messages.app, False if no-op'd (logged only). Never raises — a notify must
    not break the flow.
    """
    to = _recipient_for(hospital_id)
    if not to:
        log.info("[imsg:%s] (no recipient — message NOT sent, shown for testing)\n%s",
                 hospital_id, message)
        return False
    try:
        proc = subprocess.run(
            ["osascript", "-", to, message],
            input=_APPLESCRIPT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        if proc.returncode != 0:
            log.warning("[imsg:%s] send failed (rc=%s): %s — message was:\n%s",
                        hospital_id, proc.returncode, proc.stderr.strip(), message)
            return False
        log.info("[imsg:%s] sent ✅ -> %s", hospital_id, to)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("[imsg:%s] send error (%s) — message was:\n%s",
                    hospital_id, exc, message)
        return False


if __name__ == "__main__":  # quick manual test: python -m fetch.approval.imessage_client hospital_a
    import sys
    hid = sys.argv[1] if len(sys.argv) > 1 else "hospital_a"
    body = sys.argv[2] if len(sys.argv) > 2 else "Stockpile iMessage test ✅"
    print(f"notify({hid!r}) ->", notify(hid, body))
