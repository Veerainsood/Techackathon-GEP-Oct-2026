import { ArrowRight, Bell, Bot, ShieldAlert, X } from 'lucide-react'
import { awaySummary, formatL, overnightRuns, type AutoRun } from '../data'

export function NotificationButton({ unread, onClick, active }: { unread: number; onClick: () => void; active: boolean }) {
  return (
    <button
      onClick={onClick}
      title="Notifications"
      className={`relative flex h-8 w-8 items-center justify-center rounded-lg transition ${
        active ? 'bg-brand-soft text-brand' : 'text-stone-600 hover:bg-stone-100'
      }`}
    >
      <Bell size={17} />
      {unread > 0 && (
        <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-500 px-1 text-[9px] font-semibold text-white ring-2 ring-paper">
          {unread}
        </span>
      )}
    </button>
  )
}

interface Props {
  open: boolean
  onClose: () => void
  pendingCount: number
  onReview: () => void
  stats?: { l: string; v: string; s: string; c: string }[]
  runs?: AutoRun[]
  windowLabel?: string
  dataLabel?: string
}

export function NotificationsPanel({ open, onClose, pendingCount, onReview, stats, runs = overnightRuns, windowLabel = awaySummary.window, dataLabel = 'sandbox data' }: Props) {
  return (
    <aside
      className={`shrink-0 overflow-hidden border-line bg-white transition-[width] duration-300 ease-out ${
        open ? 'w-[380px] border-l' : 'w-0'
      }`}
    >
      <div className="flex h-full w-[380px] flex-col">
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <div className="flex items-center gap-2">
            <Bot size={16} className="text-brand" />
            <span className="text-[14px] font-semibold">While you were away</span>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-muted hover:bg-stone-100">
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto scrollbar-thin">
          <div className="px-5 pt-4 text-[12px] text-muted">
            {windowLabel} · <span className="text-sky-700">{dataLabel}</span>
          </div>

          <div className="mx-5 mt-3 grid grid-cols-2 gap-2">
            {(
              stats ?? [
                { l: 'Handled on its own', v: String(awaySummary.handled), s: 'no human needed', c: 'text-ink' },
                { l: 'Needed you', v: String(pendingCount), s: 'above its authority', c: 'text-amber-700' },
                { l: 'Verified cash impact', v: formatL(awaySummary.realizedL), s: `+${formatL(awaySummary.estimatedL)} estimated`, c: 'text-emerald-700' },
                { l: 'Team time saved', v: `${awaySummary.hoursSaved} hrs`, s: 'vs. manual follow-ups', c: 'text-ink' },
              ]
            ).map((k) => (
              <div key={k.l} className="rounded-xl bg-stone-50 px-3 py-2.5">
                <div className="text-[11px] text-muted">{k.l}</div>
                <div className={`text-[20px] font-semibold tracking-tight ${k.c}`}>{k.v}</div>
                <div className="text-[10px] text-stone-400">{k.s}</div>
              </div>
            ))}
          </div>

          {pendingCount > 0 && (
            <button
              onClick={onReview}
              className="mx-5 mt-3 flex w-[calc(100%-40px)] items-center justify-between rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-left text-[12px] text-amber-900 hover:bg-amber-100/60"
            >
              <span className="flex items-center gap-2">
                <ShieldAlert size={14} />
                <b>{pendingCount} decisions</b> need you
              </span>
              <span className="flex items-center gap-1 font-medium">
                Review <ArrowRight size={13} />
              </span>
            </button>
          )}

          <div className="px-5 pb-2 pt-5 text-[11px] font-medium uppercase tracking-wider text-stone-400">Timeline</div>
          <ol className="relative mx-5 mb-6 border-l border-line">
            {runs.map((r) => (
              <li key={r.time + r.title} className="relative pb-4 pl-4 last:pb-0">
                <span className="absolute -left-[5px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-white bg-brand" />
                <div className="text-[11px] tabular-nums text-stone-400">{r.time}</div>
                <div className="text-[13px] font-medium leading-snug">{r.title}</div>
                <div className="text-[12px] text-muted">{r.detail}</div>
                {r.impact && (
                  <span
                    className={`mt-1.5 inline-block rounded-md px-2 py-0.5 text-[11px] font-medium ${
                      r.realized ? 'bg-emerald-50 text-emerald-700' : 'bg-stone-100 text-stone-600'
                    }`}
                  >
                    {r.impact} · {r.realized ? 'verified' : 'expected'}
                  </span>
                )}
              </li>
            ))}
          </ol>
        </div>
      </div>
    </aside>
  )
}
