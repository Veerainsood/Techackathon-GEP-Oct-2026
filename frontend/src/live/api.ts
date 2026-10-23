// Live client for the UDRE FastAPI backend. Every UI element in live mode is driven by these calls.
export const API_BASE: string = (import.meta.env.VITE_UDRE_API as string | undefined) ?? 'http://localhost:8000'

export const identity = {
  tenant: localStorage.getItem('udre.tenant') ?? ((import.meta.env.VITE_UDRE_TENANT as string | undefined) ?? 'default'),
  user: localStorage.getItem('udre.user') ?? ((import.meta.env.VITE_UDRE_USER as string | undefined) ?? 'analyst'),
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  return { 'X-Tenant-Id': identity.tenant, 'X-User-Id': identity.user, ...extra }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(API_BASE + path, { ...init, headers: { ...headers(), ...((init.headers as Record<string, string>) ?? {}) } })
  if (!r.ok) {
    let detail = await r.text()
    try {
      detail = JSON.stringify(JSON.parse(detail).detail ?? detail)
    } catch {
      /* keep text */
    }
    throw new Error(`${r.status}: ${detail}`)
  }
  return r.json() as Promise<T>
}

export interface ModelInfo {
  mode: string
  provider?: string
  model?: string
}
export interface ModelConfig {
  explorer: ModelInfo
  hercules: ModelInfo
  adjudicator: ModelInfo
  sandbox: string
}
export interface RuntimeSource {
  source_id: string
  name: string
  description?: string
  type: string
  mode: string
  snapshot_synced_at?: string | null
  collections: number
  data_version?: string | null
  sequence?: number | null
  sync_status: string
  last_sync_at?: string | null
  last_window_end?: string | null
  last_error?: string | null
  schema_version?: string | null
  knowledge_version?: string | null
}
export interface SyncEventRow {
  event_id: string
  source_id: string
  event_type: string
  data_version: string
  window_start: string
  window_end: string
  status: string
  attempts: number
  last_error?: string | null
  notes: string[]
  received_at: string
  collections: string[]
}
export interface CollectionFinding {
  name: string
  status: string
  method: string
  changed_rows?: number | null
  late_rows?: number | null
  deleted_rows?: number | null
  operations: Record<string, number>
  changed_columns?: string[] | null
  affected_parents: { collection: string; via: string; entities: number }[]
  affected_dependents: { collection: string; via: string; affected_rows: number | null; reason: string }[]
  affected_metrics: string[]
  schema_changed: boolean
  notes: string[]
}
export interface Investigation {
  investigation_id: string
  event_id: string
  source_id: string
  status: string
  created_at: string
  summary: {
    kind: string
    data_version?: string
    window?: string[]
    schema_changed?: boolean
    changed_collections?: string[]
    dependent_collections?: string[]
    answers_marked_stale?: number
    knowledge?: { action?: string | null }
    notes?: string[]
    collections?: CollectionFinding[]
    stage_b?: { ran: boolean; reason?: string; triggers?: unknown[] }
  }
}
export interface AnswerRow {
  answer_id: string
  question: string
  created_at: string
  stale: boolean
  stale_reason?: string | null
  final_answer?: string | null
  sources: string[]
  collections: string[]
}
export interface StoredFile {
  file_id: string
  filename: string
  size: number
  sha256: string
  created_at: string
  download_url: string
  origin?: string | null
}
export interface Workspace {
  workspace_id: string
  source_id: string
  collections: string[]
  files: { file_id: string; filename: string; collection: string }[]
}
export interface AskRequest {
  question: string
  mode?: 'dual' | 'explorer' | 'hercules'
  use_memory?: boolean
  export_formats?: string[]
  workspace_id?: string | null
  sources?: string[] | null
}
export type StreamEvent = Record<string, unknown> & { event: string; agent?: string }

export type GuardrailMode = 'auto' | 'approve' | 'never'
export interface GuardrailRow {
  rule_id: string
  action: string
  agent: string
  mode: GuardrailMode
  limit: string
  locked: boolean
}
export type ApprovalStatus = 'pending' | 'approved' | 'rejected'
export interface ApprovalRow {
  approval_id: string
  status: ApprovalStatus
  payload: Record<string, unknown>
  requested_at: string
  decided_at?: string | null
  decided_by?: string | null
  decision_note?: string | null
}

const json = { 'Content-Type': 'application/json' }

export const api = {
  health: () => request<{ status: string; sources: string[] }>('/health'),
  models: () => request<ModelConfig>('/runtime/models'),
  sources: () => request<RuntimeSource[]>('/runtime/sources'),
  events: (sourceId?: string) => request<SyncEventRow[]>(`/runtime/events${sourceId ? `?source_id=${encodeURIComponent(sourceId)}` : ''}`),
  investigations: () => request<Investigation[]>('/runtime/investigations'),
  answers: () => request<AnswerRow[]>('/runtime/answers'),
  files: () => request<StoredFile[]>('/files'),
  upload: async (files: File[]): Promise<StoredFile[]> => {
    const fd = new FormData()
    files.forEach((f) => fd.append('files', f))
    return request<StoredFile[]>('/files', { method: 'POST', body: fd })
  },
  createWorkspace: (fileIds: string[]) =>
    request<Workspace>('/workspaces', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file_ids: fileIds }) }),

  // Guardrails (Settings → Guardrails). Locked rules are refused by the server with 403.
  guardrails: () => request<GuardrailRow[]>('/guardrails'),
  setGuardrail: (ruleId: string, mode: GuardrailMode) =>
    request<GuardrailRow>(`/guardrails/${encodeURIComponent(ruleId)}`, { method: 'PUT', headers: json, body: JSON.stringify({ mode }) }),

  // Approval queue (Approvals screen).
  approvals: (status: ApprovalStatus | 'all' = 'pending') => request<ApprovalRow[]>(`/approvals?status=${status}`),
  decideApproval: (approvalId: string, approved: boolean, note?: string) =>
    request<ApprovalRow>(`/approvals/${encodeURIComponent(approvalId)}/decision`, {
      method: 'POST',
      headers: json,
      body: JSON.stringify({ approved, note: note ?? null }),
    }),

  async download(url: string, filename: string): Promise<void> {
    const r = await fetch(API_BASE + url, { headers: headers() })
    if (!r.ok) throw new Error(`download failed: ${r.status}`)
    const blob = await r.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = filename
    a.click()
    URL.revokeObjectURL(a.href)
  },

  // Server-sent events from POST /ask/stream: one JSON object per `data:` line, last one is {event: "result"}.
  async askStream(req: AskRequest, onEvent: (e: StreamEvent) => void, signal?: AbortSignal): Promise<void> {
    const r = await fetch(API_BASE + '/ask/stream', {
      method: 'POST',
      headers: headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ use_memory: true, export_formats: ['excel', 'csv'], ...req }),
      signal,
    })
    if (!r.ok || !r.body) throw new Error(`${r.status}: ${await r.text()}`)
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        for (const line of chunk.split('\n')) {
          if (line.startsWith('data: ')) onEvent(JSON.parse(line.slice(6)) as StreamEvent)
        }
      }
    }
  },
}

export function ago(iso?: string | null): string {
  if (!iso) return 'never'
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return `${Math.max(0, Math.round(s))}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}
