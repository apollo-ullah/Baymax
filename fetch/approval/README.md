# Approval service — Hospital A ↔ Hospital B over Poke

Human-in-the-loop transfer approvals. When Hospital A is short on an item that
Hospital B can spare, the doctor at A is texted (via Poke) with **tappable
Accept/Reject links**; on accept, the doctor at B is texted to approve or deny.
On B-accept the transfer is logged to Redis (and shows on the dashboard); on
B-deny we notify A and publish an `approval_b_denied` event — the **Browserbase
"buy" fallback is a separate (partner) step** that attaches to that event.

Why links instead of replies: Poke's reliable mode is **outbound**; replies go to
Poke's own AI, not our backend. Tappable links hit our endpoints directly, so the
decision is deterministic.

## Run (from repo root)

```bash
pip install -r fetch/agents/requirements.txt      # fastapi, uvicorn, ...
uvicorn fetch.approval.service:app --port 8080
```

## Try it (no phone needed)

With **no Poke key**, every message + its action links are printed to the logs,
so you can drive the whole flow from a browser/curl:

```bash
curl -XPOST localhost:8080/shortage          # -> request_id; logs Dr A's links
# open the "a/accept" link from the logs       -> logs Dr B's links
# open the "b/accept" link                      -> transfer logged to Redis
```

With a **Poke key** set (`POKE_API_KEY`, or per-hospital
`POKE_API_KEY_HOSPITAL_A` / `_B`), the same messages are delivered as real texts.

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

- `POKE_API_KEY` — recipient (shared fallback).
- `POKE_API_KEY_HOSPITAL_A` / `_B` — optional per-doctor keys (two-phone demo).
- `APPROVAL_BASE_URL` — base for the action links (default `http://localhost:8080`).

Detection reads the Redis track's inventory/surplus; with no seeded Redis it
falls back to the canonical scenario (`hospital_a` short on IV Fluids,
`hospital_b` has surplus).
