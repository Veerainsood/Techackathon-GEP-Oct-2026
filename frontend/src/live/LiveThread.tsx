// Live version of components/Thread.tsx: same layout and styling, every value comes from /ask/stream events.
import { AlertTriangle, Check, ChevronDown, ChevronRight, Copy, Download, GitMerge, Layers, ListChecks, RefreshCw, Share2, ThumbsUp, Zap } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Composer } from '../components/Composer'
import { Callout, SourceCard } from '../components/Thread'
import { Cite, SectionLabel, SourceDot, Spinner, StatusPill } from '../components/ui'
import type { Source } from '../data'
import { manifestSource } from './adapter'
import { api, type AskRequest, type ModelConfig, type RuntimeSource, type StreamEvent } from './api'

type Row = Record<string, unknown>
interface Result {
  answer: string
  request_id?: string
  verification_status: string
  selected_path: string
  agreement_status: string
  confidence_score?: number
  data: Row[] | Row | number | string | null
  query: string
  source_id: string
  assumptions: string[]
  validation: { validator: string; passed: boolean; severity: string; message: string }[]
  adjudicator: { status?: string; selected_agent?: string; rationale?: string; model?: string; disagreement?: string }
  verification_actions: { sql: string; purpose: string; rows?: Row[]; error?: string }[]
  consistency?: { status: string; warnings: string[] }
  manifest?: { sources: Record<string, Record<string, unknown>> }
  artifacts: { file_id: string; filename: string; download_url: string; origin?: string; size?: number }[]
  candidates_summary: Record<string, { mode: string; value: unknown; sql: string; tool_calls: number; llm_usage?: Record<string, number>; failure?: string | null }>
  errors: Record<string, string>
  total_ms: number
  retried_from?: string | null
}

interface Props {
  request: AskRequest
  onFollowUp: (q: string) => void
  onRerun: () => void
  headerRight?: React.ReactNode
  related: string[]
  known: RuntimeSource[]
  models: ModelConfig | null
  sourceCount?: number
  onAttach?: (files: File[]) => void
}

type Tab = 'answer' | 'actions' | 'sources'

// Pipeline phases shown in the original step list, each bound to the backend graph nodes it covers.
const PHASES: { nodes: string[]; all?: boolean }[] = [
  { nodes: ['parse_request'] },
  { nodes: ['resolve_sources'] },
  { nodes: ['retrieve_knowledge'] },
  { nodes: ['explorer', 'hercules'], all: true },
  { nodes: ['verify'] },
  { nodes: ['adjudicate', 'llm_adjudicate', 'targeted_followup'] },
  { nodes: ['finalize'] },
]

