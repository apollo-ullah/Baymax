# STOCKPILE — 60-second demo script

The whole demo happens **inside the ASI:One chat** — no custom frontend. The hero
beat is the **split + settle**: no single hospital can cover the shortfall, so the
network composes a multi-facility transfer and settles it as a real testnet
transaction, narrating every step back into the chat.

---

## Pre-demo checklist (do this BEFORE recording)

1. Fund the ASI:One wallet with testnet FET — faucet: https://faucet-dorado.fetch.ai
2. Start the three agents, each in its own terminal:
   ```bash
   ./.venv/bin/python run_hospital_b.py
   ./.venv/bin/python run_hospital_c.py
   ./.venv/bin/python run_front.py
   ```
3. For each, open the printed **Agent Inspector** URL → **Connect → Mailbox → Finish**.
4. In ASI:One, open a chat with `stockpile_front` (address in the README).
5. Confirm `PAYMENT_VERIFY_ONCHAIN=true` (real verification) and `FETCH_NETWORK=testnet`.

---

## The 60 seconds

| Time | On screen | Voiceover |
| :-- | :-- | :-- |
| **0:00–0:08** | Title: "Stockpile — hospitals that restock each other, autonomously." Cut to the ASI:One chat. | "Hospitals run lean on supplies. When one runs short, another nearby often has surplus — but reconciling it is slow and manual. Stockpile automates it." |
| **0:08–0:16** | Type into ASI:One: **`Hospital A is short on IV fluids`** and send. | "A supply manager just states the problem in plain language — to an agent live on ASI:One." |
| **0:16–0:30** | Milestones stream into the chat: `shortfall_detected` (short 200) → `requesting` → offers from **B (150, near)** and **C (80, far, nearer expiry)`. | "The front agent broadcasts to the network. Two hospitals answer with real constraints — quantity, distance, expiry." |
| **0:30–0:42** | `evaluating`: "no single facility covers 200" → **split: 150 from B + 50 from C** → `proposing` → both **accept**. | "No one hospital can cover it — so the agents compose a split across two facilities and re-plan on the fly. This is the real negotiation." |
| **0:42–0:54** | `settling` → an **"Approve FET Payment"** action appears in the chat. Click it; wallet signs. | "The resolved transfer settles as a real on-chain transaction — the approval happens right here in the chat." |
| **0:54–1:00** | `confirmed` with the transfer summary + the testnet **tx hash**. | "Detected, negotiated, split, and settled — autonomously. That's Stockpile." |

---

## Backup line (if asked "is it real?")

"Real Fetch.ai uAgents messaging agent-to-agent, the official Chat and Payment
protocols, and a real FET transaction on the dorado-1 testnet — verified on-chain
before the deal is marked complete. Inventory and offer-ranking are clean stubbed
seams (Redis / Claude) owned by sibling workstreams."

## Fallback scenarios (if you want variety / a safety net)

- Full cover: **`we're short 100 saline`** → single-facility transfer (B alone).
- No offer: **`need sutures at Hospital A`** → graceful escalation to manual procurement.
- Backup video: pre-record the run in case the live venue network is flaky.
