// Maps backend responses onto the data shapes the existing UI components already render, so live mode
// reuses the original screens unchanged.
import { useEffect, useState } from 'react'
import type { FeedItem } from '../components/Autopilot'
import type { Action, AutoRun, Boundary, Mode, Source, StandingGoal } from '../data'
import {
  ago,
  api,
  type AnswerRow,
  type ApprovalRow,
  type GuardrailRow,
  type Investigation,
  type ModelConfig,
  type RuntimeSource,
  type SyncEventRow,
} from './api'

export const LIVE = ((import.meta.env.VITE_BACKEND_MODE as string | undefined) ?? 'live') !== 'demo'

const PALETTE = ['bg-blue-600', 'bg-indigo-600', 'bg-orange-500', 'bg-brand', 'bg-violet-600', 'bg-teal-600', 'bg-sky-600']
const TYPE_LABEL: Record<string, string> = {
  postgresql: 'PostgreSQL',
  file: 'Files · DuckDB',
  duckdb: 'Files · DuckDB',
  mongodb: 'MongoDB',
  rest: 'REST API',
}
const BAD_SYNC = new Set(['sync_failed', 'visibility_timeout', 'failed'])

const hhmm = (iso?: string | null) => (iso ? new Date(iso).toTimeString().slice(0, 5) : '--:--')
const hhmmss = (iso?: string | null) => (iso ? new Date(iso).toTimeString().slice(0, 8) : '--:--:--')

export function toSource(s: RuntimeSource, i: number): Source {
  return {
    id: i + 1,
    name: s.name || s.source_id,
    // Describe the business system, not the storage engine: "Contacts synced from HubSpot", not "PostgreSQL".
    system: `${s.description || TYPE_LABEL[s.type] || s.type} · ${s.mode ?? 'live'}`,
    detail: `${s.collections} collections · ${s.data_version ? `data ${s.data_version}` : 'no sync events yet'}`,
    freshness: s.last_sync_at ? ago(s.last_sync_at) : s.snapshot_synced_at ? ago(s.snapshot_synced_at) : 'live connection',
    stale: BAD_SYNC.has(s.sync_status),
    color: PALETTE[i % PALETTE.length],
  }
}

/** Manifest entry (resolved for one question) -> Source card. */
export function manifestSource(sid: string, m: Record<string, unknown>, i: number, known?: RuntimeSource): Source {
  const consistent = m.consistency === 'pg_snapshot'
  return {
    id: i + 1,
    name: known?.name || sid,
    system: `${TYPE_LABEL[known?.type ?? ''] ?? String(m.mode ?? 'source')} · ${consistent ? 'consistent snapshot' : 'change detection'}`,
    detail: `${known ? `${known.collections} collections · ` : ''}${m.data_version ? `data ${String(m.data_version)}` : 'no sync events yet'}`,
    freshness: m.last_sync_at ? ago(String(m.last_sync_at)) : 'live connection',
    stale: BAD_SYNC.has(String(m.sync_status ?? '')),
    color: PALETTE[i % PALETTE.length],
  }
}

function investigationOutcome(inv?: Investigation): string | null {
  const s = inv?.summary
  if (!s) return null
  if (s.kind === 'sync_failed') return 'Sync failed in the source — watermark and version left unchanged'
  if (s.kind === 'knowledge_refresh') return `Knowledge refreshed${s.knowledge?.action ? ` (${s.knowledge.action})` : ''}`
  const rows = (s.collections ?? []).reduce((n, c) => n + (c.changed_rows ?? 0), 0)
  const parts = [`${rows} row${rows === 1 ? '' : 's'} changed in ${(s.changed_collections ?? []).length} table${(s.changed_collections ?? []).length === 1 ? '' : 's'}`]
  if (s.dependent_collections?.length) parts.push(`${s.dependent_collections.length} dependent table${s.dependent_collections.length === 1 ? '' : 's'} affected`)
  if (s.schema_changed) parts.push('schema changed → knowledge rebuilt')
  else if (s.knowledge?.action) parts.push(`knowledge ${s.knowledge.action}`)
  if (s.answers_marked_stale) parts.push(`${s.answers_marked_stale} saved answer${s.answers_marked_stale === 1 ? '' : 's'} marked stale`)
  return parts.join(' · ')
}

