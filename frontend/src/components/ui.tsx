import { AlertTriangle, CheckCircle2, CircleDashed, Clock, FlaskConical, Lightbulb, OctagonX, XCircle } from 'lucide-react'
import type { ReactNode } from 'react'
import { sources, type ActionStatus, type Source } from '../data'

const statusMap: Record<ActionStatus, { label: string; cls: string; icon: ReactNode }> = {
  verified: { label: 'Verified', cls: 'bg-emerald-50 text-emerald-700 ring-emerald-200', icon: <CheckCircle2 size={12} /> },
  simulated: { label: 'Simulated', cls: 'bg-sky-50 text-sky-700 ring-sky-200', icon: <FlaskConical size={12} /> },
  pending: { label: 'Needs approval', cls: 'bg-amber-50 text-amber-700 ring-amber-200', icon: <Clock size={12} /> },
  failed: { label: 'Failed', cls: 'bg-rose-50 text-rose-700 ring-rose-200', icon: <XCircle size={12} /> },
  blocked: { label: 'Held by policy', cls: 'bg-orange-50 text-orange-700 ring-orange-200', icon: <OctagonX size={12} /> },
  recommended: { label: 'Recommended', cls: 'bg-stone-100 text-stone-700 ring-stone-200', icon: <Lightbulb size={12} /> },
}

export function StatusPill({ status }: { status: ActionStatus }) {
  const s = statusMap[status]
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${s.cls}`}>
      {s.icon}
      {s.label}
    </span>
  )
}

export function Cite({ ids, list = sources }: { ids: number[]; list?: Source[] }) {
  return (
    <span className="ml-0.5 inline-flex gap-0.5 align-middle">
      {ids.map((id) => {
        const src = list.find((s) => s.id === id)
        if (!src) return null
        return (
          <span
            key={id}
            title={`${src.name} · ${src.freshness}`}
            className={`inline-flex h-4 min-w-4 cursor-default items-center justify-center rounded-full px-1 text-[10px] font-medium ${
              src.stale ? 'bg-rose-100 text-rose-700' : 'bg-stone-200/70 text-stone-600 hover:bg-brand hover:text-white'
            }`}
          >
            {id}
          </span>
        )
      })}
    </span>
  )
}

export function SourceDot({ color, letter }: { color: string; letter: string }) {
  return (
    <span className={`inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-[4px] text-[9px] font-bold text-white ${color}`}>
      {letter}
    </span>
  )
}

export function CheckIcon({ result }: { result: 'pass' | 'fail' | 'warn' }) {
  if (result === 'pass') return <CheckCircle2 size={15} className="shrink-0 text-emerald-600" />
  if (result === 'fail') return <XCircle size={15} className="shrink-0 text-rose-600" />
  return <AlertTriangle size={15} className="shrink-0 text-amber-600" />
}

export function Spinner() {
  return <CircleDashed size={15} className="animate-spin text-brand" />
}

export function SectionLabel({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className="mb-3 flex items-center gap-2 text-[13px] font-medium text-ink">
      <span className="text-muted">{icon}</span>
      {children}
    </div>
  )
}
