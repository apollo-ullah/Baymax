# Agent Network MVP — Workstream A Roadmap

Your task: the cross-hospital network of agents. This is the qualifying core of the whole project, so it has its own roadmap. No code here, just what to build, in what order, and what "done" looks like at each step.

---

## Your MVP, defined

A user types an intent in ASI:One ("Hospital A is short on IV fluids"). Your front agent, registered on Agentverse and reachable through ASI:One, runs a real agent-to-agent negotiation across three hospital agents, composes a transfer (splitting across facilities when no single one covers the need), settles it as a transaction, and reports the outcome back in the chat. Plus the deliverables captured for Devpost.

That hits all six mandatory requirements and your hero bonus (the transaction).

**The floor vs the winning version.** Phases 0, 1, 2, 4, 5 make you prize-eligible and give you a demoable workflow. Phase 3 (the transaction) is technically a Fetch bonus, but it is the "action" in "From Intent to Action" and it's what makes you competitive rather than just eligible. Treat it as MVP unless you run out of time.

---

## The one principle that keeps you out of trouble

Two things can sink this workstream: getting an agent reachable in ASI:One at all, and firing a Payment Protocol transaction. Both are unknowns until proven. So prove each one early, in isolation, as a throwaway spike, before wiring it into the real system. Everything else you build against stubs (hardcoded inventory, a simple allocator) so you are never blocked waiting on Workstreams B, C, or D.

---

## Phase 0 — Prove the Fetch pipe (do this first)

- Redeem the promo codes: BERKELEYAIAV for Agentverse, BERKELEYAI for ASI:One.
- Run the 5-minute setup: register a hello-world agent on Agentverse and confirm you can talk to it from ASI:One.
- **Done when:** you type to a trivial agent in ASI:One and it replies.

This is your first go/no-go. If you cannot get a hello agent reachable in ASI:One, nothing else in the workstream matters, so clear it before building anything real.

---

## Phase 1 — Negotiation core runs live

- You already have the three-agent negotiation logic built and verified. Get the messages actually flowing with the network on (Bureau locally to start).
- **Done when:** a run logs the full chain: shortfall, request, an offer from each facility, a plan that includes the split, accept, confirmed.

---

## Phase 2 — Front agent speaks ASI:One (the make-or-break)

- Layer the Chat Protocol onto your front agent so a user (and ASI:One) can message it. On an intent like "we're short on IV fluids," it kicks off the negotiation and narrates the outcome back conversationally.
- **Done when:** in ASI:One you send a request and get the negotiation result in the chat.

This clears mandatory requirements: Chat Protocol, ASI:One-discoverable, workflow completes with no custom frontend. Time this around the 3pm Fetch workshop, which covers exactly this and has mentors on hand to unblock you.

---

## Phase 3 — The transaction (your hero)

- Spike first: fire a bare Payment Protocol transaction between two throwaway agents on testnet. This is your second go/no-go.
- Then integrate: when a transfer resolves, settle it via Payment Protocol, surfaced as the confirm/pay step inside the ASI:One chat.
- **Done when:** the chat shows a real testnet transaction completing as the settlement of the transfer.

---

## Phase 4 — Register and make discoverable

- Register all three agents on Agentverse with proper READMEs: each agent's name and address, a clear description, and the required badges (innovationlab, hackathon).
- **Done when:** each agent has a public profile URL and the front agent is discoverable and usable in ASI:One.

---

## Phase 5 — Capture the deliverables

These are what you actually submit, so do not leave them to the last 20 minutes:
- The ASI:One shared chat session URL showing the complete workflow.
- The three Agentverse agent profile URLs.
- A public GitHub repo with run instructions, the badges, and the agent addresses in the README.
- A 3 to 5 minute demo video.
- A short writeup of problem, target user, and outcome.
- **Done when:** all five are in hand.

---

## What you stub, so you are never blocked

- **Inventory state:** hardcoded dict now, swap for Redis (Workstream C) later. Your agents just read a number.
- **Offer ranking:** the simple nearest-first allocator you already have now, swap for Claude (Workstream B) later. Same function signature.
- **Visualization:** not your concern. The dashboard (Workstream D) is separate and is not required for the Fetch prize.

---

## Suggested order against the clock

- **Now to 3pm:** Phase 0, then Phase 1.
- **3pm:** Fetch workshop (Tilden Room, 5th floor).
- **Afternoon to evening:** Phase 2.
- **Evening:** Phase 3 spike, then integrate.
- **Night:** finish Phase 3, then Phase 4.
- **Sunday morning:** Phase 5, harden, rehearse. Submit by 11am.

The sequencing rule: Phase 0 and the Phase 3 spike are isolated proofs you do before integrating. Everything else builds on the negotiation core you already have working.
