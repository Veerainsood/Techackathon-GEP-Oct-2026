import { AlertTriangle, Bot, Check, CircleX, Code2, Database, Download, FileSpreadsheet, Gavel, Layers, ShieldCheck, Zap } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { SectionLabel, Spinner } from '../components/ui'
import { api, type AskRequest, type ModelConfig, type StreamEvent } from './api'

const NODES = ['parse_request', 'resolve_sources', 'retrieve_knowledge', 'explorer', 'hercules', 'verify', 'adjudicate', 'llm_adjudicate', 'finalize']
const NODE_LABEL: Record<string, string> = {
  parse_request: 'Parse request',
  resolve_sources: 'Authorize sources · pin versions',
  retrieve_knowledge: 'Load knowledge',
  explorer: 'Explorer investigation',
  hercules: 'Hercules investigation',
  verify: 'Deterministic verification',
  adjudicate: 'Evidence adjudication',
  llm_adjudicate: 'Adjudicator model',
  finalize: 'Finalize · persist · export',
}

type Row = Record<string, unknown>
interface Result {
  answer: string
  verification_status: string
  selected_path: string
  agreement_status: string
  data: Row[]
  query: string
  source_id: string
  query_lineage: string[]
  assumptions: string[]
  validation: { validator: string; passed: boolean; severity: string; message: string }[]
  adjudicator: Record<string, unknown>
  verification_actions: { sql: string; purpose: string; rows: Row[]; error?: string }[]
  consistency?: { status: string; warnings: string[] }
  manifest?: { sources: Record<string, Record<string, unknown>> }
  artifacts: { file_id: string; filename: string; download_url: string; origin?: string }[]
  candidates_summary: Record<string, { mode: string; value: unknown; sql: string; tool_calls: number; llm_usage?: Record<string, number>; failure?: string }>
  errors: Record<string, string>
  total_ms: number
  retried_from?: string | null
}

function fmt(v: unknown): string {
  if (typeof v === 'number') return Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(+v.toFixed(4))
  if (v === null || v === undefined) return '—'
  return typeof v === 'object' ? JSON.stringify(v) : String(v)
}

