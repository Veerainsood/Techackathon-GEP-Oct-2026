import { AlertTriangle, CheckCircle2, Clock, Database, GitBranch, RefreshCw, XCircle } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { ago, api, type AnswerRow, type Investigation, type RuntimeSource, type SyncEventRow } from './api'

function Page({ title, subtitle, children, onRefresh }: { title: string; subtitle: string; children: ReactNode; onRefresh: () => void }) {
  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className="mx-auto max-w-[960px] px-6 py-10">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="font-serif text-[32px] tracking-tight">{title}</h1>
            <p className="mt-1 text-[14px] text-muted">{subtitle}</p>
          </div>
          <button onClick={onRefresh} className="flex items-center gap-1 rounded-lg border border-line bg-white px-2.5 py-1.5 text-[12px] hover:border-stone-300"><RefreshCw size={12} /> Refresh</button>
        </div>
        <div className="mt-6">{children}</div>
      </div>
    </div>
  )
}

function Status({ s }: { s: string }) {
  const ok = s === 'ok' || s === 'processed'
  const bad = s.includes('fail') || s === 'rejected' || s.includes('timeout')
  const cls = ok ? 'bg-emerald-50 text-emerald-700' : bad ? 'bg-rose-50 text-rose-700' : 'bg-amber-50 text-amber-800'
  const Icon = ok ? CheckCircle2 : bad ? XCircle : Clock
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${cls}`}><Icon size={11} />{s}</span>
}

function usePoll<T>(fn: () => Promise<T>, ms = 5000): [T | null, string | null, () => void] {
  const [data, setData] = useState<T | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const load = useCallback(() => {
    fn().then((d) => { setData(d); setErr(null) }).catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)))
  }, [fn])
  useEffect(() => {
    load()
    const t = setInterval(load, ms)
    return () => clearInterval(t)
  }, [load, ms])
  return [data, err, load]
}

export function LiveSourcesPage() {
  const [sources, err, reload] = usePoll<RuntimeSource[]>(api.sources)
  return (
    <Page title="Sources" subtitle="Freshness comes from processed synchronization events; snapshot sources show when they were last synchronized." onRefresh={reload}>
      {err && <div className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-[12px] text-rose-800">{err}</div>}
      {!sources && !err && <div className="text-[13px] text-muted">Loading…</div>}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {sources?.map((s) => (
          <div key={s.source_id} className={`rounded-xl border bg-white p-4 ${s.sync_status.includes('fail') ? 'border-rose-200' : 'border-line'}`}>
            <div className="flex items-center justify-between">
              <span className="flex items-center gap-2 text-[14px] font-medium"><Database size={14} /> {s.source_id}</span>
              <Status s={s.sync_status} />
            </div>
            <div className="mt-1 text-[12px] text-muted">{s.name} · {s.type} · {s.mode} · {s.collections} collections</div>
            <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-[12px]">
              <dt className="text-muted">Data version</dt><dd>{s.data_version ?? '—'}</dd>
              <dt className="text-muted">Last sync event</dt><dd>{ago(s.last_sync_at)}</dd>
              <dt className="text-muted">Covered up to</dt><dd>{s.last_window_end ? new Date(s.last_window_end).toLocaleString() : '—'}</dd>
              {s.snapshot_synced_at && (<><dt className="text-muted">Snapshot synced</dt><dd>{ago(s.snapshot_synced_at)}</dd></>)}
              <dt className="text-muted">Schema version</dt><dd className="truncate font-mono text-[11px]">{s.schema_version ?? '—'}</dd>
              <dt className="text-muted">Knowledge version</dt><dd className="truncate font-mono text-[11px]">{s.knowledge_version ?? '—'}</dd>
            </dl>
            {s.last_error && <div className="mt-2 rounded bg-rose-50 px-2 py-1 text-[11px] text-rose-700">{s.last_error}</div>}
          </div>
        ))}
      </div>
    </Page>
  )
}

export function LiveChangesPage() {
  const [events, e1, r1] = usePoll<SyncEventRow[]>(useCallback(() => api.events(), []))
  const [invs, e2, r2] = usePoll<Investigation[]>(api.investigations)
  const byEvent = Object.fromEntries((invs ?? []).map((i) => [i.event_id, i]))
  return (
    <Page title="Recent database changes" subtitle="Synchronization events from the ingestion service and the scoped investigation each one triggered." onRefresh={() => { r1(); r2() }}>
      {(e1 || e2) && <div className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-[12px] text-rose-800">{e1 || e2}</div>}
      {events && !events.length && <div className="rounded-xl border border-dashed border-line p-8 text-center text-[13px] text-muted">No synchronization events received yet (POST /events/sync).</div>}
      <div className="space-y-3">
        {events?.map((ev) => {
          const inv = byEvent[ev.event_id]
          const s = inv?.summary
          return (
            <div key={ev.event_id} className="rounded-xl border border-line bg-white p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-[13px]">
                  <GitBranch size={14} className="text-muted" />
                  <b>{ev.source_id}</b> <span className="text-muted">{ev.data_version}</span>
                  <span className="text-[11px] text-stone-400">{new Date(ev.window_start).toLocaleTimeString()} → {new Date(ev.window_end).toLocaleTimeString()}</span>
                </div>
                <div className="flex items-center gap-2 text-[11px] text-muted"><Status s={ev.status} /> {ago(ev.received_at)} {ev.attempts > 1 ? `· ${ev.attempts} attempts` : ''}</div>
              </div>
              {ev.last_error && <div className="mt-2 rounded bg-amber-50 px-2 py-1 text-[12px] text-amber-900">{ev.last_error}</div>}
              {s?.kind === 'data_change' && (
                <div className="mt-3 space-y-2 text-[12px]">
                  <div className="flex flex-wrap gap-1.5">
                    {s.schema_changed && <span className="rounded bg-violet-50 px-1.5 py-0.5 text-violet-700">schema changed</span>}
                    <span className="rounded bg-stone-100 px-1.5 py-0.5">knowledge: {s.knowledge?.action ?? 'none'}</span>
                    {!!s.answers_marked_stale && <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-800">{s.answers_marked_stale} answers now stale</span>}
                    <span className="rounded bg-stone-100 px-1.5 py-0.5">agent analysis: {s.stage_b?.ran ? 'ran' : s.stage_b?.reason}</span>
                  </div>
                  <table className="w-full">
                    <thead className="text-left text-[10px] uppercase tracking-wider text-muted">
                      <tr><th className="py-1">Collection</th><th>Method</th><th>Changed</th><th>Deleted</th><th>Affected</th></tr>
                    </thead>
                    <tbody>
                      {s.collections?.map((c) => (
                        <tr key={c.name} className="border-t border-line align-top">
                          <td className="py-1.5 font-medium">{c.name}{c.status !== 'ok' && <div className="text-[11px] font-normal text-amber-700">{c.status}</div>}</td>
                          <td>{c.method}</td>
                          <td>{c.changed_rows ?? '—'}{c.late_rows ? ` (+${c.late_rows} late)` : ''}</td>
                          <td>{c.deleted_rows ?? '—'}</td>
                          <td className="text-[11px] text-stone-600">
                            {c.affected_parents.map((p) => `${p.collection} (${p.entities})`).concat(c.affected_dependents.map((d) => `${d.collection}${d.affected_rows !== null ? ` (${d.affected_rows} rows)` : ''}`)).join(', ') || '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {[...(s.notes ?? []), ...(s.collections?.flatMap((c) => c.notes.map((n) => `${c.name}: ${n}`)) ?? [])].slice(0, 6).map((n, i) => (
                    <div key={i} className="flex gap-1.5 text-[11px] text-stone-500"><AlertTriangle size={11} className="mt-0.5 shrink-0" />{n}</div>
                  ))}
                </div>
              )}
              {s && s.kind !== 'data_change' && <div className="mt-2 text-[12px] text-muted">{s.kind}: {(s.notes ?? []).join('; ')}</div>}
            </div>
          )
        })}
      </div>
    </Page>
  )
}

export function LiveActivityPage({ onAsk }: { onAsk: (q: string) => void }) {
  const [answers, err, reload] = usePoll<AnswerRow[]>(api.answers)
  return (
    <Page title="Answers" subtitle="Every answer keeps the source versions it was computed on; later changes to the collections it read mark it stale." onRefresh={reload}>
      {err && <div className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-[12px] text-rose-800">{err}</div>}
      <div className="overflow-hidden rounded-xl border border-line bg-white text-[13px]">
        {answers?.map((a) => (
          <button key={a.answer_id} onClick={() => onAsk(a.question)} className="flex w-full items-start gap-3 border-b border-line px-4 py-3 text-left last:border-0 hover:bg-stone-50">
            <span className="w-20 shrink-0 text-[11px] text-stone-400">{ago(a.created_at)}</span>
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">{a.question}</span>
              <span className="block truncate text-[12px] text-muted">{a.final_answer}</span>
              {a.stale && <span className="mt-1 inline-block rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-800">stale · {a.stale_reason}</span>}
            </span>
            <span className="shrink-0 text-[11px] text-stone-400">{a.collections.slice(0, 2).join(', ')}</span>
          </button>
        ))}
        {answers && !answers.length && <div className="p-8 text-center text-muted">No answers yet.</div>}
      </div>
    </Page>
  )
}
