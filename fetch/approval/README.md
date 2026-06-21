# Approval service — Hospital A ↔ Hospital B over iMessage

Human-in-the-loop transfer approvals. When Hospital A is short on an item that
Hospital B can spare, the doctor at A is texted (via iMessage) with **tappable
Accept/Reject links**; on accept, the doctor at B is texted to approve or deny.
On B-accept the transfer is logged to Redis (and shows on the dashboard); on
B-deny we notify A and publish an `approval_b_denied` event — the **Browserbase
"buy" fallback is a separate (partner) step** that attaches to that event.

Texts are sent by driving **Messages.app via AppleScript** (`osascript`) — see
`imessage_client.py`. (This replaced the Poke inbound API, which silently stopped
delivering: it returns `success:true` but no message arrives.)

Why links instead of replies: outbound iMessage is reliable and deterministic;
tappable links hit our FastAPI endpoints directly, so the decision needs no
reply-parsing. Requires macOS with Messages.app signed into an iMessage account;
texts send from this Mac's Apple ID.

## Run (from repo root)

```bash
pip install -r fetch/agents/requirements.txt      # fastapi, uvicorn, ...
uvicorn fetch.approval.service:app --port 8080
```

## Try it (no phone needed)

With **no recipient configured**, every message + its action links are printed to
the logs, so you can drive the whole flow from a browser/curl:

```bash
curl -XPOST localhost:8080/shortage          # -> request_id; logs Dr A's links
# open the "a/accept" link from the logs       -> logs Dr B's links
# open the "b/accept" link                      -> transfer logged to Redis
```

With a per-hospital recipient set (`IMESSAGE_TO_HOSPITAL_A` / `_B`, or shared
`IMESSAGE_TO`), the same messages are delivered as real iMessages. Smoke-test the
transport directly:

```bash
python -m fetch.approval.imessage_client hospital_a "Stockpile test ✅"
```

## Endpoints

| Method | Path | Effect |
| :-- | :-- | :-- |
| POST | `/shortage` | detect shortage → text Dr A (accept/reject) |
| GET | `/req/{id}` | request status (JSON) |
| GET | `/req/{id}/a/accept` | A accepts → text Dr B (accept/deny) |
| GET | `/req/{id}/a/reject` | A skips → end |
| GET | `/req/{id}/b/accept` | B approves → log transfer + confirm both |
| GET | `/req/{id}/b/deny` | B denies → notify A + publish `approval_b_denied` |
| GET | `/` | demo index with a "Trigger shortage" button |

## Env

- `IMESSAGE_TO_HOSPITAL_A` / `_B` — phone number (or Apple ID email) to text per hospital.
- `IMESSAGE_TO` — optional shared recipient fallback (single-phone demos).
- `APPROVAL_BASE_URL` — base for the action links (default `http://localhost:8080`).

Detection reads the Redis track's inventory/surplus; with no seeded Redis it
falls back to the canonical scenario (`hospital_a` short on IV Fluids,
`hospital_b` has surplus).
