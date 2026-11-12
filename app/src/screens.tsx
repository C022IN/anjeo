import { useCallback, useEffect, useMemo, useState } from 'react'
import { RefreshControl, ScrollView, Switch, Text, TextInput, View } from 'react-native'
import {
  describeError,
  type Activity,
  type Api,
  type Approval,
  type AskResult,
  type CallOutcome,
  type Connection,
  type JsonSchema,
  type Mandate,
  type Memory,
  type Routine,
  type Server,
  type Tool,
} from './api'
import { Button, Card, Empty, ErrorLine, Pill, ago, color, policyTone, s, t, until } from './ui'

function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const run = useCallback(async () => {
    setLoading(true)
    try {
      setData(await load())
      setError(null)
    } catch (e) {
      setError(describeError(e))
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(() => {
    run()
  }, [run])
  return { data, error, loading, reload: run, setError }
}

function Screen({
  title,
  children,
  onRefresh,
  refreshing = false,
}: {
  title: string
  children: React.ReactNode
  onRefresh?: () => void
  refreshing?: boolean
}) {
  return (
    <ScrollView
      contentContainerStyle={{ padding: 16, paddingBottom: 40 }}
      keyboardShouldPersistTaps="handled"
      refreshControl={
        onRefresh ? <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={color.dim} /> : undefined
      }
    >
      <Text style={t.h1}>{title}</Text>
      {children}
    </ScrollView>
  )
}

function OutcomeView({ outcome }: { outcome: CallOutcome }) {
  if (outcome.status === 'pending_approval')
    return <Text style={[t.body, { color: color.warn }]}>Waiting in your Inbox for approval.</Text>
  if (outcome.status === 'denied') return <Text style={[t.body, { color: color.bad }]}>Blocked by policy.</Text>
  if (outcome.error) return <Text style={[t.body, { color: color.bad }]}>{outcome.error}</Text>
  const text = outcome.result?.content.map((c) => c.text ?? JSON.stringify(c)).join('\n') ?? ''
  return (
    <Text style={[t.mono, outcome.result?.is_error && { color: color.bad }]} selectable>
      {text.slice(0, 4000)}
    </Text>
  )
}

// ── Ask: one sentence, every server ────────────────────────────────────────
export function AskScreen({ api, onQueued }: { api: Api; onQueued: () => void }) {
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [history, setHistory] = useState<{ prompt: string; result: AskResult }[]>([])

  async function send() {
    const p = prompt.trim()
    if (!p) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.ask(p)
      setHistory((h) => [{ prompt: p, result }, ...h])
      setPrompt('')
      if (result.pending_approvals.length) onQueued()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Screen title="Ask">
      <Text style={[t.dim, { marginBottom: 10 }]}>
        Say it once. Anjeo reads across your servers freely; anything that reaches people waits for your approval.
      </Text>
      <TextInput
        style={[s.input, { minHeight: 90, textAlignVertical: 'top' }]}
        placeholder="e.g. Check my inbox for replies from Pentriarch leads and draft follow-ups"
        placeholderTextColor={color.dim}
        multiline
        value={prompt}
        onChangeText={setPrompt}
      />
      <ErrorLine text={error} />
      <Button label={busy ? 'Working…' : 'Send'} onPress={send} busy={busy} disabled={!prompt.trim()} />
      <View style={{ height: 16 }} />
      {history.map(({ prompt: p, result }, i) => (
        <Card key={i}>
          <Text style={[t.dim, { marginBottom: 6 }]}>{p}</Text>
          <Text style={t.body} selectable>
            {result.answer || '(no answer)'}
          </Text>
          {result.steps.length > 0 && (
            <View style={{ marginTop: 10, gap: 4 }}>
              {result.steps.map((st, j) => (
                <View key={j} style={s.row}>
                  <Pill
                    text={st.mandate ? `mandate · ${st.mandate}` : st.status === 'pending_approval' ? 'approve' : st.status}
                    tone={st.mandate ? 'accent' : st.status === 'executed' ? 'ok' : st.status === 'pending_approval' ? 'warn' : 'bad'}
                  />
                  <Text style={t.dim}>{st.tool}</Text>
                </View>
              ))}
            </View>
          )}
          {result.stop_reason && result.stop_reason !== 'end_turn' && (
            <Text style={[t.dim, { marginTop: 8 }]}>Stopped: {result.stop_reason}</Text>
          )}
        </Card>
      ))}
    </Screen>
  )
}

// ── Inbox: the owner's thumb on every outbound action ──────────────────────
export function InboxScreen({ api, onChange }: { api: Api; onChange: () => void }) {
  const { data, error, loading, reload, setError } = useLoad(() => api.approvals('pending'), [api])
  const [busyId, setBusyId] = useState<string | null>(null)
  const [outcomes, setOutcomes] = useState<Record<string, CallOutcome | { status: 'rejected' }>>({})

  async function decide(a: Approval, approve: boolean) {
    setBusyId(a.id)
    try {
      const out = approve ? await api.approve(a.id) : await api.reject(a.id)
      setOutcomes((o) => ({ ...o, [a.id]: out as CallOutcome }))
      onChange()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Screen title="Inbox" onRefresh={reload} refreshing={loading}>
      <ErrorLine text={error} />
      {data && data.length === 0 && <Empty text="Nothing waiting on you." />}
      {data?.map((a) => {
        const done = outcomes[a.id]
        return (
          <Card key={a.id}>
            <View style={[s.row, { justifyContent: 'space-between', marginBottom: 6 }]}>
              <Text style={t.h2}>{a.tool}</Text>
              <Text style={t.dim}>{ago(a.created_at)}</Text>
            </View>
            {a.intent ? <Text style={[t.dim, { marginBottom: 6 }]}>For: “{a.intent}”</Text> : null}
            <Text style={[t.mono, { marginBottom: 8 }]} selectable>
              {JSON.stringify(a.arguments, null, 2)}
            </Text>
            <Text style={[t.dim, { marginBottom: 10 }]}>
              {a.source === 'agent' ? 'Proposed by Anjeo' : 'Started by you'} · {a.reason} · {until(a.expires_at)}
            </Text>
            {done ? (
              done.status === 'rejected' ? (
                <Text style={[t.body, { color: color.dim }]}>Rejected.</Text>
              ) : (
                <OutcomeView outcome={done as CallOutcome} />
              )
            ) : (
              <View style={s.row}>
                <Button label="Approve" kind="ok" onPress={() => decide(a, true)} busy={busyId === a.id} />
                <Button label="Reject" kind="ghost" onPress={() => decide(a, false)} disabled={busyId === a.id} />
              </View>
            )}
          </Card>
        )
      })}
    </Screen>
  )
}

// ── Tools: every MCP tool you own, runnable by hand ────────────────────────
function fieldsOf(schema: JsonSchema) {
  return Object.entries(schema.properties ?? {}).map(([name, sch]) => ({
    name,
    schema: sch,
    required: schema.required?.includes(name) ?? false,
  }))
}

function coerce(raw: string, schema: JsonSchema): unknown {
  switch (schema.type) {
    case 'integer':
    case 'number': {
      const n = Number(raw)
      if (Number.isNaN(n)) throw new Error(`"${raw}" is not a number`)
      return schema.type === 'integer' ? Math.trunc(n) : n
    }
    case 'array':
    case 'object':
      return JSON.parse(raw)
    default:
      return raw
  }
}

function ToolRunner({ api, tool, onQueued }: { api: Api; tool: Tool; onQueued: () => void }) {
  const fields = useMemo(() => fieldsOf(tool.input_schema), [tool])
  const [values, setValues] = useState<Record<string, string>>({})
  const [flags, setFlags] = useState<Record<string, boolean>>({})
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<CallOutcome | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function run() {
    setBusy(true)
    setError(null)
    setOutcome(null)
    try {
      const args: Record<string, unknown> = {}
      for (const f of fields) {
        if (f.schema.type === 'boolean') {
          if (f.name in flags) args[f.name] = flags[f.name]
          continue
        }
        const raw = values[f.name]?.trim()
        if (!raw) {
          if (f.required) throw new Error(`${f.name} is required`)
          continue
        }
        args[f.name] = coerce(raw, f.schema)
      }
      const out = await api.call(tool.name, args)
      setOutcome(out)
      if (out.status === 'pending_approval') onQueued()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <View style={{ marginTop: 10 }}>
      {fields.map((f) =>
        f.schema.type === 'boolean' ? (
          <View key={f.name} style={[s.row, { marginBottom: 10 }]}>
            <Switch value={!!flags[f.name]} onValueChange={(v) => setFlags((x) => ({ ...x, [f.name]: v }))} />
            <Text style={t.body}>{f.name}</Text>
          </View>
        ) : (
          <View key={f.name}>
            <Text style={t.dim}>
              {f.name}
              {f.required ? ' *' : ''} {f.schema.type ? `(${f.schema.type})` : ''}
            </Text>
            <TextInput
              style={s.input}
              placeholder={f.schema.description ?? (f.schema.default !== undefined ? String(f.schema.default) : '')}
              placeholderTextColor={color.dim}
              autoCapitalize="none"
              multiline={f.schema.type === 'object' || f.schema.type === 'array' || f.name.includes('text')}
              value={values[f.name] ?? ''}
              onChangeText={(v) => setValues((x) => ({ ...x, [f.name]: v }))}
            />
          </View>
        ),
      )}
      <ErrorLine text={error} />
      <Button
        label={tool.policy === 'approve' ? 'Queue for approval' : 'Run'}
        onPress={run}
        busy={busy}
        disabled={tool.policy === 'deny'}
      />
      {outcome && (
        <View style={{ marginTop: 10 }}>
          <OutcomeView outcome={outcome} />
        </View>
      )}
    </View>
  )
}

export function ToolsScreen({ api, onQueued }: { api: Api; onQueued: () => void }) {
  const servers = useLoad(() => api.servers(), [api])
  const tools = useLoad(() => api.tools(), [api])
  const [open, setOpen] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [refreshing, setRefreshing] = useState(false)

  async function rediscover() {
    setRefreshing(true)
    try {
      await api.refresh()
    } catch (e) {
      servers.setError(describeError(e))
    }
    await Promise.all([servers.reload(), tools.reload()])
    setRefreshing(false)
  }

  const q = query.trim().toLowerCase()
  const visible = (tools.data ?? []).filter(
    (x) => !q || x.name.toLowerCase().includes(q) || x.description.toLowerCase().includes(q),
  )

  return (
    <Screen title="Tools" onRefresh={rediscover} refreshing={refreshing}>
      <ErrorLine text={servers.error ?? tools.error} />
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
        {servers.data?.map((sv: Server) => (
          <Card key={sv.id} style={{ marginBottom: 0, paddingVertical: 8 }}>
            <View style={s.row}>
              <Pill
                text={sv.status}
                tone={sv.status === 'online' ? 'ok' : sv.status === 'offline' ? 'bad' : 'dim'}
              />
              <Text style={t.body}>{sv.name}</Text>
              <Text style={t.dim}>{sv.tool_count}</Text>
            </View>
          </Card>
        ))}
      </View>
      <TextInput
        style={s.input}
        placeholder="Search tools"
        placeholderTextColor={color.dim}
        autoCapitalize="none"
        value={query}
        onChangeText={setQuery}
      />
      {tools.data && visible.length === 0 && <Empty text="No tools. Pull down to rediscover servers." />}
      {visible.map((tool) => (
        <Card key={tool.name}>
          <Text onPress={() => setOpen(open === tool.name ? null : tool.name)}>
            <Text style={t.h2}>{tool.original}</Text>
            <Text style={t.dim}> · {tool.server}</Text>
          </Text>
          <View style={[s.row, { marginVertical: 4 }]}>
            <Pill text={tool.policy} tone={policyTone[tool.policy]} />
            <Text style={t.dim}>{tool.policy_reason}</Text>
          </View>
          <Text style={t.dim} numberOfLines={open === tool.name ? undefined : 2}>
            {tool.description}
          </Text>
          {open === tool.name && <ToolRunner api={api} tool={tool} onQueued={onQueued} />}
        </Card>
      ))}
    </Screen>
  )
}

// ── Activity: the ledger ───────────────────────────────────────────────────
export function ActivityScreen({ api }: { api: Api }) {
  const { data, error, loading, reload } = useLoad(() => api.activity(), [api])
  const tone = (st: string) =>
    st === 'executed' || st === 'end_turn' || st === 'set' ? 'ok' : st === 'pending' ? 'warn' : st === 'failed' || st === 'denied' ? 'bad' : 'dim'
  return (
    <Screen title="Activity" onRefresh={reload} refreshing={loading}>
      <ErrorLine text={error} />
      {data && data.length === 0 && <Empty text="Nothing has happened yet." />}
      {data?.map((a: Activity) => (
        <View key={a.id} style={[s.row, { paddingVertical: 8, borderBottomWidth: 1, borderColor: color.line }]}>
          <Pill text={a.status} tone={tone(a.status)} />
          <View style={{ flex: 1 }}>
            <Text style={t.body}>{a.tool ?? a.kind}</Text>
            <Text style={t.dim}>
              {a.kind} · {ago(a.at)}
            </Text>
          </View>
        </View>
      ))}
    </Screen>
  )
}

// ── Me: what the digital self knows, and how to reach the engine ───────────
export function MeScreen({
  api,
  connection,
  onConnection,
}: {
  api: Api
  connection: Connection
  onConnection: (c: Connection) => void
}) {
  const mem = useLoad(() => api.memory(), [api])
  const [key, setKey] = useState('')
  const [value, setValue] = useState('')
  const [baseUrl, setBaseUrl] = useState(connection.baseUrl)
  const [token, setToken] = useState(connection.token)
  const [health, setHealth] = useState<string | null>(null)

  async function add() {
    if (!key.trim() || !value.trim()) return
    try {
      await api.remember(key.trim(), value.trim())
      setKey('')
      setValue('')
      mem.reload()
    } catch (e) {
      mem.setError(describeError(e))
    }
  }

  async function forget(m: Memory) {
    try {
      await api.forget(m.key)
      mem.reload()
    } catch (e) {
      mem.setError(describeError(e))
    }
  }

  async function test() {
    try {
      const h = await api.health()
      setHealth(`Connected · engine ${h.version}`)
    } catch (e) {
      setHealth(describeError(e))
    }
  }

  return (
    <Screen title="Me" onRefresh={mem.reload} refreshing={mem.loading}>
      <Text style={t.h2}>What Anjeo knows about you</Text>
      <Text style={[t.dim, { marginBottom: 10 }]}>
        Your voice, your goals, your no-go zones. Every Ask carries this.
      </Text>
      <ErrorLine text={mem.error} />
      {mem.data?.map((m) => (
        <Card key={m.key}>
          <View style={[s.row, { justifyContent: 'space-between' }]}>
            <Text style={t.h2}>{m.key}</Text>
            <Text style={[t.dim, { color: color.bad }]} onPress={() => forget(m)}>
              forget
            </Text>
          </View>
          <Text style={t.body}>{m.value}</Text>
        </Card>
      ))}
      <TextInput
        style={s.input}
        placeholder="key (e.g. voice)"
        placeholderTextColor={color.dim}
        autoCapitalize="none"
        value={key}
        onChangeText={setKey}
      />
      <TextInput
        style={[s.input, { minHeight: 60 }]}
        placeholder="value (e.g. direct, warm, no buzzwords)"
        placeholderTextColor={color.dim}
        multiline
        value={value}
        onChangeText={setValue}
      />
      <Button label="Remember" onPress={add} disabled={!key.trim() || !value.trim()} />

      <View style={{ height: 28 }} />
      <MandatesSection api={api} />
      <View style={{ height: 28 }} />
      <RoutinesSection api={api} />
      <View style={{ height: 28 }} />
      <Text style={t.h2}>Connection</Text>
      <TextInput
        style={s.input}
        placeholder="Engine URL"
        placeholderTextColor={color.dim}
        autoCapitalize="none"
        autoCorrect={false}
        value={baseUrl}
        onChangeText={setBaseUrl}
      />
      <TextInput
        style={s.input}
        placeholder="ANJEO_TOKEN"
        placeholderTextColor={color.dim}
        autoCapitalize="none"
        autoCorrect={false}
        secureTextEntry
        value={token}
        onChangeText={setToken}
      />
      <View style={s.row}>
        <Button label="Save" onPress={() => onConnection({ baseUrl: baseUrl.trim(), token: token.trim() })} />
        <Button label="Test" kind="ghost" onPress={test} />
      </View>
      {health && <Text style={[t.dim, { marginTop: 8 }]}>{health}</Text>}
    </Screen>
  )
}

function limitText(name: string, l: Mandate['args'][string]): string {
  const parts: string[] = []
  if (l.min !== null && l.max !== null) parts.push(`${l.min}–${l.max}`)
  else if (l.max !== null) parts.push(`≤ ${l.max}`)
  else if (l.min !== null) parts.push(`≥ ${l.min}`)
  if (l.one_of) parts.push(l.one_of.join(' / '))
  if (l.required && !parts.length) parts.push('required')
  return `${name}: ${parts.join(', ')}`
}

function MandatesSection({ api }: { api: Api }) {
  const { data, error } = useLoad(() => api.mandates(), [api])
  return (
    <View>
      <Text style={t.h2}>Mandates</Text>
      <Text style={[t.dim, { marginBottom: 10 }]}>
        Where Anjeo may act without asking. Outside these limits it asks you. Edit them in anjeo.toml.
      </Text>
      <ErrorLine text={error} />
      {data && data.length === 0 && <Text style={t.dim}>None. Every action asks you first.</Text>}
      {data?.map((m) => (
        <Card key={m.id}>
          <View style={[s.row, { justifyContent: 'space-between' }]}>
            <Text style={t.h2}>{m.id}</Text>
            <Pill text={m.active ? 'active' : 'inactive'} tone={m.active ? 'accent' : 'dim'} />
          </View>
          <Text style={[t.dim, { marginBottom: 6 }]}>{m.tool}</Text>
          {Object.entries(m.args).map(([name, l]) => (
            <Text key={name} style={t.body}>
              {limitText(name, l)}
            </Text>
          ))}
          {m.checks.map((c, i) => (
            <Text key={i} style={t.body}>
              {c.label || c.path}: {c.min !== null ? `≥ ${c.min}` : ''}
              {c.max !== null ? ` ≤ ${c.max}` : ''} (checked live)
            </Text>
          ))}
          {m.max_per_day !== null && (
            <Text style={t.body}>
              today: {m.used_today} / {m.max_per_day}
            </Text>
          )}
          {m.inactive_reason && <Text style={[t.dim, { marginTop: 6 }]}>{m.inactive_reason}</Text>}
        </Card>
      ))}
    </View>
  )
}

function RoutinesSection({ api }: { api: Api }) {
  const { data, error, reload, setError } = useLoad(() => api.routines(), [api])
  const [busy, setBusy] = useState<string | null>(null)

  async function think(r: Routine) {
    setBusy(r.id)
    try {
      await api.runRoutine(r.id)
      reload()
    } catch (e) {
      setError(describeError(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <View>
      <Text style={t.h2}>Routines</Text>
      <Text style={[t.dim, { marginBottom: 10 }]}>What Anjeo thinks about on its own, and what it last concluded.</Text>
      <ErrorLine text={error} />
      {data && data.length === 0 && <Text style={t.dim}>None scheduled.</Text>}
      {data?.map((r) => (
        <Card key={r.id}>
          <View style={[s.row, { justifyContent: 'space-between' }]}>
            <Text style={t.h2}>{r.id}</Text>
            <Text style={t.dim}>
              every {r.every_minutes}m{r.last_run ? ` · ${ago(r.last_run)}` : ''}
            </Text>
          </View>
          <Text style={[t.dim, { marginBottom: 6 }]}>{r.prompt}</Text>
          {r.last_answer && (
            <Text style={[t.body, { marginBottom: 8 }]} selectable>
              {r.last_answer}
            </Text>
          )}
          <Button
            label={r.running || busy === r.id ? 'Thinking…' : 'Think now'}
            kind="ghost"
            onPress={() => think(r)}
            busy={busy === r.id}
            disabled={r.running}
          />
        </Card>
      ))}
    </View>
  )
}
