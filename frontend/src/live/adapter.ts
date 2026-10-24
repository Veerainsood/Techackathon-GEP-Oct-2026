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
  type AutopilotAction,
  type Investigation,
  type LeakageCase,
  type PolicyEvent,
  type PolicyRow,
  type LeakageSummary,
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

const usd = (v: number) => `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`

/** Leakage types -> standing goals on the Autopilot screen. */
export function toLeakageGoals(s: LeakageSummary | null): StandingGoal[] {
  return Object.entries(s?.by_type ?? {}).map(([key, t]) => ({
    id: key,
    title: t.label,
    metric: `${t.cases} case${t.cases === 1 ? '' : 's'} · ${usd(t.recoverable_usd)} recoverable`,
    status: t.open ? 'at-risk' : 'on-track',
    progress: t.cases ? Math.round((t.executed / t.cases) * 100) : 100,
    lastRun: 'continuously',
    actionsToday: t.executed,
  }))
}

/** Leakage cases -> live feed items (highest priority first). */
export function toLeakageFeed(cases: LeakageCase[]): FeedItem[] {
  return cases
    .filter((c) => c.status !== 'resolved')
    .map((c) => ({
      source: c.source_label ? `${c.type_label} · HubSpot file ${c.source_label}` : c.type_label,
      title: c.root_cause.length > 160 ? `${c.root_cause.slice(0, 157)}…` : c.root_cause,
      outcome:
        c.status === 'executed'
          ? `${c.recovery?.request_type ?? 'Recovery'} sent to the AP system (${c.recovery?.request_id ?? ''}) · ${usd(c.recoverable_usd)}`
          : c.status === 'dismissed'
            ? 'Dismissed by a reviewer'
            : `Recovery drafted · ${usd(c.recoverable_usd)} recoverable · waiting in Approvals`,
      handled: c.status === 'executed' ? 'auto' : c.status === 'dismissed' ? 'blocked' : 'escalated',
      rule: `${c.supplier_name ?? 'Supplier'} · confidence ${Math.round(c.confidence * 100)}%`,
      at: hhmm(c.first_seen),
    }))
}

export function leakageKpis(s: LeakageSummary | null) {
  return [
    { l: 'Leakage detected', v: usd(s?.recoverable_usd ?? 0), c: 'text-rose-700' },
    { l: 'Recovery awaiting approval', v: String(s?.awaiting_approval ?? 0), c: 'text-amber-700' },
    { l: 'Recovered (approved)', v: usd(s?.recovered_usd ?? 0), c: 'text-emerald-700' },
  ]
}

/** Active policies -> standing goals (records in scope, violations now). */
export function toPolicyGoals(policies: PolicyRow[], actions: AutopilotAction[]): StandingGoal[] {
  return policies
    .filter((p) => p.active)
    .map((p) => ({
      id: p.policy_id,
      title: p.name,
      metric: `${p.in_scope ?? 0} in scope${p.violations ? ` · ${p.violations} violating` : ''} · ${p.region_name}`,
      status: p.violations ? 'at-risk' : 'on-track',
      progress: p.in_scope ? Math.round((((p.in_scope ?? 0) - (p.violations ?? 0)) / p.in_scope) * 100) : 100,
      lastRun: 'on every change',
      actionsToday: actions.filter((a) => a.collection === p.collection && a.decision === 'executed').length,
    }))
}

const ACTION_TITLE: Record<AutopilotAction['action'], string> = {
  sign_contract: 'Mark contract {id} signed',
  record_payment: 'Record payment of overdue invoice {id}',
}
const OUTCOME: Record<AutopilotAction['decision'], string> = {
  executed: 'Done on its own · recorded, not written to the ERP',
  awaiting_approval: 'Waiting in Approvals',
  blocked: 'Blocked by policy',
  refused: 'Refused by guardrail',
  skipped: 'No policy covers it · nothing done',
  paused: 'Not done · autopilot paused',
  rejected: 'Rejected in Approvals',
  superseded: 'No longer pending',
}

