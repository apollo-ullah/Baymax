/**
 * Module-level server-side state shared between the crisis POST route handler
 * and the decide GET route handler.
 *
 * Next.js runs all route handlers in the same Node.js process (in dev and in
 * a long-running server deployment), so a module-level variable is the
 * simplest cross-route channel.  The decide() call always operates on the
 * most-recently-completed crisis run, which matches the UX expectation.
 */

let _lastReqId = "";

export function setLastReqId(id: string): void {
  if (id) _lastReqId = id;
}

export function getLastReqId(): string {
  return _lastReqId;
}