export function toFeed(events: SyncEventRow[], invs: Investigation[]): FeedItem[] {
  const byEvent = new Map(invs.map((i) => [i.event_id, i]))
  return events.map((e) => {
    const inv = byEvent.get(e.event_id)
    const method = inv?.summary.collections?.[0]?.method
    const stageB = inv?.summary.stage_b
    const handled: FeedItem['handled'] =
      e.status === 'failed' || e.status === 'rejected' ? 'blocked' : stageB?.ran || (stageB?.triggers?.length ?? 0) > 0 ? 'escalated' : e.status === 'processed' ? 'auto' : 'blocked'
    const outcome =
      investigationOutcome(inv) ??
      (e.status === 'processed' ? 'Processed' : e.last_error ? `${e.status}: ${e.last_error}` : `${e.status} · attempt ${e.attempts}`)
    return {
      source: e.source_id,
      title: `${e.event_type.replace('database.', '').replace(/\./g, ' ')} · ${e.collections.join(', ') || 'all collections'} · ${e.data_version}`,
      outcome,
      handled,
      rule: method ? `${method.replace(/_/g, ' ')} · window ${hhmmss(e.window_start)}–${hhmmss(e.window_end)}` : e.event_type,
      at: hhmm(e.received_at),
    }
  })
}

export function toGoals(sources: RuntimeSource[], events: SyncEventRow[]): StandingGoal[] {
  return sources.map((s, i) => {
    const mine = events.filter((e) => e.source_id === s.source_id)
    const ok = !BAD_SYNC.has(s.sync_status)
    return {
      id: `G-${i + 1}`,
      title: `Keep ${s.name || s.source_id} answers current`,
      metric: s.data_version ? `Data ${s.data_version} · ${s.collections} collections watched` : `${s.collections} collections · waiting for first sync event`,
      status: ok ? 'on-track' : 'at-risk',
      progress: ok ? 100 : 50,
      lastRun: s.last_sync_at ? ago(s.last_sync_at) : 'not yet',
      actionsToday: mine.filter((e) => e.status === 'processed').length,
    }
  })
}

/** Guardrail row from GET /guardrails -> Settings row. */
export function toBoundary(g: GuardrailRow): Boundary {
  return { id: g.rule_id, action: g.action, agent: g.agent, mode: g.mode as Mode, limit: g.limit, locked: g.locked }
}

/** Approval row -> the Action shape the Approvals screen and drawer render. The status comes from the queue row. */
export function toAction(row: ApprovalRow): Action {
  return { ...(row.payload as unknown as Action), id: row.approval_id, status: row.status }
}

export function toTimeline(invs: Investigation[]): AutoRun[] {
  return invs.slice(0, 12).map((i) => ({
    time: hhmm(i.created_at),
    title: `${i.source_id} · ${(i.summary.changed_collections ?? []).join(', ') || i.summary.kind}`,
    detail: investigationOutcome(i) ?? i.status,
    impact: i.summary.answers_marked_stale ? `${i.summary.answers_marked_stale} answers refreshed` : undefined,
    realized: i.status === 'ok' || i.status === 'processed',
  }))
}

export interface ActivityEntry {
  time: string
  actor: string
  event: string
  ref: string
  question?: string
}

export function toActivity(answers: AnswerRow[], events: SyncEventRow[], invs: Investigation[]): ActivityEntry[] {
  const out: (ActivityEntry & { at: string })[] = [
    ...answers.map((a) => ({
      at: a.created_at,
      time: hhmmss(a.created_at),
      actor: 'Explorer + Hercules',
      event: `${a.question}${a.stale ? ' — stale: data changed since' : ''}`,
      ref: a.answer_id.slice(0, 10),
      question: a.question,
    })),
    ...events.map((e) => ({
      at: e.received_at,
      time: hhmmss(e.received_at),
      actor: `Sync · ${e.source_id}`,
      event: `${e.event_type} ${e.data_version} → ${e.status}`,
      ref: e.event_id.slice(0, 10),
    })),
    ...invs.map((i) => ({
      at: i.created_at,
      time: hhmmss(i.created_at),
      actor: 'Change investigator',
      event: investigationOutcome(i) ?? i.status,
      ref: i.investigation_id.slice(0, 10),
    })),
  ]
  return out.sort((a, b) => b.at.localeCompare(a.at))
}

export interface LiveData {
  backend: 'up' | 'down' | 'checking'
  sources: RuntimeSource[]
  events: SyncEventRow[]
  investigations: Investigation[]
  answers: AnswerRow[]
  models: ModelConfig | null
  refresh: () => void
}

export function useLiveData(enabled: boolean, intervalMs = 5000): LiveData {
  const [state, setState] = useState<Omit<LiveData, 'refresh'>>({
    backend: 'checking',
    sources: [],
    events: [],
    investigations: [],
    answers: [],
    models: null,
  })
  const [tick, setTick] = useState(0)

  useEffect(() => {
    if (!enabled) return
    let alive = true
    const load = async () => {
      try {
        const [sources, events, investigations, answers, models] = await Promise.all([
          api.sources(),
          api.events(),
          api.investigations(),
          api.answers(),
          api.models(),
        ])
        if (alive) setState({ backend: 'up', sources, events, investigations, answers, models })
      } catch {
        if (alive) setState((s) => ({ ...s, backend: 'down' }))
      }
    }
    load()
    const t = setInterval(load, intervalMs)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [enabled, intervalMs, tick])

  return { ...state, refresh: () => setTick((t) => t + 1) }
}
