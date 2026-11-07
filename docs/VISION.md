# Anjeo: the digital self

## The thesis

Today, your agents live in separate servers, each with its own dashboard, token
and idea of what's safe. Anjeo flips that around. **You** are the integration
point, and every agent acts *as you*, under rules you hold.

Three things make that real:

1. **One namespace.** Every MCP server you own shows up as one set of tools.
   A new server is a new block in `anjeo.toml`, with no new app screen.
2. **One gate.** Reads are free. Actions wait for your approval, on your phone,
   with the exact arguments shown. The agent knows an action is *waiting*, not
   *done*, and doesn't retry it.
3. **One self.** Your voice, goals and no-go zones live in memory. Every request
   carries them, so a LinkedIn reply and a cold email sound like the same person.

## Shipped in v0.1

- Engine: hub (HTTP + stdio MCP), policy, approval inbox with single-use claims
  and expiry, activity log, memory, and a Claude agent behind `/ask`.
- App: Ask, Inbox (with a live badge), Tools (a form built from each tool's
  JSON Schema), Activity, Me (memory and connection).

## Roadmap, in order of what it unlocks

1. **FXRizon as an MCP server.** Wrap `trading_agent` status, positions, risk
   profile and kill switch as tools. Reads get `allow`; anything that changes
   risk or places orders gets `deny` or `approve` through explicit rules.
2. **Push approvals.** Expo push notifications when the Inbox gets an item, with
   Approve/Reject as notification actions. The phone becomes the hardware key
   for your digital self.
3. **Edit before approve.** Change a drafted message in the Inbox and approve
   the edited version. The gateway already runs only the stored arguments, so
   editing means updating those arguments before the claim.
4. **Anjeo as an MCP server.** Expose the namespaced tools and memory over
   `/mcp`, so Claude Desktop or Claude Code use your digital self. Every call
   still goes through the same gateway and lands in the same Inbox.
5. **Standing intents.** "Every morning, triage my inbox and queue replies."
   Scheduled asks whose actions pile up in the Inbox for one review pass.
6. **Trust that grows.** Per-tool approval history ("you approved 40 of 40
   `email_send` to existing threads") suggests promoting narrow patterns to
   `allow`, but only as a rule *you* accept into `anjeo.toml`, never silently.
7. **Voice from evidence.** Learn memory from approved and edited drafts (what
   you changed is the signal) and propose memory updates, which also need
   your approval.

## Decisions worth knowing

- **Session per call** to downstream servers: one handshake per call buys
  immunity to redeploys and dropped sessions. That's the right trade at human
  scale. Pool sessions only if a hot loop ever needs it.
- **Approvals store arguments, not intent.** An approved action can never be
  re-generated into something else between your tap and its execution.
- **The agent can't write memory.** Tool results carry other people's words,
  and memory is identity. A memory write by the agent will be a proposal in
  the Inbox (roadmap 7), never a direct write.
