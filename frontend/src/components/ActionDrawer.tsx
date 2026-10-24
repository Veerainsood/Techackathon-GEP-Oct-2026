import { MessageSquare, RotateCcw, ShieldCheck, X } from 'lucide-react'
import { sources, type Action } from '../data'
import { CheckIcon, SourceDot, StatusPill } from './ui'

interface Props {
  action: Action | null
  onClose: () => void
  onApprove: (id: string) => void
  onReject: (id: string) => void
}

export function ActionDrawer({ action, onClose, onApprove, onReject }: Props) {
  return (
    <>
      <div
        onClick={onClose}
        className={`fixed inset-0 z-30 bg-stone-900/10 transition-opacity ${action ? 'opacity-100' : 'pointer-events-none opacity-0'}`}
      />
      <aside
        className={`fixed right-0 top-0 z-40 flex h-full w-[440px] flex-col border-l border-line bg-white shadow-2xl transition-transform duration-300 ${
          action ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        {action && (
          <>
            <div className="flex items-start justify-between border-b border-line px-5 py-4">
              <div>
                <div className="flex items-center gap-2 text-[11px] text-muted">
                  {action.id} · {action.agent} agent · {action.autonomy}
                </div>
                <h2 className="mt-1 text-[16px] font-semibold leading-snug">{action.title}</h2>
                <div className="mt-1.5">
                  <StatusPill status={action.status} />
                </div>
              </div>
              <button onClick={onClose} className="rounded-lg p-1.5 text-muted hover:bg-stone-100">
                <X size={16} />
              </button>
            </div>

            <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4 text-[13px] scrollbar-thin">
              <Field label="Target">{action.entity}</Field>
              <Field label="Why">{action.rationale}</Field>
              <div className="grid grid-cols-2 gap-3">
                <Box label="Expected impact">{action.impact}</Box>
                <Box label="Approval">{action.approval}</Box>
                <Box label="Reversible">
                  <span className="flex items-center gap-1.5">
                    <RotateCcw size={12} /> {action.reversible ? 'Yes' : 'No — irreversible'}
                  </span>
                </Box>
                <Box label="Mode">{action.status === 'simulated' ? 'Sandbox simulation' : 'Live (sandbox tenant)'}</Box>
              </div>

              <div>
                <div className="mb-2 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-muted">
                  <ShieldCheck size={12} /> Policy checks · deterministic
                </div>
                <div className="divide-y divide-line rounded-xl border border-line">
                  {action.checks.map((c) => (
                    <div key={c.label} className="flex items-start gap-2.5 px-3 py-2.5">
                      <CheckIcon result={c.result} />
                      <div>
                        <div className="font-medium">{c.label}</div>
                        <div className="text-[12px] text-muted">{c.detail}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <div className="mb-2 text-[11px] font-medium uppercase tracking-wider text-muted">Evidence</div>
                <div className="flex flex-wrap gap-1.5">
                  {action.sources.map((id) => {
                    const s = sources.find((x) => x.id === id)!
                    return (
                      <span key={id} className="flex items-center gap-1.5 rounded-lg border border-line bg-stone-50 px-2 py-1 text-[12px]">
                        <SourceDot color={s.color} letter={s.name[0]} kind={s.kind} />
                        {s.name}
                        <span className={s.stale ? 'text-rose-600' : 'text-stone-400'}>{s.freshness}</span>
                      </span>
                    )
                  })}
                </div>
              </div>

              <div>
                <div className="mb-2 text-[11px] font-medium uppercase tracking-wider text-muted">Audit trail</div>
                <ol className="space-y-2.5 border-l border-line pl-4">
                  {action.audit.map((e, i) => (
                    <li key={i} className="relative">
                      <span className="absolute -left-[20.5px] top-1.5 h-2 w-2 rounded-full border-2 border-white bg-stone-400" />
                      <div className="text-[11px] tabular-nums text-stone-400">
                        {e.time} · {e.actor}
                      </div>
                      <div>{e.event}</div>
                    </li>
                  ))}
                </ol>
              </div>
            </div>

            {action.status === 'pending' ? (
              <div className="border-t border-line p-4">
                <p className="mb-2.5 text-[11px] text-muted">
                  Your approval applies only to these exact parameters. If the amount, payee or date changes, it has to be approved
                  again.
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => onReject(action.id)}
                    className="flex-1 rounded-lg border border-line py-2 text-[13px] font-medium text-stone-700 hover:bg-stone-50"
                  >
                    Reject
                  </button>
                  <button className="flex items-center justify-center gap-1.5 rounded-lg border border-line px-3 py-2 text-[13px] text-stone-700 hover:bg-stone-50">
                    <MessageSquare size={13} /> Ask
                  </button>
                  <button
                    onClick={() => onApprove(action.id)}
                    className="flex-1 rounded-lg bg-brand py-2 text-[13px] font-medium text-white hover:bg-brand/90"
                  >
                    Approve & schedule
                  </button>
                </div>
              </div>
            ) : null}
          </>
        )}
      </aside>
    </>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-[11px] font-medium uppercase tracking-wider text-muted">{label}</div>
      <div className="leading-relaxed text-stone-800">{children}</div>
    </div>
  )
}

function Box({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg bg-stone-50 p-2.5">
      <div className="text-[11px] text-muted">{label}</div>
      <div className="mt-0.5 font-medium leading-snug">{children}</div>
    </div>
  )
}