function DataTable({ rows, max = 50 }: { rows: Row[]; max?: number }) {
  if (!rows?.length) return <div className="text-[12px] text-muted">No rows</div>
  const cols = Object.keys(rows[0])
  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-white">
      <table className="w-full text-[12px]">
        <thead className="bg-stone-50 text-left text-[10px] uppercase tracking-wider text-muted">
          <tr>{cols.map((c) => <th key={c} className="px-2.5 py-1.5 font-medium">{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.slice(0, max).map((r, i) => (
            <tr key={i} className="border-t border-line">
              {cols.map((c) => <td key={c} className="px-2.5 py-1.5 tabular-nums">{fmt(r[c])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > max && <div className="px-2.5 py-1 text-[11px] text-muted">{rows.length - max} more rows</div>}
    </div>
  )
}

function ResultChart({ rows }: { rows: Row[] }) {
  if (!rows || rows.length < 2 || rows.length > 40) return null
  const cols = Object.keys(rows[0])
  const valueCol = cols.includes('value') ? 'value' : cols.find((c) => typeof rows[0][c] === 'number')
  const labelCol = cols.find((c) => c !== valueCol && typeof rows[0][c] !== 'number')
  if (!valueCol || !labelCol) return null
  const data = rows.map((r) => ({ label: String(r[labelCol]), value: Number(r[valueCol]) }))
  return (
    <div className="h-56 rounded-xl border border-line bg-white p-3">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
          <XAxis dataKey="label" tick={{ fontSize: 10 }} interval={0} angle={data.length > 6 ? -25 : 0} height={data.length > 6 ? 50 : 25} />
          <YAxis tick={{ fontSize: 10 }} width={70} />
          <Tooltip formatter={(v) => fmt(v)} />
          <Bar dataKey="value" fill="#0b5cab" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function Sql({ code }: { code: string }) {
  return <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded-md bg-stone-900 px-2.5 py-1.5 font-mono text-[11px] leading-relaxed text-stone-100">{code}</pre>
}

function AgentEvent({ e }: { e: StreamEvent }) {
  const rows = (e.rows as Row[] | undefined) ?? []
  if (e.event === 'tool') {
    const err = (e.error as string | undefined) || (String(e.summary ?? '').startsWith('error') ? String(e.summary) : '')
    return (
      <li className="rounded-lg border border-line bg-white p-2.5">
        <div className="flex items-center gap-1.5 text-[11px]">
          <span className="rounded bg-stone-100 px-1.5 py-0.5 font-mono font-medium text-stone-700">{String(e.tool)}</span>
          {typeof e.latency_ms === 'number' && <span className="text-stone-400">{Math.round(e.latency_ms as number)} ms</span>}
          {(e.provenance as Record<string, unknown> | undefined)?.mode === 'snapshot' && <span className="rounded bg-sky-50 px-1 text-sky-700">snapshot</span>}
          {(e.provenance as Record<string, unknown> | undefined)?.consistent_snapshot === true && <span className="rounded bg-emerald-50 px-1 text-emerald-700">pinned</span>}
        </div>
        <div className="mt-1 text-[12px] text-stone-600">{String(e.objective)}</div>
        {e.query ? <Sql code={String(e.query)} /> : null}
        {e.code ? <Sql code={String(e.code)} /> : null}
        {e.term ? <div className="mt-1 text-[11px] text-muted">term: <b>{String(e.term)}</b> {e.collection ? `in ${String(e.collection)}` : ''}</div> : null}
        {!e.query && !e.code && e.collection ? <div className="mt-1 text-[11px] text-muted">{String(e.collection)}</div> : null}
        {err ? <div className="mt-1 rounded bg-rose-50 px-2 py-1 font-mono text-[11px] text-rose-700">{err}</div> : null}
        {rows.length > 0 && <div className="mt-1.5"><DataTable rows={rows} max={5} /></div>}
        {typeof e.row_count === 'number' && <div className="mt-1 text-[11px] text-muted">{e.row_count as number} rows</div>}
        {e.stdout ? <pre className="mt-1 max-h-40 overflow-auto rounded bg-stone-50 p-2 font-mono text-[11px]">{String(e.stdout)}</pre> : null}
        {!e.query && !e.code && !rows.length && !err && <div className="mt-1 truncate text-[11px] text-stone-400">{String(e.summary ?? '')}</div>}
      </li>
    )
  }
  if (e.event === 'model_text') return <li className="rounded-lg bg-stone-50 px-2.5 py-2 text-[12px] italic text-stone-600">{String(e.text)}</li>
  if (e.event === 'submission')
    return (
      <li className="rounded-lg border border-brand/30 bg-brand-soft/50 p-2.5 text-[12px]">
        <div className="font-medium text-brand">Submitted candidate · {String(e.interpretation ?? '')}</div>
        <Sql code={String(e.sql)} />
        {rows.length > 0 && <div className="mt-1.5"><DataTable rows={rows} max={5} /></div>}
      </li>
    )
  if (e.event === 'submission_rejected') return <li className="rounded-lg bg-rose-50 px-2.5 py-2 text-[12px] text-rose-700">Submission rejected on re-execution: {String(e.error)}</li>
  if (e.event === 'candidate') return <li className="rounded-lg bg-emerald-50 px-2.5 py-2 text-[12px] text-emerald-800">Candidate value: <b>{String(e.value)}</b> · {String(e.mode)}</li>
  if (e.event === 'finished') return null
  return null
}

function AgentColumn({ name, events, mode, model }: { name: string; events: StreamEvent[]; mode?: string; model?: string }) {
  const done = events.some((e) => e.event === 'candidate')
  return (
    <div className="min-w-0 flex-1">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-[13px] font-medium">
          {done ? <Check size={14} className="text-brand" /> : events.length ? <Spinner /> : <Bot size={14} className="text-stone-400" />}
          {name}
        </div>
        <span className="truncate text-[10px] text-muted">{mode === 'llm' ? model : mode ?? ''}</span>
      </div>
      <ol className="space-y-2">{events.map((e, i) => <AgentEvent key={i} e={e} />)}</ol>
      {!events.length && <div className="rounded-lg border border-dashed border-line p-3 text-[12px] text-muted">Waiting…</div>}
    </div>
  )
}

export function LiveThread({ request, onFollowUp }: { request: AskRequest; onFollowUp: (q: string) => void }) {
  const [events, setEvents] = useState<StreamEvent[]>([])
  const [result, setResult] = useState<Result | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [models, setModels] = useState<ModelConfig | null>(null)
  const [follow, setFollow] = useState('')
  const started = useRef(0)

  useEffect(() => {
    api.models().then(setModels).catch(() => setModels(null))
  }, [])

  useEffect(() => {
    const ctrl = new AbortController()
    setEvents([])
    setResult(null)
    setError(null)
    started.current = Date.now()
    api
      .askStream(
        request,
        (e) => {
          if (e.event === 'result') setResult(e.result as Result)
          else if (e.event === 'error') setError(String(e.error))
          else setEvents((prev) => [...prev, e])
        },
        ctrl.signal,
      )
      .catch((err: unknown) => {
        if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : String(err))
      })
    return () => ctrl.abort()
  }, [request])

  const nodeState = useMemo(() => {
    const s: Record<string, { status: string; ms?: number; error?: string }> = {}
    for (const e of events) if (e.event === 'node') s[String(e.node)] = { status: String(e.status), ms: e.ms as number | undefined, error: e.error as string | undefined }
    return s
  }, [events])
  const manifest = events.find((e) => e.event === 'manifest')?.manifest as { sources: Record<string, Record<string, unknown>>; workspace_files?: { filename: string }[] } | undefined
  const byAgent = (a: string) => events.filter((e) => e.agent === a && e.event !== 'started')
  const agentMode = (a: string) => events.find((e) => e.agent === a && e.event === 'started')?.mode as string | undefined
  const adjEvents = events.filter((e) => e.agent === 'Adjudicator')
  const running = !result && !error
  const verified = result?.verification_status === 'verified_fact'

  return (
    <div className="relative flex h-full flex-col">
      <div className="flex items-center justify-between gap-4 border-b border-line bg-paper/80 px-6 py-3 backdrop-blur">
        <div className="flex min-w-0 items-center gap-2 text-[13px]">
          <span className="truncate font-medium">{request.question}</span>
          {request.workspace_id && <span className="shrink-0 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] text-amber-700">uploaded files</span>}
        </div>
        <div className="flex shrink-0 items-center gap-1.5 text-[11px] text-muted">
          {models && (['explorer', 'hercules', 'adjudicator'] as const).map((k) => (
            <span key={k} className="rounded-full border border-line bg-white px-2 py-0.5">
              {k}: {models[k].mode === 'llm' ? models[k].model : 'offline'}
            </span>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-thin">
        <div className="mx-auto max-w-[1100px] px-6 pb-32 pt-6">
          <h1 className="font-serif text-[28px] leading-tight tracking-tight">{request.question}</h1>

          {/* pipeline */}
          <div className="mt-4 flex flex-wrap gap-1.5">
            {NODES.map((n) => {
              const st = nodeState[n]
              const cls = !st ? 'border-line text-stone-400' : st.status === 'finished' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : st.status === 'failed' ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-brand/30 bg-brand-soft text-brand'
              return (
                <span key={n} title={st?.error} className={`flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] ${cls}`}>
                  {st?.status === 'started' && <Spinner />}
                  {NODE_LABEL[n]}
                  {st?.ms !== undefined && st.status === 'finished' && <span className="opacity-60">{st.ms < 1000 ? `${Math.round(st.ms)}ms` : `${(st.ms / 1000).toFixed(1)}s`}</span>}
                </span>
              )
            })}
          </div>

          {manifest && (
            <div className="mt-4 rounded-xl border border-line bg-white p-3 fade-up">
              <SectionLabel icon={<Layers size={14} />}>Pinned source versions (shared by both agents)</SectionLabel>
              <div className="grid grid-cols-1 gap-1.5 text-[12px] md:grid-cols-2">
                {Object.entries(manifest.sources).map(([sid, m]) => (
                  <div key={sid} className="flex items-center justify-between rounded-lg bg-stone-50 px-2.5 py-1.5">
                    <span className="flex items-center gap-1.5 font-medium"><Database size={12} /> {sid}</span>
                    <span className="text-muted">
                      {String(m.data_version ?? 'no sync events')} · {String(m.mode)} · {m.consistency === 'pg_snapshot' && m.snapshot_id ? 'consistent snapshot' : 'change detection'}
                    </span>
                  </div>
                ))}
              </div>
              {manifest.workspace_files?.length ? <div className="mt-2 text-[11px] text-muted">Files: {manifest.workspace_files.map((f) => f.filename).join(', ')}</div> : null}
            </div>
          )}

          {/* agents */}
          <div className="mt-5 flex flex-col gap-4 lg:flex-row">
            <AgentColumn name="Explorer" events={byAgent('Explorer')} mode={agentMode('Explorer')} model={models?.explorer.model} />
            <AgentColumn name="Hercules" events={byAgent('Hercules')} mode={agentMode('Hercules')} model={models?.hercules.model} />
          </div>

          {adjEvents.length > 0 && (
            <div className="mt-5 rounded-xl border border-line bg-white p-3 fade-up">
              <SectionLabel icon={<Gavel size={14} />}>Adjudicator ({models?.adjudicator.model ?? 'model'})</SectionLabel>
              <ol className="space-y-2 text-[12px]">
                {adjEvents.map((e, i) => (
                  <li key={i}>
                    {e.event === 'verdict' && <div><b>{String(e.status)}</b>{e.selected ? ` → ${String(e.selected)}` : ''}: {String(e.rationale ?? '')}</div>}
                    {e.event === 'verification' && (
                      <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-2">
                        <div className="font-medium text-amber-800">Targeted verification · {String(e.purpose)}</div>
                        <Sql code={String(e.sql)} />
                        {e.error ? <div className="text-rose-700">{String(e.error)}</div> : <div className="mt-1"><DataTable rows={(e.rows as Row[]) ?? []} max={8} /></div>}
                      </div>
                    )}
                  </li>
                ))}
              </ol>
            </div>
          )}

          {running && (
            <div className="mt-6 flex items-center gap-2 text-[13px] text-muted">
              <Spinner /> Running on the backend · {events.length} events received
            </div>
          )}
          {error && (
            <div className="mt-6 flex gap-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-[13px] text-rose-800">
              <CircleX size={16} className="mt-0.5 shrink-0" />
              <div><b>Request failed.</b> {error}<div className="mt-1 text-[12px]">Is the backend running? <code>uvicorn udre.api.app:app --port 8000</code></div></div>
            </div>
          )}

          {result && (
            <div className="mt-8 fade-up">
              <SectionLabel icon={<span className="flex h-4 w-4 items-center justify-center rounded bg-brand text-white"><Zap size={10} /></span>}>Answer</SectionLabel>
              <div className={`rounded-2xl border p-4 ${verified ? 'border-emerald-200 bg-white' : 'border-amber-200 bg-amber-50/40'}`}>
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  <span className={`flex items-center gap-1 rounded-full px-2 py-0.5 font-medium ${verified ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-100 text-amber-800'}`}>
                    {verified ? <ShieldCheck size={12} /> : <AlertTriangle size={12} />}{verified ? 'Verified by execution evidence' : 'Not verified / unresolved'}
                  </span>
                  <span className="rounded-full bg-stone-100 px-2 py-0.5 text-stone-600">{result.selected_path} · {result.agreement_status}</span>
                  <span className="text-muted">{(result.total_ms / 1000).toFixed(1)}s</span>
                  {result.retried_from && <span className="rounded-full bg-amber-50 px-2 py-0.5 text-amber-700">retried after a version change</span>}
                </div>
                <p className="mt-3 text-[15px] leading-relaxed text-stone-800">{result.answer}</p>
              </div>

              {result.consistency?.warnings?.length || result.assumptions?.length ? (
                <div className="mt-3 space-y-1.5">
                  {[...(result.consistency?.warnings ?? []), ...result.assumptions.filter((a) => !(result.consistency?.warnings ?? []).includes(a))].slice(0, 8).map((w, i) => (
                    <div key={i} className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50/50 px-3 py-1.5 text-[12px] text-amber-900"><AlertTriangle size={13} className="mt-0.5 shrink-0" />{w}</div>
                  ))}
                </div>
              ) : null}

              {result.data?.length ? (
                <div className="mt-4 space-y-3">
                  <ResultChart rows={result.data} />
                  <DataTable rows={result.data} />
                </div>
              ) : null}

              <div className="mt-5 grid gap-4 md:grid-cols-2">
                <div>
                  <SectionLabel icon={<ShieldCheck size={14} />}>Verification</SectionLabel>
                  <ul className="space-y-1 text-[12px]">
                    {result.validation.filter((v) => v.severity !== 'info' || !v.passed).slice(0, 10).map((v, i) => (
                      <li key={i} className={`rounded px-2 py-1 ${v.severity === 'error' ? 'bg-rose-50 text-rose-800' : 'bg-amber-50 text-amber-900'}`}><b>{v.validator}</b>: {v.message}</li>
                    ))}
                    <li className="text-muted">{result.validation.filter((v) => v.passed).length} checks passed</li>
                  </ul>
                </div>
                <div>
                  <SectionLabel icon={<Bot size={14} />}>Candidates</SectionLabel>
                  <ul className="space-y-1 text-[12px]">
                    {Object.entries(result.candidates_summary).map(([n, c]) => (
                      <li key={n} className="rounded bg-stone-50 px-2 py-1">
                        <b>{n}</b> ({c.mode}): {fmt(c.value)} · {c.tool_calls} tool calls
                        {c.llm_usage?.calls ? ` · ${c.llm_usage.input_tokens + c.llm_usage.output_tokens} tokens` : ''}
                        {c.failure ? <span className="text-rose-700"> · {c.failure}</span> : null}
                      </li>
                    ))}
                    {Object.entries(result.errors).map(([k, v]) => <li key={k} className="rounded bg-rose-50 px-2 py-1 text-rose-800">{k}: {v}</li>)}
                  </ul>
                </div>
              </div>

              <div className="mt-5">
                <SectionLabel icon={<Code2 size={14} />}>Lineage</SectionLabel>
                {result.query ? <Sql code={result.query} /> : <div className="text-[12px] text-muted">No single winning query (unresolved).</div>}
                <div className="mt-1 text-[11px] text-muted">source: {result.source_id}</div>
              </div>

              {result.artifacts.length > 0 && (
                <div className="mt-5">
                  <SectionLabel icon={<FileSpreadsheet size={14} />}>Artifacts</SectionLabel>
                  <div className="flex flex-wrap gap-2">
                    {result.artifacts.map((a) => (
                      <button key={a.file_id} onClick={() => api.download(a.download_url, a.filename)} className="flex items-center gap-1.5 rounded-lg border border-line bg-white px-2.5 py-1.5 text-[12px] hover:border-stone-300">
                        <Download size={13} /> {a.filename} <span className="text-stone-400">{a.origin}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-paper via-paper to-transparent pb-5 pt-10">
        <form
          className="pointer-events-auto mx-auto flex max-w-[760px] gap-2 px-6"
          onSubmit={(ev) => {
            ev.preventDefault()
            if (follow.trim()) onFollowUp(follow.trim())
            setFollow('')
          }}
        >
          <input value={follow} onChange={(e) => setFollow(e.target.value)} placeholder="Ask a follow-up…" className="flex-1 rounded-2xl border border-line bg-white px-4 py-2.5 text-[14px] outline-none focus:border-stone-300" />
          <button disabled={!follow.trim()} className="rounded-2xl bg-brand px-4 text-[13px] font-medium text-white disabled:bg-stone-200 disabled:text-stone-400">Ask</button>
        </form>
      </div>
    </div>
  )
}