function fmt(v: unknown): string {
  if (typeof v === 'number') return Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(+v.toFixed(4))
  if (v === null || v === undefined) return '—'
  if (Array.isArray(v)) return v.length === 1 && v[0] && typeof v[0] === 'object' ? fmt(Object.values(v[0] as Row)[0]) : `${v.length} rows`
  return typeof v === 'object' ? JSON.stringify(v) : String(v)
}
const secs = (ms?: number) => (ms === undefined ? '' : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`)
const modelName = (m: ModelConfig | null, k: 'explorer' | 'hercules' | 'adjudicator') =>
  m ? (m[k].mode === 'llm' ? m[k].model ?? 'model' : 'deterministic offline') : k

export function LiveThread({ request, onFollowUp, onRerun, headerRight, related, known, models, sourceCount, onAttach }: Props) {
  const [events, setEvents] = useState<StreamEvent[]>([])
  const [result, setResult] = useState<Result | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [stepsOpen, setStepsOpen] = useState(true)
  const [tab, setTab] = useState<Tab>('answer')
  const query = request.question

  useEffect(() => {
    const ctrl = new AbortController()
    setEvents([])
    setResult(null)
    setError(null)
    setStepsOpen(true)
    setTab('answer')
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

  const done = !!result || !!error
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
  const cand = (a: string) => events.find((e) => e.agent === a && e.event === 'candidate')
  const verdict = [...events].reverse().find((e) => e.agent === 'Adjudicator' && e.event === 'verdict')
  const toolCount = (a: string) => tools.filter((t) => t.agent === a).length

  const phaseState = PHASES.map((p) => {
    const st = p.nodes.map((n) => nodes[n]?.status)
    const finished = st.filter((s) => s === 'finished' || s === 'failed').length
    const running = st.some((s) => s === 'started')
    const isDone = done || (p.all ? finished === p.nodes.length : finished > 0 && !running)
    return { isDone, running, ms: p.nodes.reduce((t, n) => t + (nodes[n]?.ms ?? 0), 0), error: p.nodes.map((n) => nodes[n]?.error).find(Boolean) }
  })
  const stepIdx = done ? PHASES.length : Math.max(0, phaseState.findIndex((p) => !p.isDone))

  const fresh = srcList.filter((s) => !s.stale).length
  const passed = result?.validation.filter((v) => v.passed).length ?? 0
  const steps = [
    { title: 'Interpreting question', detail: phaseState[0].error ?? `Parsed in ${secs(phaseState[0].ms)} · dual-path investigation` },
    {
      title: `Reading ${srcList.length || '…'} connected source${srcList.length === 1 ? '' : 's'}`,
      detail: srcList.length ? `${fresh} current${srcList.length - fresh ? ` · ${srcList.length - fresh} stale` : ''} · versions pinned for both agents` : 'Authorizing sources',
    },
    { title: 'Loading verified knowledge', detail: phaseState[2].error ?? `Ontology, metrics and skill files · ${secs(phaseState[2].ms)}` },
    {
      title: 'Running Explorer and Hercules',
      detail: phaseState[3].isDone
        ? `Explorer ${toolCount('Explorer')} tool calls → ${fmt(cand('Explorer')?.value)} · Hercules ${toolCount('Hercules')} → ${fmt(cand('Hercules')?.value)}`
        : lastTool
          ? `${String(lastTool.agent)}: ${String(lastTool.objective ?? lastTool.tool)}`
          : `${modelName(models, 'explorer')} · ${modelName(models, 'hercules')}`,
    },
    { title: 'Validating candidate answers', detail: result ? `${passed}/${result.validation.length} deterministic checks passed` : 'Deterministic validators' },
    {
      title: 'Adjudicating the evidence',
      detail: verdict ? `${modelName(models, 'adjudicator')}: ${String(verdict.status)}${verdict.selected ? ` → ${String(verdict.selected)}` : ''}` : 'Comparing executed results',
    },
    { title: 'Checking consistency & saving', detail: result ? `${result.consistency?.status ?? 'consistent'} · ${result.artifacts.length} artifact${result.artifacts.length === 1 ? '' : 's'}` : 'Version check, lineage, exports' },
  ]

  const data: Row[] = Array.isArray(result?.data) ? (result!.data as Row[]) : []
  const cols = data.length ? Object.keys(data[0]) : []
  const cs = result?.candidates_summary ?? {}
  const verified = result?.verification_status === 'verified_fact'
  const tokens = Object.values(cs).reduce((t, c) => t + (c.llm_usage?.input_tokens ?? 0) + (c.llm_usage?.output_tokens ?? 0), 0)
  const warnings = [...(result?.consistency?.warnings ?? []), ...(result?.assumptions ?? [])].filter((w, i, a) => a.indexOf(w) === i)
  const disagreement = result && result.agreement_status !== 'exact_match'
  const cite = srcList.findIndex((s) => known.find((k) => k.source_id === result?.source_id)?.name === s.name || s.name === result?.source_id) + 1

  return (
    <div className="relative flex h-full flex-col">
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

      <div className="flex-1 overflow-y-auto scrollbar-thin">
        <div className="mx-auto max-w-[760px] px-6 pb-40 pt-8">
          <h1 className="font-serif text-[32px] leading-tight tracking-tight">{query}</h1>

          {/* tabs */}
          <div className="mt-5 flex items-center gap-5 border-b border-line text-[13px]">
            {(
              [
                ['answer', 'Answer', Zap],
                ['actions', `Actions · ${result?.artifacts.length ?? 0}`, ListChecks],
                ['sources', `Sources · ${srcList.length}`, Layers],
              ] as const
            ).map(([id, label, Icon]) => (
              <button
                key={id}
                onClick={() => setTab(id)}
                disabled={!done && id !== 'answer'}
                className={`-mb-px flex items-center gap-1.5 border-b-2 pb-2.5 transition disabled:opacity-40 ${
                  tab === id ? 'border-ink font-medium text-ink' : 'border-transparent text-muted hover:text-ink'
                }`}
              >
                <Icon size={14} />
                {label}
              </button>
            ))}
          </div>

          {tab === 'answer' && (
            <div className="pt-5">
              {/* agent steps */}
              <div className="rounded-xl border border-line bg-white">
                <button onClick={() => setStepsOpen((o) => !o)} className="flex w-full items-center justify-between px-4 py-3 text-[13px]">
                  <span className="flex items-center gap-2">
                    {done ? <Check size={15} className="text-brand" /> : <Spinner />}
                    {done ? (
                      <span className="font-medium">
                        Completed {steps.length} steps · 2 agents · {tools.length} tool calls · {secs(result?.total_ms)}
                      </span>
                    ) : (
                      <span className="shimmer-text font-medium">{steps[Math.min(stepIdx, steps.length - 1)].title}…</span>
                    )}
                  </span>
                  {stepsOpen ? <ChevronDown size={15} className="text-muted" /> : <ChevronRight size={15} className="text-muted" />}
                </button>
                {stepsOpen && (
                  <ol className="border-t border-line px-4 py-3">
                    {steps.map((s, i) => {
                      const state = i < stepIdx ? 'done' : i === stepIdx ? 'active' : 'todo'
                      return (
                        <li key={s.title} className={`relative flex gap-3 pb-3 last:pb-0 ${state === 'todo' ? 'opacity-35' : ''}`}>
                          {i < steps.length - 1 && <span className="absolute left-[7px] top-5 h-[calc(100%-12px)] w-px bg-line" />}
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
                            {state === 'done' && i === 1 && (
                              <div className="mt-1.5 flex flex-wrap gap-1.5 fade-up">
                                {srcList.map((src) => (
                                  <span
                                    key={src.id}
                                    className={`flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] ${
                                      src.stale ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-line bg-stone-50 text-stone-600'
                                    }`}
                                  >
                                    <SourceDot color={src.color} letter={src.name[0]} />
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

              {stepIdx >= 2 && srcList.length > 0 && (
                <div className="mt-6 fade-up">
                  <SectionLabel icon={<Layers size={15} />}>Sources</SectionLabel>
                  <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
                    {srcList.slice(0, 3).map((s) => (
                      <SourceCard key={s.id} s={s} />
                    ))}
                    {srcList.length > 3 && (
                      <button
                        onClick={() => done && setTab('sources')}
                        className="flex flex-col justify-between rounded-xl border border-line bg-white/60 p-2.5 text-left hover:bg-white"
                      >
                        <div className="flex -space-x-1">
                          {srcList.slice(3).map((s) => (
                            <SourceDot key={s.id} color={s.color} letter={s.name[0]} />
                          ))}
                        </div>
                        <div className="text-[12px] text-muted">
                          +{srcList.length - 3} more
                          {srcList.slice(3).some((s) => s.stale) && <> · <span className="text-rose-600">{srcList.slice(3).filter((s) => s.stale).length} stale</span></>}
                        </div>
                      </button>
                    )}
                  </div>
                </div>
              )}

              {error && (
                <div className="mt-6 space-y-2 fade-up">
                  <Callout tone="rose">
                    <b>The backend could not complete this request.</b> {error}
                  </Callout>
                </div>
              )}

              {result && (
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

                  <div className="space-y-4 text-[15px] leading-[1.7] text-stone-800">
                    <p>
                      {result.answer}
                      {cite > 0 && <Cite ids={[cite]} list={srcList} />}
                    </p>
                    <p>
                      {disagreement ? (
                        <>
                          Explorer and Hercules <b>did not fully agree</b> ({result.agreement_status.replace(/_/g, ' ')}); the adjudicator
                          {result.adjudicator.selected_agent ? <> selected <b>{result.adjudicator.selected_agent}</b></> : <> left it <b>unresolved</b></>}.
                        </>
                      ) : (
                        <>
                          Explorer and Hercules reached this <b>independently</b> and their executed results match.
                        </>
                      )}{' '}
                      {verified ? (
                        <>The answer is <b className="text-brand">verified by execution evidence</b>.</>
                      ) : (
                        <>It is <b className="text-rose-700">not verified</b> ({result.verification_status.replace(/_/g, ' ')}).</>
                      )}
                    </p>
                  </div>

                  {/* KPIs */}
                  <div className="mt-5 grid grid-cols-2 divide-x divide-line md:grid-cols-4 overflow-hidden rounded-2xl border border-line bg-white">
                    {[
                      { l: 'Answer', v: fmt(cs.Explorer?.value ?? cs.Hercules?.value ?? result.data), t: verified ? 'Verified' : 'Not verified', c: verified ? 'text-emerald-700' : 'text-amber-700' },
                      { l: 'Agreement', v: result.agreement_status.replace(/_/g, ' '), t: result.selected_path.replace(/_/g, ' '), c: disagreement ? 'text-amber-700' : 'text-muted' },
                      { l: 'Confidence', v: result.confidence_score !== undefined ? `${Math.round(result.confidence_score * 100)}%` : '—', t: 'Evidence-weighted', c: 'text-muted' },
                      { l: 'Run time', v: secs(result.total_ms), t: `${tools.length} tool calls · ${tokens.toLocaleString()} tokens`, c: 'text-muted' },
                    ].map((k) => (
                      <div key={k.l} className="p-3.5">
                        <div className="text-[11px] text-muted">{k.l}</div>
                        <div className="mt-0.5 truncate text-xl font-semibold tracking-tight">{k.v}</div>
                        <div className={`text-[11px] ${k.c}`}>{k.t}</div>
                      </div>
                    ))}
                  </div>

                  {/* warnings */}
                  {(warnings.length > 0 || Object.keys(result.errors).length > 0) && (
                    <div className="mt-4 space-y-2">
                      {Object.entries(result.errors).map(([k, v]) => (
                        <Callout key={k} tone="rose">
                          <b>{k.replace('_error', '')} failed.</b> {v}
                        </Callout>
                      ))}
                      {warnings.slice(0, 4).map((w) => (
                        <Callout key={w} tone="amber">
                          {w}
                        </Callout>
                      ))}
                    </div>
                  )}

                  {/* result rows */}
                  {data.length > 0 && (cols.length > 1 || data.length > 1) && (
                    <>
                      <h3 className="mb-2 mt-8 text-[17px] font-semibold tracking-tight">Result</h3>
                      <p className="mb-3 text-[14px] text-stone-600">
                        {data.length} row{data.length === 1 ? '' : 's'} from <b>{result.source_id}</b>, re-executed by the engine
                        {cite > 0 && <Cite ids={[cite]} list={srcList} />}.
                      </p>
                      <div className="overflow-x-auto rounded-xl border border-line bg-white text-[13px]">
                        <table className="w-full">
                          <thead className="bg-stone-50 text-left text-[11px] uppercase tracking-wider text-muted">
                            <tr>
                              {cols.map((c) => (
                                <th key={c} className="px-3 py-2 font-medium">
                                  {c}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {data.slice(0, 25).map((r, i) => (
                              <tr key={i} className="border-t border-line">
                                {cols.map((c) => (
                                  <td key={c} className={`px-3 py-2.5 ${typeof r[c] === 'number' ? 'text-right tabular-nums' : ''}`}>
                                    {fmt(r[c])}
                                  </td>
                                ))}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}

                  {/* conflict */}
                  <h3 className="mb-2 mt-8 text-[17px] font-semibold tracking-tight">
                    {disagreement ? 'Resolving a conflict between agents' : 'Cross-checking the two agents'}
                  </h3>
                  <div className="rounded-xl border border-line bg-white p-4">
                    <div className="grid grid-cols-2 gap-3 text-[13px]">
                      {(['Explorer', 'Hercules'] as const).map((a) => (
                        <div key={a} className="rounded-lg bg-stone-50 p-3">
                          <div className="text-[11px] font-medium uppercase tracking-wider text-muted">
                            {a} agent · {modelName(models, a === 'Explorer' ? 'explorer' : 'hercules')}
                          </div>
                          <div className="mt-1">
                            {cs[a] ? (
                              <>
                                <b>{fmt(cs[a].value)}</b> · {cs[a].tool_calls} tool calls{cs[a].failure ? ` · ${cs[a].failure}` : ''}
                                <div className="mt-1 line-clamp-3 font-mono text-[11px] text-stone-500">{cs[a].sql}</div>
                              </>
                            ) : (
                              <span className="text-rose-700">{result.errors[`${a.toLowerCase()}_error`] ?? 'No candidate'}</span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                    <div className="mt-3 flex gap-2.5 text-[13px]">
                      <GitMerge size={16} className="mt-0.5 shrink-0 text-brand" />
                      <div>
                        <b>
                          Decision ({result.adjudicator.model ?? modelName(models, 'adjudicator')}
                          {result.adjudicator.status ? `, ${result.adjudicator.status}` : ''}):
                        </b>{' '}
                        {result.adjudicator.rationale ??
                          (disagreement ? 'Selected on deterministic evidence scores.' : 'Both executed results match, so the shared value is accepted.')}
                        {result.verification_actions.length > 0 && (
                          <> It ran {result.verification_actions.length} targeted verification quer{result.verification_actions.length === 1 ? 'y' : 'ies'}: {result.verification_actions.map((v) => v.purpose).join('; ')}.</>
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="mt-6 flex items-center gap-1 text-muted">
                    {[
                      { I: Copy, on: () => navigator.clipboard?.writeText(result.answer) },
                      { I: ThumbsUp, on: () => undefined },
                      { I: RefreshCw, on: onRerun },
                    ].map(({ I, on }, i) => (
                      <button key={i} onClick={on} className="rounded-lg p-2 hover:bg-stone-100 hover:text-ink">
                        <I size={15} />
                      </button>
                    ))}
                    <span className="ml-auto text-[11px]">
                      {modelName(models, 'explorer')} · {modelName(models, 'hercules')} · run {(result.request_id ?? '').slice(0, 8)} ·{' '}
                      {new Date().toTimeString().slice(0, 5)} IST
                    </span>
                  </div>

                  {/* related */}
                  {related.length > 0 && (
                    <div className="mt-8 border-t border-line pt-5">
                      <SectionLabel icon={<Layers size={15} />}>Related</SectionLabel>
                      {related.slice(0, 4).map((q) => (
                        <button
                          key={q}
                          onClick={() => onFollowUp(q)}
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
                      <span className="text-[11px] font-medium text-stone-400">{a.file_id.slice(0, 10)}</span>
                      <span className="text-[11px] text-stone-400">·</span>
                      <span className="text-[11px] text-stone-500">{a.origin ?? 'export'}</span>
                      <StatusPill status="verified" />
                    </div>
                    <div className="mt-1 text-[14px] font-medium">Download {a.filename}</div>
                    <div className="mt-0.5 truncate text-[12px] text-muted">
                      Generated from the verified result{a.size ? ` — ${(a.size / 1024).toFixed(1)} KB` : ''}
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
      </div>

      {/* follow-up composer */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-paper via-paper to-transparent pb-5 pt-10">
        <div className="pointer-events-auto mx-auto max-w-[760px] px-6">
          {done && result && !verified && (
            <div className="mb-2 flex items-center justify-between rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] fade-up">
              <span className="flex items-center gap-2 text-amber-800">
                <AlertTriangle size={14} /> This answer needs your review: {result.verification_status.replace(/_/g, ' ')}
              </span>
            </div>
          )}
          <Composer compact onSubmit={onFollowUp} placeholder="Ask a follow-up or refine the goal…" sourceCount={sourceCount} onAttach={onAttach} />
        </div>
      </div>
    </div>
  )
}
