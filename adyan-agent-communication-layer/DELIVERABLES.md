# STOCKPILE — Submission deliverables checklist

Tracking the Fetch.ai **"From Intent to Action"** submission artifacts and the
mandatory requirements. Code is complete; the remaining boxes are **browser-gated
manual steps** (Agentverse Mailbox connect, ASI:One demo, video) that cannot be
done from the build environment.

---

## Submission artifacts (fill in the placeholders)

- [ ] **ASI:One shared-chat URL** — the public conversation showing the live
      negotiation + settlement:
      `__FILL_ME__:  https://asi1.ai/chat/__________`
- [ ] **Agentverse profile URLs** (one per agent, after each Mailbox connect):
  - [ ] `stockpile_front` (Hospital A) —
        `__FILL_ME__:  https://agentverse.ai/agents/details/__________`
        (address `agent1qtmgxmgr6l8jzegay8wketwm576g58ndarpjzrvrd70jxtfg84wmujwcvau`)
  - [ ] `stockpile_hospital_b` (Hospital B) —
        `__FILL_ME__:  https://agentverse.ai/agents/details/__________`
        (address `agent1qf6xup6ayvegczq0nq829wcf8smvlxharjkj7fa67ezymq2ye4dcujrheke`)
  - [ ] `stockpile_hospital_c` (Hospital C) —
        `__FILL_ME__:  https://agentverse.ai/agents/details/__________`
        (address `agent1qd0jd7w0t6t5xzx5myupdajyk2z65zm9xsvrag2cn3909z6qmyg76kdwftt`)
- [ ] **Demo video** (≤ a few min: state an intent in ASI:One → watch the
      negotiation milestones stream → approve the FET payment → confirmed):
      `__FILL_ME__:  https://________`
- [ ] **Public code repo URL**:
      `__FILL_ME__:  https://github.com/__________`

---

## Mandatory requirements matrix (Fetch "From Intent to Action")

| # | Requirement | How STOCKPILE meets it | Status |
| :-- | :-- | :-- | :-- |
| 1 | **Agent(s) built on Fetch.ai uAgents** | Three `uagents.Agent`s (Hospital A/B/C) built via `agent_base.build_hospital_agent`; full PRD §10 negotiation in `stockpile_agents.py`. | **Done** (verified: Bureau demo + 3-agent self-test run end to end) |
| 2 | **ASI:One Chat Protocol** (official, schema-matched) | Official `chat_protocol_spec` re-exported from `protocol.py` (never redefined); `agent_base.build_chat_protocol` acks + parses; FRONT includes it with `publish_manifest=True`. | **Done** (verified: chat → on_intent → narration loop runs; manifest publishes `AgentChatProtocol`) |
| 3 | **Natural-language intent → action** | `front_agent.parse_intent` turns "Hospital A is short on IV fluids" into a structured request and calls `start_negotiation`; every milestone narrates back to the chat. LLM-parser seam ready behind the same signature. | **Done** (verified: intent parsed + negotiation kicked off in self-test) |
| 4 | **Discoverable on Agentverse via Mailbox** | Each agent has its own runner (`run_front.py`, `run_hospital_b.py`, `run_hospital_c.py`) building it with `mailbox=True`, `publish_agent_details=True`, and a README path for the profile. | **Pending-manual** (code done + addresses verified; the one-time **Connect → Mailbox** needs a browser login — see README) |
| 5 | **Payment Protocol settlement on testnet** | Official `payment_protocol_spec` (seller role) in `settlement.py`: `RequestPayment → CommitPayment → CompletePayment/CancelPayment`, with cosmpy on-chain verification (in a worker thread) on `fetchai_stable_testnet`. `run_front.py` registers it on the negotiation core so `settle_transfer` delegates directly on settle. | **Done (code) / Pending-manual (live tx)** (verified end to end offline by `wave2_e2e_check.py`: chat → negotiate → settle → `RequestPayment → CommitPayment → CompletePayment`; the live signed tx needs an ASI:One wallet) |
| 6 | **innovationlab + hackathon tags / testnet-only** | `README.md` carries the `innovationlab` + `hackathon` badges; `FETCH_NETWORK=testnet` is forced in `agent_base.py` and `NetworkConfig.fetchai_stable_testnet()` is used for verification — never mainnet, never real funds. | **Done** (verified: `agent_base.FET_NETWORK == "testnet"`; badges present in README) |

Legend: **Done** = implemented + verified in-repo. **Pending-manual** = code/config
complete and verified as far as possible offline; the remaining step is a
browser-gated Agentverse/ASI:One action that must be performed by a human.

---

## Manual steps to flip the remaining boxes

These cannot be automated from the build environment (browser login + a funded
ASI:One wallet are required). Run order does not matter for the three agents.

1. **Start each agent in its own terminal** (each pins testnet):
   ```bash
   ./.venv/bin/python run_hospital_b.py
   ./.venv/bin/python run_hospital_c.py
   ./.venv/bin/python run_front.py
   ```
   Each banner prints the agent address and the **Agent Inspector** URL.
2. **For each agent**: open its Inspector URL → **Connect → Mailbox → Finish**
   (one-time). Then open its **Agentverse profile** and paste the URL above
   (requirement #4).
3. **On [ASI:One](https://asi1.ai)**: find `stockpile_front` by its address and
   send `Hospital A is short on IV fluids`. Watch the milestones stream back
   (requirements #2, #3).
4. **Approve + sign** the FET `RequestPayment` in your ASI:One wallet to settle on
   testnet (requirement #5, live tx). Capture the **shared-chat URL** and a
   **video** of the run for the placeholders above.

---

## Verification done in-repo (no browser / no live testnet)

| Check | Command | Result |
| :-- | :-- | :-- |
| Addresses derive deterministically | `./.venv/bin/python -c "import agent_base; [print(f, agent_base.address_for(f)) for f in ('Hospital A','Hospital B','Hospital C')]"` | the three `agent1q…` addresses above |
| Each runner constructs the right agent | `./.venv/bin/python -c "import run_front, run_hospital_b, run_hospital_c"` then call each `build_*()` | names `stockpile_front/_hospital_b/_hospital_c`, matching addresses |
| FRONT carries **both** protocols | inspect `run_front.build_agent()[0].protocols` | `AgentChatProtocol` + `AgentPaymentProtocol` |
| Settlement seam fires on settle | (within `wave2_e2e_check.py`) `settle_transfer` → registered hook | `RequestPayment` sent to the chat user for the final plan (ref `pay-…`) |
| Full wired path (chat→negotiate→settle→pay) | `./.venv/bin/python wave2_e2e_check.py` | `WAVE2 E2E SUCCESS` — exit 0, clean 3× in a row |
| 3-agent negotiation end to end | `STOCKPILE_EXIT_WHEN_DONE=1 ./.venv/bin/python stockpile_agents.py` | `shortfall_detected → … → confirmed` (split 150 + 50) |
