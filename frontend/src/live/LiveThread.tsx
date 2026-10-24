// Live version of components/Thread.tsx: same layout and styling, every value comes from /ask/stream events.
// The conversation is one scrollable thread; how the answer was produced (agents, models, cross-checks) stays internal.
import { Check, ChevronDown, ChevronRight, Copy, Download, Layers, ListChecks, RefreshCw, Share2, ThumbsUp, Zap } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Composer } from '../components/Composer'
import { Callout, SourceCard } from '../components/Thread'
import { SectionLabel, SourceDot, Spinner, StatusPill } from '../components/ui'
import type { Source } from '../data'
import { manifestSource } from './adapter'
import { Charts, type ChartSpec } from './Charts'
import { Markdown } from './Markdown'
import { PrepCard, type PrepState } from './PrepCard'
import { api, type AskRequest, type ModelConfig, type PrepFix, type PrepJob, type RuntimeSource, type StreamEvent } from './api'

type Row = Record<string, unknown>
export interface Detail {
  columns: string[]
  rows: Row[]
  row_count: number
  limited?: boolean
}
interface Result {
  answer: string
  request_id?: string
  verification_status: string
  data: Row[] | Row | number | string | null
  source_id: string
  consistency?: { status: string; warnings: string[] }
  manifest?: { sources: Record<string, Record<string, unknown>> }
  artifacts: { file_id: string; filename: string; download_url: string; origin?: string; size?: number }[]
  errors: Record<string, string>
  total_ms: number
  detail?: Detail | null
  charts?: ChartSpec[] | null
  intent?: {
    route?: 'answer' | 'clarify' | 'investigate' | 'leakage'
    options?: string[]
    queried?: boolean
    brief?: { interpreted_question?: string } | null
  }
}

export interface Turn {
  question: string
  answer: string
  data?: Row[]
  detail?: Detail | null
  charts?: ChartSpec[] | null // planned by the backend; absent on answers saved before chart plans existed
  verified?: boolean
  totalMs?: number
  route?: string
}

interface Props {
  request: AskRequest | null // null: reopened conversation, history only
  history?: Turn[]
  onConversation?: (conversationId: string, workspaceId: string | null) => void
  onComplete?: (turn: Turn) => void
  onRunning?: (running: boolean) => void
  attachments?: string[]
  onRemoveAttachment?: () => void
  onFollowUp: (q: string) => void
  onRerun: () => void
  headerRight?: React.ReactNode
  related: string[]
  known: RuntimeSource[]
  models: ModelConfig | null
  sourceCount?: number
  onAttach?: (files: File[]) => void
  prep?: PrepState | null
  onDecideFix?: (job: PrepJob, fix: PrepFix, approved: boolean) => Promise<void>
}

type Tab = 'answer' | 'actions' | 'sources'

// Pipeline phases shown in the original step list, each bound to the backend graph nodes it covers.
const PHASES: { nodes: string[]; all?: boolean }[] = [
  { nodes: ['parse_request', 'understand_intent'], all: true },
  { nodes: ['resolve_sources'] },
  { nodes: ['retrieve_knowledge'] },
  { nodes: ['explorer', 'hercules'], all: true },
  { nodes: ['verify'] },
  { nodes: ['adjudicate', 'llm_adjudicate', 'targeted_followup'] },
  { nodes: ['respond', 'finalize'] },
  { nodes: ['leakage_scan'] },
]
const INVESTIGATION_PHASES = [0, 1, 2, 3, 4, 5, 6]
// A direct reply or clarifying question only runs the first and last phases; the leakage hunter has its own step.
const DIRECT_PHASES = [0, 6]
const LEAKAGE_PHASES = [0, 7, 6]

