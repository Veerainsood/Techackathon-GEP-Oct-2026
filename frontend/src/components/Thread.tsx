import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  GitMerge,
  Layers,
  ListChecks,
  RefreshCw,
  Share2,
  ThumbsUp,
  Zap,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { forecastStats as fs, formatL, overdue, sources, steps, type Action } from '../data'
import { Composer } from './Composer'
import { ForecastChart } from './ForecastChart'
import { Cite, SectionLabel, SourceDot, Spinner, StatusPill } from './ui'

interface Props {
  query: string
  actions: Action[]
  onOpenAction: (id: string) => void
  onApprove: (id: string) => void
  onFollowUp: (q: string) => void
  paused: boolean
  headerRight?: React.ReactNode
}

type Tab = 'answer' | 'actions' | 'sources'

export function Thread({ query, actions, onOpenAction, onApprove, onFollowUp, paused, headerRight }: Props) {
  const [stepIdx, setStepIdx] = useState(0)
  const [stepsOpen, setStepsOpen] = useState(true)
  const [tab, setTab] = useState<Tab>('answer')
  const scrollRef = useRef<HTMLDivElement>(null)
  const done = stepIdx >= steps.length

  useEffect(() => {
    setStepIdx(0)
    setStepsOpen(true)
    setTab('answer')
  }, [query])

  useEffect(() => {
    if (done) {
      const t = setTimeout(() => setStepsOpen(false), 600)
      return () => clearTimeout(t)
    }
    const t = setTimeout(() => setStepIdx((i) => i + 1), stepIdx === 0 ? 500 : 650)
    return () => clearTimeout(t)
  }, [stepIdx, done])

  const pending = actions.filter((a) => a.status === 'pending')
  const groups: { title: string; filter: (a: Action) => boolean }[] = [
    { title: 'Needs your approval', filter: (a) => a.status === 'pending' },
    { title: 'Executed within authority', filter: (a) => a.status === 'verified' || a.status === 'simulated' },
    { title: 'Held or failed — escalated', filter: (a) => a.status === 'blocked' || a.status === 'failed' },
    { title: 'Recommendations', filter: (a) => a.status === 'recommended' },
  ]

  return (
    <div className="relative flex h-full flex-col">
      {/* top bar */}
      <div className="flex items-center justify-between gap-4 border-b border-line bg-paper/80 px-6 py-3 backdrop-blur">
        <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden text-[13px] text-muted">
          <span className="min-w-0 truncate font-medium text-ink">{query}</span>
          <span className="shrink-0 whitespace-nowrap rounded-full bg-brand-soft px-2 py-0.5 text-[11px] font-medium text-brand">L4 · Orchestrate</span>
        </div>
        <div className="flex shrink-0 items-center gap-1 whitespace-nowrap">
          <button className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-stone-600 hover:bg-stone-100">
            <RefreshCw size={13} /> <span className="hidden xl:inline">Re-run</span>
          </button>
          <button className="flex items-center gap-1.5 rounded-lg bg-ink px-3 py-1.5 text-xs font-medium text-white hover:bg-stone-800">
            <Share2 size={13} /> Share
          </button>
          {headerRight && <span className="ml-1">{headerRight}</span>}
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto scrollbar-thin">
        <div className="mx-auto max-w-[760px] px-6 pb-40 pt-8">
          <h1 className="font-serif text-[32px] leading-tight tracking-tight">{query}</h1>

          {/* tabs */}
          <div className="mt-5 flex items-center gap-5 border-b border-line text-[13px]">
            {(
              [
                ['answer', 'Answer', Zap],
                ['actions', `Actions · ${actions.length}`, ListChecks],
                ['sources', `Sources · ${sources.length}`, Layers],
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
                <button
                  onClick={() => setStepsOpen((o) => !o)}
                  className="flex w-full items-center justify-between px-4 py-3 text-[13px]"
                >
                  <span className="flex items-center gap-2">
                    {done ? <Check size={15} className="text-brand" /> : <Spinner />}
                    {done ? (
                      <span className="font-medium">Completed 7 steps · 4 agents · 8 actions planned</span>
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
                                {sources.map((src) => (
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

              {stepIdx >= 2 && (
                <div className="mt-6 fade-up">
                  <SectionLabel icon={<Layers size={15} />}>Sources</SectionLabel>
                  <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
                    {sources.slice(0, 3).map((s) => (
                      <SourceCard key={s.id} s={s} />
                    ))}
                    <button
                      onClick={() => done && setTab('sources')}
                      className="flex flex-col justify-between rounded-xl border border-line bg-white/60 p-2.5 text-left hover:bg-white"
                    >
                      <div className="flex -space-x-1">
                        {sources.slice(3).map((s) => (
                          <SourceDot key={s.id} color={s.color} letter={s.name[0]} />
                        ))}
                      </div>
                      <div className="text-[12px] text-muted">
                        +3 more · <span className="text-rose-600">1 stale</span>
                      </div>
                    </button>
                  </div>
                </div>
              )}

              {done && (
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
                      You'll stay above the <b>₹30L hard minimum</b>, but without action cash dips{' '}
                      <b>below your ₹50L target for {fs.breachDays} days (D20–D25)</b>, bottoming at{' '}
                      <b className="text-rose-700">{formatL(fs.trough)} on D{fs.troughDay}</b>
                      <Cite ids={[1, 2, 6]} />. The trough is driven by October payroll on D15 and the Cloudnine hosting bill on
                      D20, while Orion's ₹20L doesn't land until D26.
                    </p>
                    <p>
                      I've already <b>escalated Orion Logistics</b> (verified in CRM) and sent a sandbox reminder to Nimbus Retail
                      <Cite ids={[3]} />. If you approve <b>two payment-timing changes</b>, the revised plan keeps cash at or above{' '}
                      <b className="text-brand">{formatL(fs.revisedTrough)}</b> for the full 30 days and captures a ₹24K early-payment
                      discount.
                    </p>
                  </div>

                  {/* KPIs */}
                  <div className="mt-5 grid grid-cols-2 divide-x divide-line md:grid-cols-4 overflow-hidden rounded-2xl border border-line bg-white">
                    {[
                      { l: 'Available cash', v: formatL(fs.current), t: 'Confirmed', c: 'text-emerald-700' },
                      { l: 'Expected collections', v: `+${formatL(fs.collections)}`, t: 'Expected · not confirmed', c: 'text-amber-700' },
                      { l: 'Scheduled outflows', v: `−${formatL(fs.expenses)}`, t: 'Payroll, vendors, opex', c: 'text-muted' },
                      { l: 'Projected D30', v: formatL(fs.ending), t: `+${formatL(fs.ending - 50)} over target`, c: 'text-muted' },
                    ].map((k) => (
                      <div key={k.l} className="p-3.5">
                        <div className="text-[11px] text-muted">{k.l}</div>
                        <div className="mt-0.5 text-xl font-semibold tracking-tight">{k.v}</div>
                        <div className={`text-[11px] ${k.c}`}>{k.t}</div>
                      </div>
                    ))}
                  </div>

                  <div className="mt-4">
                    <ForecastChart revisedReady />
                  </div>

                  {/* warnings */}
                  <div className="mt-4 space-y-2">
                    <Callout tone="rose">
                      <b>Payroll feed is 26 hours old.</b> I used the last confirmed schedule (₹30L on D15). Payroll-dependent
                      actions are paused until it refreshes. <Cite ids={[6]} />
                    </Callout>
                    <Callout tone="amber">
                      <b>Vertex Health's ₹35L is left out of the forecast.</b> It's 60 days overdue, two promised payment dates were
                      missed, and a customer email mentions a "service credit". I've flagged it for review.
                      <Cite ids={[2, 3]} />
                    </Callout>
                  </div>

                  {/* collections */}
                  <h3 className="mb-2 mt-8 text-[17px] font-semibold tracking-tight">Collections, ranked</h3>
                  <p className="mb-3 text-[14px] text-stone-600">
                    I ranked the ₹60L overdue by value × days overdue × customer risk <Cite ids={[2, 3]} />.
                  </p>
                  <div className="overflow-hidden rounded-xl border border-line bg-white text-[13px]">
                    <table className="w-full">
                      <thead className="bg-stone-50 text-left text-[11px] uppercase tracking-wider text-muted">
                        <tr>
                          <th className="px-3 py-2 font-medium">Customer</th>
                          <th className="px-3 py-2 text-right font-medium">Amount</th>
                          <th className="px-3 py-2 text-right font-medium">Overdue</th>
                          <th className="px-3 py-2 font-medium">Risk</th>
                          <th className="px-3 py-2 font-medium">Agent action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {overdue.map((o) => (
                          <tr key={o.customer} className="border-t border-line">
                            <td className="px-3 py-2.5 font-medium">{o.customer}</td>
                            <td className="px-3 py-2.5 text-right tabular-nums">{formatL(o.amountL)}</td>
                            <td className="px-3 py-2.5 text-right tabular-nums">{o.days}d</td>
                            <td className="px-3 py-2.5">
                              <span
                                className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
                                  o.risk === 'High'
                                    ? 'bg-rose-50 text-rose-700'
                                    : o.risk === 'Medium'
                                      ? 'bg-amber-50 text-amber-700'
                                      : 'bg-emerald-50 text-emerald-700'
                                }`}
                              >
                                {o.risk}
                              </span>
                            </td>
                            <td className="px-3 py-2.5 text-stone-600">{o.action}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {/* conflict */}
                  <h3 className="mb-2 mt-8 text-[17px] font-semibold tracking-tight">Resolving a conflict between agents</h3>
                  <div className="rounded-xl border border-line bg-white p-4">
                    <div className="grid grid-cols-2 gap-3 text-[13px]">
                      <div className="rounded-lg bg-stone-50 p-3">
                        <div className="text-[11px] font-medium uppercase tracking-wider text-muted">Payments agent</div>
                        <div className="mt-1">Pay Acme ₹12L early on D8 to get the 2% discount (₹24K).</div>
                      </div>
                      <div className="rounded-lg bg-stone-50 p-3">
                        <div className="text-[11px] font-medium uppercase tracking-wider text-muted">Cash-flow agent</div>
                        <div className="mt-1">Hold on to cash. Reserve headroom is thin this month.</div>
                      </div>
                    </div>
                    <div className="mt-3 flex gap-2.5 text-[13px]">
                      <GitMerge size={16} className="mt-0.5 shrink-0 text-brand" />
                      <div>
                        <b>Decision (rule P-2, the reserve comes first):</b> recommend paying early. The low point comes from
                        outflows after D20, so paying on D8 never takes cash below ₹50L. Because it's over ₹2L, it still needs
                        your approval.
                      </div>
                    </div>
                  </div>

                  {/* plan */}
                  <h3 className="mb-3 mt-8 text-[17px] font-semibold tracking-tight">Action plan</h3>
                  {paused && (
                    <Callout tone="rose">
                      <b>Autonomous execution is paused.</b> No new actions will run. Approvals stay queued.
                    </Callout>
                  )}
                  <div className="space-y-5">
                    {groups.map((g) => {
                      const list = actions.filter(g.filter)
                      if (!list.length) return null
                      return (
                        <div key={g.title}>
                          <div className="mb-2 text-[12px] font-medium text-muted">{g.title}</div>
                          <div className="space-y-2">
                            {list.map((a) => (
                              <ActionRow key={a.id} a={a} onOpen={onOpenAction} onApprove={onApprove} />
                            ))}
                          </div>
                        </div>
                      )
                    })}
                  </div>

                  <div className="mt-6 flex items-center gap-1 text-muted">
                    {[Copy, ThumbsUp, RefreshCw].map((I, i) => (
                      <button key={i} className="rounded-lg p-2 hover:bg-stone-100 hover:text-ink">
                        <I size={15} />
                      </button>
                    ))}
                    <span className="ml-auto text-[11px]">Policy engine v3.2 · run AR-2291 · 13:31 IST</span>
                  </div>

                  {/* related */}
                  <div className="mt-8 border-t border-line pt-5">
                    <SectionLabel icon={<Layers size={15} />}>Related</SectionLabel>
                    {[
                      'What happens if Vertex Health never pays?',
                      'Simulate a 15-day delay on Orion Logistics',
                      'Show me the cheapest way to cover the ₹50K Marketing overrun',
                      'Which subscriptions can we cancel this week?',
                    ].map((q) => (
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
                </div>
              )}
            </div>
          )}

          {tab === 'actions' && (
            <div className="space-y-2 pt-5 fade-up">
              {actions.map((a) => (
                <ActionRow key={a.id} a={a} onOpen={onOpenAction} onApprove={onApprove} />
              ))}
            </div>
          )}

          {tab === 'sources' && (
            <div className="grid grid-cols-2 gap-3 pt-5 fade-up">
              {sources.map((s) => (
                <SourceCard key={s.id} s={s} large />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* follow-up composer */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-paper via-paper to-transparent pb-5 pt-10">
        <div className="pointer-events-auto mx-auto max-w-[760px] px-6">
          {done && pending.length > 0 && (
            <div className="mb-2 flex items-center justify-between rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] fade-up">
              <span className="flex items-center gap-2 text-amber-800">
                <AlertTriangle size={14} /> {pending.length} action{pending.length > 1 ? 's' : ''} waiting for your approval
              </span>
              <button
                onClick={() => onOpenAction(pending[0].id)}
                className="rounded-lg bg-amber-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-amber-700"
              >
                Review
              </button>
            </div>
          )}
          <Composer compact onSubmit={onFollowUp} placeholder="Ask a follow-up or refine the goal…" />
        </div>
      </div>
    </div>
  )
}

export function SourceCard({ s, large }: { s: (typeof sources)[number]; large?: boolean }) {
  return (
    <div className={`rounded-xl border bg-white p-2.5 ${s.stale ? 'border-rose-200' : 'border-line'} ${large ? 'p-3.5' : ''}`}>
      <div className="line-clamp-2 text-[12px] font-medium leading-snug">{s.detail}</div>
      <div className="mt-2 flex items-center gap-1.5 text-[11px] text-muted">
        <SourceDot color={s.color} letter={s.name[0]} />
        <span className="truncate">{s.name}</span>
        <span className="text-stone-300">·</span>
        <span className={`shrink-0 whitespace-nowrap ${s.stale ? 'font-medium text-rose-600' : ''}`}>{s.freshness}</span>
      </div>
      {large && <div className="mt-1 text-[11px] text-stone-400">{s.system}</div>}
    </div>
  )
}

export function Callout({ tone, children }: { tone: 'rose' | 'amber'; children: React.ReactNode }) {
  const cls = tone === 'rose' ? 'border-rose-200 bg-rose-50/60 text-rose-900' : 'border-amber-200 bg-amber-50/60 text-amber-900'
  return (
    <div className={`flex gap-2.5 rounded-xl border px-3.5 py-2.5 text-[13px] leading-relaxed ${cls}`}>
      <AlertTriangle size={15} className="mt-0.5 shrink-0" />
      <div>{children}</div>
    </div>
  )
}

export function ActionRow({ a, onOpen, onApprove }: { a: Action; onOpen: (id: string) => void; onApprove: (id: string) => void }) {
  return (
    <div
      onClick={() => onOpen(a.id)}
      className="group flex cursor-pointer items-center gap-3 rounded-xl border border-line bg-white px-4 py-3 transition hover:border-stone-300 hover:shadow-sm"
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-medium text-stone-400">{a.id}</span>
          <span className="text-[11px] text-stone-400">·</span>
          <span className="text-[11px] text-stone-500">{a.agent}</span>
          <StatusPill status={a.status} />
        </div>
        <div className="mt-1 text-[14px] font-medium">{a.title}</div>
        <div className="mt-0.5 truncate text-[12px] text-muted">
          {a.entity} — {a.impact}
        </div>
      </div>
      {a.status === 'pending' ? (
        <div className="flex shrink-0 gap-1.5" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={() => onOpen(a.id)}
            className="rounded-lg border border-line px-2.5 py-1.5 text-xs font-medium text-stone-600 hover:bg-stone-50"
          >
            Details
          </button>
          <button
            onClick={() => onApprove(a.id)}
            className="rounded-lg bg-brand px-2.5 py-1.5 text-xs font-medium text-white hover:bg-brand/90"
          >
            Approve
          </button>
        </div>
      ) : (
        <ChevronRight size={16} className="shrink-0 text-stone-300 group-hover:text-stone-500" />
      )}
    </div>
  )
}
