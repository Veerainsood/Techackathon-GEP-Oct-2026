// What the data-prep agent did to the attached files, shown in the chat; fixes that need a person are decided right here.
import { AlertTriangle, Check, ChevronDown, ChevronRight, FileSpreadsheet } from 'lucide-react'
import { useState } from 'react'
import { Spinner } from '../components/ui'
import type { PrepFix, PrepJob } from './api'

export interface PrepState {
  busy: boolean
  names: string[]
  jobs: PrepJob[]
}

const cols = (f: PrepFix) => (f.columns.length ? f.columns.join(', ') : 'all columns')

export function describeFix(f: PrepFix): string {
  const p = f.params ?? {}
  switch (f.op) {
    case 'trim_whitespace':
      return `Trimmed stray spaces in ${cols(f)}`
    case 'standardize_case':
      return `Made the case consistent in ${cols(f)}`
    case 'cast_numeric':
      return `Converted ${cols(f)} to numbers`
    case 'parse_dates':
      return `Read the dates in ${cols(f)} as YYYY-MM-DD`
    case 'normalize_values':
      return `Unified spelling variants in ${cols(f)}`
    case 'fill_missing':
      return `Filled missing ${cols(f)} (${p.method === 'constant' ? `"${String(p.value)}"` : String(p.method ?? 'rule')})`
    case 'flag_duplicates':
      return 'Flagged repeated rows (nothing removed)'
    case 'drop_exact_duplicates':
      return 'Remove rows that are exact duplicates'
    case 'drop_rows_missing':
      return `Remove rows missing ${cols(f)}`
    default:
      return `${f.op} ${cols(f)}`
  }
}

function JobRow({ job, onDecide }: { job: PrepJob; onDecide: (job: PrepJob, fix: PrepFix, approved: boolean) => Promise<void> }) {
  const fixes = job.fixes ?? []
  const pending = fixes.filter((f) => f.status === 'pending')
  const applied = fixes.filter((f) => f.status === 'applied' || f.status === 'approved')
  const notes = job.findings.filter((x) => x.severity !== 'info')
  const [open, setOpen] = useState(pending.length > 0)
  const [busy, setBusy] = useState<string | null>(null)
  const decide = async (fix: PrepFix, approved: boolean) => {
    setBusy(fix.id)
    try {
      await onDecide(job, fix, approved)
    } finally {
      setBusy(null)
    }
  }
  return (
    <li className="border-t border-line first:border-t-0">
      <button onClick={() => setOpen((o) => !o)} className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-[13px]">
        <span className="flex min-w-0 items-center gap-2">
          <FileSpreadsheet size={14} className="shrink-0 text-muted" />
          <span className="truncate font-medium">{job.filename}</span>
          <span className="shrink-0 text-[12px] text-muted">
            {applied.length} fix{applied.length === 1 ? '' : 'es'} applied
            {notes.length ? ` · ${notes.length} note${notes.length === 1 ? '' : 's'}` : ''}
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {pending.length ? (
            <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-amber-200">
              {pending.length} need{pending.length === 1 ? 's' : ''} you
            </span>
          ) : (
            <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 ring-1 ring-emerald-200">Ready</span>
          )}
          {open ? <ChevronDown size={14} className="text-muted" /> : <ChevronRight size={14} className="text-muted" />}
        </span>
      </button>
      {open && (
        <div className="space-y-2.5 px-4 pb-3 text-[12.5px]">
          {job.summary && <p className="text-muted">{job.summary}</p>}
          {pending.map((f) => (
            <div key={f.id} className="rounded-lg border border-amber-200 bg-amber-50/60 p-3">
              <div className="font-medium text-ink">{describeFix(f)}</div>
              <div className="mt-0.5 text-muted">{f.rationale}</div>
              <div className="mt-2 flex gap-2">
                <button
                  disabled={!!busy}
                  onClick={() => decide(f, true)}
                  className="rounded-lg bg-ink px-3 py-1.5 text-[12px] font-medium text-white hover:bg-stone-800 disabled:opacity-50"
                >
                  {busy === f.id ? 'Applying…' : 'Approve'}
                </button>
                <button
                  disabled={!!busy}
                  onClick={() => decide(f, false)}
                  className="rounded-lg border border-line bg-white px-3 py-1.5 text-[12px] text-stone-700 hover:bg-stone-50 disabled:opacity-50"
                >
                  Reject
                </button>
              </div>
            </div>
          ))}
          {applied.length > 0 && (
            <ul className="space-y-1">
              {applied.map((f) => (
                <li key={f.id} className="flex gap-2">
                  <Check size={13} className="mt-0.5 shrink-0 text-brand" />
                  <span>{describeFix(f)}</span>
                </li>
              ))}
            </ul>
          )}
          {notes.slice(0, 4).map((x, i) => (
            <div key={i} className="flex gap-2 text-stone-600">
              <AlertTriangle size={13} className={`mt-0.5 shrink-0 ${x.severity === 'critical' ? 'text-rose-600' : 'text-amber-600'}`} />
              <span>
                <span className="font-medium">{x.title}.</span> {x.detail}
              </span>
            </div>
          ))}
        </div>
      )}
    </li>
  )
}

export function PrepCard({ prep, onDecide }: { prep: PrepState; onDecide: (job: PrepJob, fix: PrepFix, approved: boolean) => Promise<void> }) {
  const n = prep.busy ? prep.names.length : prep.jobs.length
  const fixes = prep.jobs.flatMap((j) => j.fixes ?? [])
  const applied = fixes.filter((f) => f.status === 'applied' || f.status === 'approved').length
  const pending = fixes.filter((f) => f.status === 'pending').length
  return (
    <section className="mb-10 border-b border-line pb-8">
      <h2 className="mb-4 font-serif text-[24px] leading-tight tracking-tight">
        {prep.busy ? `Checking ${n} file${n === 1 ? '' : 's'}` : `${n} file${n === 1 ? '' : 's'} checked`}
      </h2>
      <div className="rounded-xl border border-line bg-white">
        <div className="flex items-center gap-2 px-4 py-3 text-[13px]">
          {prep.busy ? <Spinner /> : <Check size={15} className="text-brand" />}
          {prep.busy ? (
            <span className="shimmer-text font-medium">Profiling and fixing in the sandbox…</span>
          ) : (
            <span className="font-medium">
              {applied} fix{applied === 1 ? '' : 'es'} applied
              {pending ? ` · ${pending} waiting for your decision` : ' · ready to analyse'}
            </span>
          )}
        </div>
        {prep.busy ? (
          <ul className="border-t border-line px-4 py-2.5 text-[12.5px] text-muted">
            {prep.names.map((name) => (
              <li key={name} className="flex items-center gap-2 py-0.5">
                <FileSpreadsheet size={13} /> {name}
              </li>
            ))}
          </ul>
        ) : (
          <ul className="border-t border-line">
            {prep.jobs.map((j) => (
              <JobRow key={j.job_id} job={j} onDecide={onDecide} />
            ))}
          </ul>
        )}
      </div>
      {!prep.busy && <p className="mt-3 text-[12.5px] text-muted">Ask about these files below, for example “Find the leaks in these files”.</p>}
    </section>
  )
}
