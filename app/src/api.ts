import AsyncStorage from '@react-native-async-storage/async-storage'

export type Policy = 'allow' | 'approve' | 'deny' | 'mandate'

export type Server = {
  id: string
  name: string
  transport: 'http' | 'stdio'
  status: 'unknown' | 'online' | 'offline' | 'disabled'
  error: string | null
  tool_count: number
}

export type JsonSchema = {
  type?: string
  description?: string
  properties?: Record<string, JsonSchema>
  required?: string[]
  enum?: unknown[]
  default?: unknown
}

export type Tool = {
  name: string
  server: string
  original: string
  description: string
  input_schema: JsonSchema
  policy: Policy
  policy_reason: string
  owner_only?: boolean
}

export type Approval = {
  id: string
  created_at: number
  expires_at: number
  tool: string
  server: string
  arguments: Record<string, unknown>
  reason: string
  source: 'app' | 'agent'
  intent: string | null
  status: string
  result: unknown
}

export type CallOutcome = {
  status: 'executed' | 'failed' | 'pending_approval' | 'denied' | 'unknown_tool'
  tool: string
  result?: { is_error: boolean; content: { type: string; text?: string }[]; structured: unknown }
  approval?: Approval
  error?: string
  reason?: string
}

export type AskResult = {
  answer: string
  steps: { tool: string; status: string; approval_id?: string; mandate?: string }[]
  pending_approvals: string[]
  stop_reason: string | null
}

export type Mandate = {
  id: string
  tool: string
  enabled: boolean
  active: boolean
  inactive_reason: string | null
  max_per_day: number | null
  used_today: number
  args: Record<string, { required: boolean; min: number | null; max: number | null; one_of: unknown[] | null }>
  checks: { tool: string; path: string; min: number | null; max: number | null; label: string }[]
  note: string
}

export type Routine = {
  id: string
  prompt: string
  every_minutes: number
  enabled: boolean
  running: boolean
  last_run: number | null
  last_answer: string | null
}

export type Activity = { id: number; at: number; kind: string; tool: string | null; status: string; detail: unknown }
export type Memory = { key: string; value: string; updated_at: number }

export type Connection = { baseUrl: string; token: string }

const STORAGE_KEY = 'anjeo.connection'
const DEFAULT: Connection = { baseUrl: 'http://127.0.0.1:8710', token: '' }

export async function loadConnection(): Promise<Connection> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY)
    return raw ? { ...DEFAULT, ...JSON.parse(raw) } : DEFAULT
  } catch {
    return DEFAULT
  }
}

export async function saveConnection(c: Connection): Promise<void> {
  await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(c))
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

export function client(conn: Connection) {
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const res = await fetch(conn.baseUrl.replace(/\/+$/, '') + path, {
      ...init,
      headers: {
        'content-type': 'application/json',
        ...(conn.token ? { authorization: `Bearer ${conn.token}` } : {}),
        ...(init.headers ?? {}),
      },
    })
    const body = await res.json().catch(() => null)
    if (!res.ok) throw new ApiError(res.status, (body && (body.detail as string)) || res.statusText)
    return body as T
  }
  const post = <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

  return {
    health: () => request<{ ok: boolean; version: string }>('/health'),
    servers: () => request<Server[]>('/servers'),
    refresh: () => post<Server[]>('/servers/refresh'),
    tools: () => request<Tool[]>('/tools'),
    call: (tool: string, args: Record<string, unknown>) => post<CallOutcome>('/tools/call', { tool, arguments: args }),
    ask: (prompt: string) => post<AskResult>('/ask', { prompt }),
    approvals: (status = 'pending') => request<Approval[]>(`/approvals?status=${encodeURIComponent(status)}`),
    approve: (id: string) => post<CallOutcome & { approval_id: string }>(`/approvals/${id}/approve`),
    reject: (id: string) => post<{ status: string }>(`/approvals/${id}/reject`),
    mandates: () => request<Mandate[]>('/mandates'),
    routines: () => request<Routine[]>('/routines'),
    runRoutine: (id: string) => post<AskResult>(`/routines/${encodeURIComponent(id)}/run`),
    activity: () => request<Activity[]>('/activity?limit=100'),
    memory: () => request<Memory[]>('/memory'),
    remember: (key: string, value: string) =>
      request<Memory>(`/memory/${encodeURIComponent(key)}`, { method: 'PUT', body: JSON.stringify({ value }) }),
    forget: (key: string) => request<unknown>(`/memory/${encodeURIComponent(key)}`, { method: 'DELETE' }),
  }
}

export type Api = ReturnType<typeof client>

export function describeError(e: unknown): string {
  if (e instanceof ApiError) return e.status === 401 ? 'Wrong or missing token (Me → Connection).' : e.message
  if (e instanceof Error) return e.message.includes('Network') ? 'Cannot reach the engine.' : e.message
  return String(e)
}