const num = (v: unknown): number | null => {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v === 'string' && v.trim() !== '' && !Number.isNaN(Number(v))) return Number(v)
  return null
}
function fmt(v: unknown): string {
  const n = num(v)
  if (n !== null && typeof v !== 'boolean') return Math.abs(n) >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(+n.toFixed(4))
  if (v === null || v === undefined || v === '') return '—'
  return typeof v === 'object' ? JSON.stringify(v) : String(v)
}
const secs = (ms?: number) => (ms === undefined ? '' : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`)
const label = (c: string) => c.replace(/_/g, ' ').replace(/^\w/, (m) => m.toUpperCase())

function DataTable({ columns, rows, max = 25 }: { columns: string[]; rows: Row[]; max?: number }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-white text-[13px]">
      <table className="w-full">
        <thead className="bg-stone-50 text-left text-[11px] uppercase tracking-wider text-muted">
          <tr>
            {columns.map((c) => (
              <th key={c} className="whitespace-nowrap px-3 py-2 font-medium">
                {label(c)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, max).map((r, i) => (
            <tr key={i} className="border-t border-line">
              {columns.map((c) => (
                <td key={c} className={`whitespace-nowrap px-3 py-2.5 ${num(r[c]) !== null ? 'text-right tabular-nums' : ''}`}>
                  {fmt(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** A bar chart (or a line for dates/periods) when the result has one label column and one number column. */
function ResultChart({ rows }: { rows: Row[] }) {
  if (rows.length < 2 || rows.length > 40) return null
  const cols = Object.keys(rows[0])
  const valueCol = cols.includes('value') && rows.every((r) => num(r.value) !== null) ? 'value' : cols.find((c) => rows.every((r) => num(r[c]) !== null))
  const labelCol = cols.find((c) => c !== valueCol && rows.some((r) => num(r[c]) === null))
  if (!valueCol || !labelCol) return null
  const data = rows.map((r) => ({ label: String(r[labelCol] ?? '—'), value: num(r[valueCol]) ?? 0 }))
  const timeLike = data.every((d) => /^\d{4}([-/]\d{1,2})?([-/]\d{1,2})?$|^\d{4}-?Q\d$|^[A-Z][a-z]{2}[- ]\d{2,4}$/.test(d.label))
  const tick = { fontSize: 11, fill: '#6b6a66' }
  return (
    <div className="rounded-2xl border border-line bg-white p-4">
      <div className="mb-2 text-[13px] font-medium">
        {label(valueCol === 'value' ? 'value' : valueCol)} by {label(labelCol).toLowerCase()}
      </div>
      <div className="h-60">
        <ResponsiveContainer width="100%" height="100%">
          {timeLike ? (
            <LineChart data={data} margin={{ top: 8, right: 8, left: 4, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e8e6e1" vertical={false} />
              <XAxis dataKey="label" tick={tick} />
              <YAxis tick={tick} width={70} tickFormatter={(v) => fmt(v)} />
              <Tooltip formatter={(v) => fmt(v)} />
              <Line type="monotone" dataKey="value" stroke="#0b5cab" strokeWidth={2} dot={{ r: 3 }} />
            </LineChart>
          ) : (
            <BarChart data={data} margin={{ top: 8, right: 8, left: 4, bottom: data.length > 6 ? 40 : 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e8e6e1" vertical={false} />
              <XAxis dataKey="label" tick={tick} interval={0} angle={data.length > 6 ? -30 : 0} textAnchor={data.length > 6 ? 'end' : 'middle'} />
              <YAxis tick={tick} width={70} tickFormatter={(v) => fmt(v)} />
              <Tooltip formatter={(v) => fmt(v)} cursor={{ fill: '#e8f0fa' }} />
              <Bar dataKey="value" fill="#0b5cab" radius={[4, 4, 0, 0]} maxBarSize={56} />
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  )
}

/** The answer, a short verification line, and the data behind it (chart and table, or the matching records). */
function AnswerBlock({ turn }: { turn: Turn }) {
  if (!turn.answer)
    return (
      <div className="flex items-center gap-2 text-[13px] text-muted">
        <Spinner /> <span className="shimmer-text">Still answering…</span>
      </div>
    )
  const rows = (turn.data ?? []).filter((r) => r && typeof r === 'object')
  const grouped = rows.length > 1 || (rows.length === 1 && Object.keys(rows[0]).length > 1)
  const detail = turn.detail && turn.detail.rows?.length ? turn.detail : null
  return (
    <div className="space-y-4">
      <div className="space-y-3 text-[15px] leading-[1.7] text-stone-800">
        <Markdown text={turn.answer} />
      </div>
      {turn.route === 'investigate' || turn.verified !== undefined ? (
        <div className="flex items-center gap-1.5 text-[12px]">
          {turn.verified ? (
            <span className="flex items-center gap-1 text-emerald-700">
              <Check size={13} strokeWidth={2.5} /> Verified against the live data
            </span>
          ) : (
            <span className="text-muted">From the live data</span>
          )}
          {turn.totalMs !== undefined && <span className="text-muted">· {secs(turn.totalMs)}</span>}
        </div>
      ) : null}
      {Array.isArray(turn.charts) ? <Charts charts={turn.charts} /> : grouped && <ResultChart rows={rows} />}
      {grouped && <DataTable columns={Object.keys(rows[0])} rows={rows} />}
      {detail && (
        <div>
          <div className="mb-2 text-[13px] font-medium">
            {detail.limited ? `First ${detail.rows.length} matching records` : `${detail.row_count} matching record${detail.row_count === 1 ? '' : 's'}`}
          </div>
          <DataTable columns={detail.columns} rows={detail.rows} />
        </div>
      )}
    </div>
  )
}

export function LiveThread({
  request,
  history = [],
  onConversation,
  onComplete,
  onRunning,
  attachments,
  onRemoveAttachment,
  onFollowUp,
  onRerun,
  headerRight,
  related,
  known,
  models,
  sourceCount,
  onAttach,
  prep,
  onDecideFix,
}: Props) {
  const [events, setEvents] = useState<StreamEvent[]>([])
  const [result, setResult] = useState<Result | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [stepsOpen, setStepsOpen] = useState(true)
  const [tab, setTab] = useState<Tab>('answer')
  const query = request?.question ?? history[0]?.question ?? (prep ? 'Uploaded files' : '')
  const callbacks = useRef({ onConversation, onComplete, onRunning })
  callbacks.current = { onConversation, onComplete, onRunning }
  const scrollRef = useRef<HTMLDivElement>(null)
  const currentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!request) return
    const ctrl = new AbortController()
    setEvents([])
    setResult(null)
    setError(null)
    setStepsOpen(true)
    setTab('answer')
    callbacks.current.onRunning?.(true)
    api
      .askStream(
        request,
        (e) => {
          if (e.event === 'result') {
            const r = e.result as Result
            setResult(r)
            callbacks.current.onRunning?.(false)
            callbacks.current.onComplete?.({
              question: request.question,
              answer: r.answer,
              data: Array.isArray(r.data) ? r.data : [],
              detail: r.detail ?? null,
              charts: r.charts,
              verified: r.verification_status === 'verified_fact',
              totalMs: r.total_ms,
              route: r.intent?.route,
            })
          } else if (e.event === 'conversation') {
            callbacks.current.onConversation?.(String(e.conversation_id), (e.workspace_id as string | null) ?? null)
          } else if (e.event === 'error') {
            setError(String(e.error))
            callbacks.current.onRunning?.(false)
          } else setEvents((prev) => [...prev, e])
        },
        ctrl.signal,
      )
      .catch((err: unknown) => {
        if (!ctrl.signal.aborted) setError(err instanceof Error ? err.message : String(err))
        callbacks.current.onRunning?.(false)
      })
    return () => ctrl.abort()
  }, [request])

  // A new question scrolls to itself; a reopened conversation scrolls to its latest turn.
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    if (request && currentRef.current) currentRef.current.scrollIntoView({ block: 'start' })
    else el.scrollTop = el.scrollHeight
  }, [request, history.length])

  const done = !!result || !!error || !request
  useEffect(() => {
    if (!done) return
    const t = setTimeout(() => setStepsOpen(false), 600)
    return () => clearTimeout(t)
  }, [done])

  const nodes = useMemo(() => {
    const s: Record<string, { status: string; ms?: number; error?: string }> = {}
    for (const e of events) if (e.event === 'node') s[String(e.node)] = { status: String(e.status), ms: e.ms as number | undefined, error: e.error as string | undefined }
    return s
  }, [events])

  const manifest = (events.find((e) => e.event === 'manifest')?.manifest ?? result?.manifest) as { sources: Record<string, Record<string, unknown>> } | undefined
  const srcList: Source[] = useMemo(
    () => Object.entries(manifest?.sources ?? {}).map(([sid, m], i) => manifestSource(sid, m, i, known.find((k) => k.source_id === sid))),
    [manifest, known],
  )
  const tools = events.filter((e) => e.event === 'tool')
  const lastTool = tools[tools.length - 1]

  const phaseState = PHASES.map((p) => {
    const st = p.nodes.map((n) => nodes[n]?.status)
    const finished = st.filter((s) => s === 'finished' || s === 'failed').length
    const running = st.some((s) => s === 'started')
    const isDone = done || (p.all ? finished === p.nodes.length : finished > 0 && !running)
    return { isDone, running, ms: p.nodes.reduce((t, n) => t + (nodes[n]?.ms ?? 0), 0), error: p.nodes.map((n) => nodes[n]?.error).find(Boolean) }
  })
  const intentEvent = events.find((e) => e.agent === 'Intent' && e.event === 'decision')
  const route = (result?.intent?.route ?? intentEvent?.route) as string | undefined
  const leakage = route === 'leakage'
  const direct = route === 'answer' || route === 'clarify'
  const visible = leakage ? LEAKAGE_PHASES : direct ? DIRECT_PHASES : INVESTIGATION_PHASES
  const stepPos = done ? visible.length : Math.max(0, visible.findIndex((i) => !phaseState[i].isDone))

  const fresh = srcList.filter((s) => !s.stale).length
  const routeDetail =
    route === 'answer'
      ? 'Answered from what the system knows about the data'
      : route === 'clarify'
        ? 'Needs a clarifying question before querying the data'
        : route === 'investigate'
          ? result?.intent?.brief?.interpreted_question ?? 'Data question'
          : leakage
            ? 'Find spend leakage'
            : 'Understanding the question…'
  const steps = [
    { title: 'Understanding the question', detail: phaseState[0].error ?? routeDetail },
    {
      title: `Reading ${srcList.length || '…'} connected source${srcList.length === 1 ? '' : 's'}`,
      detail: srcList.length ? `${fresh} current${srcList.length - fresh ? ` · ${srcList.length - fresh} stale` : ''} · data versions pinned` : 'Authorizing sources',
    },
    { title: 'Loading what is known about the data', detail: phaseState[2].error ?? `Tables, metrics and verified relationships · ${secs(phaseState[2].ms)}` },
    {
      title: 'Analysing the data',
      detail: phaseState[3].isDone ? `${tools.length} queries and lookups` : lastTool ? String(lastTool.objective ?? lastTool.tool) : 'Planning queries',
    },
    { title: 'Validating the result', detail: 'Joins, filters, units and completeness checked' },
    { title: 'Verifying the answer', detail: 'Checked against the live data' },
    direct
      ? { title: route === 'clarify' ? 'Asking you to choose' : 'Replying', detail: 'No queries needed' }
      : { title: 'Saving the result', detail: result ? `${result.artifacts.length} download${result.artifacts.length === 1 ? '' : 's'} ready` : 'Lineage and exports' },
    {
      title: attachments?.length ? `Hunting for leakage across ${attachments.length} file${attachments.length === 1 ? '' : 's'}` : 'Hunting for leakage',
      detail: phaseState[7].error ?? 'Duplicates, prices against contracts, early-payment discounts, overpayments and unused licences',
    },
  ]
  const shown = visible.map((i) => ({ ...steps[i], phase: i }))

  const current: Turn | null = result
    ? {
        question: request?.question ?? '',
        answer: result.answer,
        data: Array.isArray(result.data) ? result.data : [],
        detail: result.detail ?? null,
        charts: result.charts,
        verified: direct ? undefined : result.verification_status === 'verified_fact',
        totalMs: direct ? undefined : result.total_ms,
        route: result.intent?.route,
      }
    : null
  const warnings = result?.consistency?.warnings ?? []
  const clarifyOptions = (result?.intent?.options ?? []) as string[]
  void models

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      {/* top bar */}
      <div className="flex items-center justify-between gap-4 border-b border-line bg-paper/80 px-6 py-3 backdrop-blur">
        <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden text-[13px] text-muted">
          <span className="min-w-0 truncate font-medium text-ink">{query}</span>
          <span className="shrink-0 whitespace-nowrap rounded-full bg-brand-soft px-2 py-0.5 text-[11px] font-medium text-brand">L4 · Orchestrate</span>
        </div>
        <div className="flex shrink-0 items-center gap-1 whitespace-nowrap">
          <button onClick={onRerun} className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-stone-600 hover:bg-stone-100">
            <RefreshCw size={13} /> <span className="hidden xl:inline">Re-run</span>
          </button>
          <button
            onClick={() => result?.artifacts[0] && api.download(result.artifacts[0].download_url, result.artifacts[0].filename)}
            className="flex items-center gap-1.5 rounded-lg bg-ink px-3 py-1.5 text-xs font-medium text-white hover:bg-stone-800"
          >
            <Share2 size={13} /> Share
          </button>
          {headerRight && <span className="ml-1">{headerRight}</span>}
        </div>
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
        <div className="mx-auto max-w-[760px] px-6 pb-40 pt-8">
          {prep && onDecideFix && <PrepCard prep={prep} onDecide={onDecideFix} />}
          {/* earlier turns of this conversation */}
          {history.map((t, i) => (
            <section key={i} className="mb-10 border-b border-line pb-8">
              <h2 className="mb-4 font-serif text-[24px] leading-tight tracking-tight">{t.question}</h2>
              <AnswerBlock turn={t} />
            </section>
          ))}

          {request && (
            <div ref={currentRef}>
              <h1 className="font-serif text-[32px] leading-tight tracking-tight">{request.question}</h1>

              {/* tabs */}
              <div className="mt-5 flex items-center gap-5 border-b border-line text-[13px]">
                {(
                  [
                    ['answer', 'Answer', Zap],
                    ['actions', `Downloads · ${result?.artifacts.length ?? 0}`, ListChecks],
                    ['sources', `Sources · ${srcList.length}`, Layers],
                  ] as const
                ).map(([id, text, Icon]) => (
                  <button
                    key={id}
                    onClick={() => setTab(id)}
                    disabled={!done && id !== 'answer'}
                    className={`-mb-px flex items-center gap-1.5 border-b-2 pb-2.5 transition disabled:opacity-40 ${
                      tab === id ? 'border-ink font-medium text-ink' : 'border-transparent text-muted hover:text-ink'
                    }`}
                  >
                    <Icon size={14} />
                    {text}
                  </button>
                ))}
              </div>

              {tab === 'answer' && (
                <div className="pt-5">
                  {/* steps */}
                  <div className="rounded-xl border border-line bg-white">
                    <button onClick={() => setStepsOpen((o) => !o)} className="flex w-full items-center justify-between px-4 py-3 text-[13px]">
                      <span className="flex items-center gap-2">
                        {done ? <Check size={15} className="text-brand" /> : <Spinner />}
                        {done ? (
                          <span className="font-medium">Completed{result ? ` in ${secs(result.total_ms)}` : ''}</span>
                        ) : (
                          <span className="shimmer-text font-medium">{shown[Math.min(stepPos, shown.length - 1)].title}…</span>
                        )}
                      </span>
                      {stepsOpen ? <ChevronDown size={15} className="text-muted" /> : <ChevronRight size={15} className="text-muted" />}
                    </button>
                    {stepsOpen && (
                      <ol className="border-t border-line px-4 py-3">
                        {shown.map((s, i) => {
                          const state = i < stepPos ? 'done' : i === stepPos ? 'active' : 'todo'
                          return (
                            <li key={s.title} className={`relative flex gap-3 pb-3 last:pb-0 ${state === 'todo' ? 'opacity-35' : ''}`}>
                              {i < shown.length - 1 && <span className="absolute left-[7px] top-5 h-[calc(100%-12px)] w-px bg-line" />}
                              <span className="relative z-10 mt-0.5 bg-white">
                                {state === 'done' ? (
                                  <span className="flex h-[15px] w-[15px] items-center justify-center rounded-full bg-brand">
                                    <Check size={10} className="text-white" strokeWidth={3} />
                                  </span>
                                ) : state === 'active' ? (
                                  <Spinner />
                                ) : (
                                  <span className="block h-[15px] w-[15px] rounded-full border border-stone-300" />
                                )}
                              </span>
                              <div className="text-[13px]">
                                <div className="font-medium">{s.title}</div>
                                {state !== 'todo' && <div className="text-[12px] text-muted fade-up">{s.detail}</div>}
                                {state === 'done' && s.phase === 1 && (
                                  <div className="mt-1.5 flex flex-wrap gap-1.5 fade-up">
                                    {srcList.map((src) => (
                                      <span
                                        key={src.id}
                                        className={`flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] ${
                                          src.stale ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-line bg-stone-50 text-stone-600'
                                        }`}
                                      >
                                        <SourceDot color={src.color} letter={src.name[0]} kind={src.kind} />
                                        {src.name}
                                      </span>
                                    ))}
                                  </div>
                                )}
                              </div>
                            </li>
                          )
                        })}
                      </ol>
                    )}
                  </div>

                  {error && (
                    <div className="mt-6 space-y-2 fade-up">
                      <Callout tone="rose">
                        <b>The request could not be completed.</b> {error}
                      </Callout>
                    </div>
                  )}

                  {current && (
                    <div className="mt-8 fade-up">
                      <SectionLabel
                        icon={
                          <span className="flex h-4 w-4 items-center justify-center rounded bg-brand text-white">
                            <Zap size={10} />
                          </span>
                        }
                      >
                        Answer
                      </SectionLabel>
                      <AnswerBlock turn={current} />

                      {(warnings.length > 0 || Object.keys(result?.errors ?? {}).length > 0) && (
                        <div className="mt-4 space-y-2">
                          {warnings.slice(0, 3).map((w) => (
                            <Callout key={w} tone="amber">
                              {w}
                            </Callout>
                          ))}
                        </div>
                      )}

                      <div className="mt-6 flex items-center gap-1 text-muted">
                        {[
                          { I: Copy, on: () => navigator.clipboard?.writeText(current.answer) },
                          { I: ThumbsUp, on: () => undefined },
                          { I: RefreshCw, on: onRerun },
                        ].map(({ I, on }, i) => (
                          <button key={i} onClick={on} className="rounded-lg p-2 hover:bg-stone-100 hover:text-ink">
                            <I size={15} />
                          </button>
                        ))}
                        <span className="ml-auto text-[11px]">
                          run {(result?.request_id ?? '').slice(0, 8)} · {new Date().toTimeString().slice(0, 5)} IST
                        </span>
                      </div>

                      {/* related, or the clarification options */}
                      {(route === 'clarify' ? clarifyOptions.length > 0 : related.length > 0) && (
                        <div className="mt-8 border-t border-line pt-5">
                          <SectionLabel icon={<Layers size={15} />}>{route === 'clarify' ? 'Choose one' : 'Related'}</SectionLabel>
                          {(route === 'clarify' ? clarifyOptions : related.slice(0, 4)).map((q) => (
                            <button
                              key={q}
                              onClick={() => onFollowUp(route === 'clarify' ? `${request.question} (${q})` : q)}
                              className="flex w-full items-center justify-between border-b border-line py-2.5 text-left text-[14px] text-stone-700 last:border-0 hover:text-brand"
                            >
                              {q}
                              <span className="text-lg leading-none text-stone-400">+</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {tab === 'actions' && (
                <div className="space-y-2 pt-5 fade-up">
                  {(result?.artifacts ?? []).map((a) => (
                    <div
                      key={a.file_id}
                      onClick={() => api.download(a.download_url, a.filename)}
                      className="group flex cursor-pointer items-center gap-3 rounded-xl border border-line bg-white px-4 py-3 transition hover:border-stone-300 hover:shadow-sm"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-[11px] font-medium text-stone-400">{a.filename.split('.').pop()?.toUpperCase()}</span>
                          <StatusPill status="verified" />
                        </div>
                        <div className="mt-1 text-[14px] font-medium">Download {a.filename}</div>
                        <div className="mt-0.5 truncate text-[12px] text-muted">
                          The verified result{a.size ? ` — ${(a.size / 1024).toFixed(1)} KB` : ''}
                        </div>
                      </div>
                      <Download size={16} className="shrink-0 text-stone-300 group-hover:text-stone-500" />
                    </div>
                  ))}
                </div>
              )}

              {tab === 'sources' && (
                <div className="grid grid-cols-2 gap-3 pt-5 fade-up">
                  {srcList.map((s) => (
                    <SourceCard key={s.id} s={s} large />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* follow-up composer */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-paper via-paper to-transparent pb-5 pt-10">
        <div className="pointer-events-auto mx-auto max-w-[760px] px-6">
          <Composer
            compact
            onSubmit={onFollowUp}
            placeholder="Ask a follow-up or refine the goal…"
            sourceCount={sourceCount}
            onAttach={onAttach}
            attachments={attachments}
            onRemoveAttachment={onRemoveAttachment}
          />
        </div>
      </div>
    </div>
  )
}
