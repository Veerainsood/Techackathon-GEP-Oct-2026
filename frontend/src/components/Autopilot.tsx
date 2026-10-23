import { CheckCircle2, Clock3, OctagonX, Radio, Target, UserRound } from 'lucide-react'
import { awaySummary, standingGoals, suggestions, type LiveEvent } from '../data'

export interface FeedItem extends LiveEvent {
  at: string
}

interface Props {
  onSubmit: (q: string) => void
  pendingCount: number
  paused: boolean
  feed: FeedItem[]
  onOpenAction: (id: string) => void
}

const handledStyle = {
  auto: { label: 'Handled', cls: 'bg-emerald-50 text-emerald-700 ring-emerald-200', icon: CheckCircle2 },
  escalated: { label: 'Escalated to you', cls: 'bg-amber-50 text-amber-700 ring-amber-200', icon: UserRound },
  blocked: { label: 'Blocked', cls: 'bg-orange-50 text-orange-700 ring-orange-200', icon: OctagonX },
}

export function AutopilotPage({ onSubmit, pendingCount, paused, feed, onOpenAction }: Props) {
  const handledLive = feed.filter((f) => f.handled !== 'escalated').length
  return (
    <div className="h-full overflow-y-auto px-6 scrollbar-thin">
      <div className="mx-auto w-full max-w-[960px] py-10">
        <h1 className="font-serif text-[32px] tracking-tight">Autopilot</h1>
        <p className="mt-1 text-[14px] text-muted">
          Goals the agent pursues continuously, and the events it is acting on right now — without being asked.
        </p>
        <div className="mb-8 mt-5 grid grid-cols-3 gap-3">
          {[
            { l: 'Status', v: paused ? 'Paused' : 'Running', c: paused ? 'text-rose-700' : 'text-emerald-700' },
            { l: 'Handled on its own today', v: String(awaySummary.handled + handledLive), c: 'text-ink' },
            { l: 'Escalated to people', v: String(pendingCount), c: 'text-amber-700' },
          ].map((k) => (
            <div key={k.l} className="rounded-xl border border-line bg-white px-4 py-3">
              <div className="text-[11px] text-muted">{k.l}</div>
              <div className={`text-[20px] font-semibold tracking-tight ${k.c}`}>{k.v}</div>
            </div>
          ))}
        </div>
        <div className="grid grid-cols-5 gap-6">
          {/* standing goals */}
          <section className="col-span-2">
            <div className="mb-3 flex items-center gap-2 text-[13px] font-medium">
              <Target size={15} className="text-muted" /> Standing goals
              <span className="text-[11px] font-normal text-muted">always running</span>
            </div>
            <div className="space-y-2.5">
              {standingGoals.map((g) => (
                <button
                  key={g.id}
                  onClick={() => g.id === 'G-1' && onSubmit(suggestions[0])}
                  className="w-full rounded-xl border border-line bg-white p-3.5 text-left transition hover:border-stone-300"
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-[13px] font-medium leading-snug">{g.title}</span>
                    <span
                      className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
                        g.status === 'on-track' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'
                      }`}
                    >
                      {g.status === 'on-track' ? 'On track' : 'At risk'}
                    </span>
                  </div>
                  <div className="mt-1 text-[12px] text-muted">{g.metric}</div>
                  <div className="mt-2.5 h-1 overflow-hidden rounded-full bg-stone-100">
                    <div
                      className={`h-full rounded-full ${g.status === 'on-track' ? 'bg-brand' : 'bg-amber-500'}`}
                      style={{ width: `${g.progress}%` }}
                    />
                  </div>
                  <div className="mt-2 flex items-center justify-between text-[11px] text-stone-400">
                    <span className="flex items-center gap-1">
                      <Clock3 size={11} /> checked {g.lastRun}
                    </span>
                    <span>{g.actionsToday} actions today</span>
                  </div>
                </button>
              ))}
            </div>
          </section>

          {/* live feed */}
          <section className="col-span-3">
            <div className="mb-3 flex items-center justify-between text-[13px] font-medium">
              <span className="flex items-center gap-2">
                <Radio size={15} className={paused ? 'text-muted' : 'text-rose-500'} /> Live
                <span className="text-[11px] font-normal text-muted">events the agent is handling right now</span>
              </span>
            </div>
            <div className="overflow-hidden rounded-xl border border-line bg-white">
              <div className="flex items-center gap-2 border-b border-line bg-stone-50/60 px-4 py-2.5 text-[12px] text-muted">
                {paused ? (
                  <>Paused — new events are queued, not acted on</>
                ) : (
                  <>
                    <span className="relative flex h-2 w-2">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand opacity-60" />
                      <span className="relative inline-flex h-2 w-2 rounded-full bg-brand" />
                    </span>
                    <span className="shimmer-text">Watching 6 sources for changes…</span>
                  </>
                )}
              </div>
              {feed.length === 0 && <div className="px-4 py-8 text-center text-[12px] text-stone-400">No new events yet</div>}
              <ul className="divide-y divide-line">
                {feed.map((e) => {
                  const st = handledStyle[e.handled]
                  const Icon = st.icon
                  return (
                    <li
                      key={e.title}
                      onClick={() => e.action && onOpenAction(e.action.id)}
                      className={`px-4 py-3 fade-up ${e.action ? 'cursor-pointer hover:bg-stone-50' : ''}`}
                    >
                      <div className="flex items-center justify-between gap-2 text-[11px] text-stone-400">
                        <span>
                          {e.at} · {e.source}
                        </span>
                        <span className={`inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 font-medium ring-1 ${st.cls}`}>
                          <Icon size={11} /> {st.label}
                        </span>
                      </div>
                      <div className="mt-1 text-[13px] font-medium">{e.title}</div>
                      <div className="mt-0.5 text-[12px] text-stone-600">
                        <span className="text-brand">→</span> {e.outcome}
                      </div>
                      <div className="mt-1 text-[11px] text-stone-400">Rule: {e.rule}</div>
                    </li>
                  )
                })}
              </ul>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
