# Anjeo: your digital self

Anjeo puts every MCP server you run behind one control plane on your phone.
LinkedIn, mail, FXRizon and anything else you connect show up as one set of
tools. You can ask in a sentence, or run any tool by hand. Reads happen
straight away. Anything that reaches other people or moves money waits in your
Inbox until you approve it.

```
 Anjeo app (Expo / RN)                Anjeo engine (Python)                 your MCP servers
 ┌──────────────────┐   HTTPS +    ┌───────────────────────────────┐   Streamable HTTP   ┌─────────────────────┐
 │ Ask              │   bearer     │  /ask ── Claude agent loop    │ ──────────────────▶ │ linkedin-mcp-server │
 │ Inbox (approve)  │ ───────────▶ │           │                   │                     ├─────────────────────┤
 │ Tools (run any)  │              │  gateway ─┴─ policy ─ hub ────│ ──────── stdio ───▶ │ local tools         │
 │ Activity · Me    │              │  mandates · routines (cron)   │                     ├─────────────────────┤
 └──────────────────┘              │  sqlite: inbox · ledger · me  │ ──────────────────▶ │ FXRizon adapter     │
                                   └───────────────────────────────┘                     └─────────────────────┘
```

## The core rule

**Every path to action goes through the gateway.** That covers a tap in the app
and every step the agent takes. For each tool, policy chooses one of:

| Policy    | Meaning                                    | How a tool gets it                                                               |
|-----------|--------------------------------------------|----------------------------------------------------------------------------------|
| `allow`   | runs now                                   | lookup verbs (`get_`, `list_`, `read_`, `search_`…) or a `read_only_hint`         |
| `approve` | queued in the Inbox, runs only when you approve | action verbs (`send_`, `post_`, `delete_`…), a `destructive_hint`, or the default |
| `deny`    | never runs                                 | only an explicit rule in `anjeo.toml`                                            |

A fourth state sits on top of `approve`: **mandate**. A mandate is standing
permission for one tool inside limits you write: argument bounds, a daily
count, and live checks such as "worst account down less than 2% today". Inside
every limit the call runs. Outside any limit it waits in your Inbox with the
exact reason ("risk_pct 0.4 > 0.25") and a short expiry. Mandates fail closed: a
check that can't be read counts as a failed check.

**Routines** are Anjeo thinking on a schedule: a standing prompt, run every N
minutes through the same agent and gateway. This is how FXRizon gets triggered
by Anjeo's own thoughts. A routine can act alone only inside a mandate. Each
run's conclusion shows under Me, and you can also run it with "Think now".

Some tools are **owner-only**: the server marks them (`_meta["anjeo/owner_only"]`),
for example FXRizon's kill switch. They always ask you; a rule can tighten one to
`deny` but nothing loosens it.

Rules in your own `anjeo.toml` override everything else. A server can make one
of its tools stricter, but it can't loosen one: a `read_only_hint` on a tool
called `delete_everything` still waits for you. Approving runs exactly the
arguments you were shown, and only once, even if you tap Approve on two devices.
An approval that isn't acted on expires after 24h by default.

## Run it

```bash
cd engine
pip install -e '.[dev]'
cp anjeo.example.toml anjeo.toml          # point it at your servers
export LINKEDIN_MCP_TOKEN=...             # whatever your headers reference
export ANTHROPIC_API_KEY=...              # needed only for Ask
export ANJEO_TOKEN=$(openssl rand -hex 24) # the app's key; required off localhost
ANJEO_HOST=0.0.0.0 anjeo                  # serves on :8710
pytest                                    # 30 tests; real in-process MCP servers, no mocks of MCP
```

```bash
cd app
npm install
npx expo start          # open in Expo Go, then Me → Connection: engine URL + ANJEO_TOKEN
```

## API

| Method | Path | |
|---|---|---|
| GET  | `/health` | liveness, no auth |
| GET  | `/servers` · POST `/servers/refresh` | connected servers; rediscover tools |
| GET  | `/tools` | every tool, with its policy and the reason for it |
| POST | `/tools/call` `{tool, arguments}` | run through the gateway |
| POST | `/ask` `{prompt}` | Claude works across all servers; actions get queued |
| GET  | `/approvals?status=pending` | the Inbox |
| POST | `/approvals/{id}/approve` · `/reject` | decide |
| GET  | `/activity` | the activity log |
| GET/PUT/DELETE | `/memory/{key}` | what Anjeo knows about you (voice, goals, no-go zones) |
| GET  | `/mandates` | standing permissions, whether each is active, today's usage |
| GET  | `/routines` · POST `/routines/{id}/run` | scheduled thinking; think now |

See [docs/VISION.md](docs/VISION.md) for where this goes next.