/** Autopilot decisions (HubSpot-triggered) and policy violations -> live feed items, newest first. */
export function toAutopilotFeed(actions: AutopilotAction[], events: PolicyEvent[]): (FeedItem & { ts: string })[] {
  const items = [
    ...actions.map((a) => ({
      ts: a.created_at,
      source: `HubSpot · ${a.trigger.name}${a.simulated ? ' (simulated)' : ''}`,
      title: `${ACTION_TITLE[a.action].replace('{id}', a.record_id)} — ${a.customer ?? ''}`,
      outcome: `${OUTCOME[a.decision]}: ${a.reason}`,
      handled: (a.decision === 'executed' ? 'auto' : a.decision === 'awaiting_approval' ? 'escalated' : 'blocked') as FeedItem['handled'],
      rule: `${a.trigger.account ?? a.trigger.contact} → ${a.trigger.to} · guardrail ${a.mode === 'approve' ? 'Ask me' : a.mode === 'auto' ? 'Autonomous' : a.mode}`,
      at: hhmm(a.created_at),
    })),
    ...events
      .filter((e) => e.kind === 'violation' || e.kind === 'policy_rejected')
      .map((e) => ({
        ts: e.created_at,
        source: e.kind === 'violation' ? 'Policy check' : 'Policy editor',
        title: e.title,
        outcome: e.kind === 'violation' ? 'Violation recorded · no action taken' : String((e.detail as { reason?: string }).reason ?? ''),
        handled: 'blocked' as FeedItem['handled'],
        rule: e.kind === 'violation' ? String((e.detail as { region?: string }).region ?? '') : 'Admission check',
        at: hhmm(e.created_at),
      })),
    ...events
      .filter((e) => e.kind === 'hubspot_file')
      .map((e) => {
        const leak = (e.detail as { leakage?: { cases?: number } }).leakage
        return {
          ts: e.created_at,
          source: 'HubSpot file',
          title: e.title,
          outcome: leak?.cases
            ? 'Checked by the data-prep agent, read as invoices · recovery drafts waiting in Approvals'
            : 'Checked by the data-prep agent, read as invoices · nothing to recover',
          handled: (leak?.cases ? 'escalated' : 'auto') as FeedItem['handled'],
          rule: 'Every file uploaded to HubSpot is checked for leaks',
          at: hhmm(e.created_at),
        }
      }),
  ]
  return items.sort((a, b) => (a.ts < b.ts ? 1 : -1))
}

export interface LiveData {
  backend: 'up' | 'down' | 'checking'
  sources: RuntimeSource[]
  events: SyncEventRow[]
  investigations: Investigation[]
  answers: AnswerRow[]
  models: ModelConfig | null
  leakage: LeakageSummary | null
  leakageCases: LeakageCase[]
  policies: PolicyRow[]
  autopilot: AutopilotAction[]
  policyEvents: PolicyEvent[]
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
    leakage: null,
    leakageCases: [],
    policies: [],
    autopilot: [],
    policyEvents: [],
  })
  const [tick, setTick] = useState(0)

  useEffect(() => {
    if (!enabled) return
    let alive = true
    const load = async () => {
      try {
        const [sources, events, investigations, answers, models, leakage, leakageCases, policies, autopilot, policyEvents] = await Promise.all([
          api.sources(),
          api.events(),
          api.investigations(),
          api.answers(),
          api.models(),
          api.leakageSummary().catch(() => null),
          api.leakageCases().catch(() => []),
          api.policies().catch(() => []),
          api.autopilotActions().catch(() => []),
          api.policyEvents().catch(() => []),
        ])
        if (alive)
          setState({ backend: 'up', sources, events, investigations, answers, models, leakage, leakageCases, policies, autopilot, policyEvents })
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
