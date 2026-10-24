import { CheckCircle2, Inbox, Lock, RefreshCw } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { boundaries, policies, sources, type Action, type Boundary, type Mode, type Source } from '../data'
import { api } from '../live/api'
import { LIVE, toBoundary } from '../live/adapter'
import { PoliciesPanel } from '../live/PoliciesPanel'
import { ActionRow } from './Thread'
import { SourceDot } from './ui'

function Page({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className="mx-auto max-w-[820px] px-6 py-10">
        <h1 className="font-serif text-[32px] tracking-tight">{title}</h1>
        <p className="mt-1 text-[14px] text-muted">{subtitle}</p>
        <div className="mt-6">{children}</div>
      </div>
    </div>
  )
}

export function ApprovalsPage({ actions, onOpen, onApprove }: { actions: Action[]; onOpen: (id: string) => void; onApprove: (id: string) => void }) {
  const pending = actions.filter((a) => a.status === 'pending' || a.status === 'blocked')
  return (
    <Page title="Approvals" subtitle="Actions the agent can't take without you. Each one shows why it was proposed, what it will change, and which checks it ran.">
      {pending.length ? (
        <div className="space-y-2">
          {pending.map((a) => (
            <ActionRow key={a.id} a={a} onOpen={onOpen} onApprove={onApprove} />
          ))}
        </div>
      ) : (
        <div className="flex flex-col items-center rounded-2xl border border-dashed border-line py-16 text-muted">
          <Inbox size={22} />
          <div className="mt-2 text-[14px]">You're all caught up</div>
        </div>
      )}
    </Page>
  )
}

export interface ActivityRow {
  time: string
  actor: string
  event: string
  ref: string
  onClick?: () => void
}

export function ActivityPage({ actions, onOpen, entries }: { actions: Action[]; onOpen: (id: string) => void; entries?: ActivityRow[] }) {
  const events: ActivityRow[] =
    entries ??
    actions
      .flatMap((a) => a.audit.map((e) => ({ ...e, ref: a.id, onClick: () => onOpen(a.id) })))
      .sort((x, y) => y.time.localeCompare(x.time))
  return (
    <Page title="Activity log" subtitle="Every decision and action, traced from the goal to the result. Read-only and access-controlled.">
      <div className="overflow-hidden rounded-xl border border-line bg-white text-[13px]">
        {events.map((e, i) => (
          <button
            key={i}
            onClick={e.onClick}
            className="flex w-full items-center gap-4 border-b border-line px-4 py-2.5 text-left last:border-0 hover:bg-stone-50"
          >
            <span className="w-16 shrink-0 tabular-nums text-stone-400">{e.time}</span>
            <span className="w-36 shrink-0 truncate text-stone-500">{e.actor}</span>
            <span className="flex-1 truncate">{e.event}</span>
            <span className="shrink-0 text-[11px] text-stone-400">{e.ref}</span>
          </button>
        ))}
      </div>
    </Page>
  )
}

const modeMeta: Record<Mode, { label: string; on: string }> = {
  auto: { label: 'Autonomous', on: 'bg-brand text-white' },
  approve: { label: 'Ask me', on: 'bg-amber-500 text-white' },
  never: { label: 'Never', on: 'bg-stone-700 text-white' },
}

const settingsTabs = [
  {
    id: 'guardrails',
    label: 'Guardrails',
    desc: 'Who decides each action autopilot can take on records your policies cover: autopilot itself, you (Approvals), or nobody. A policy violation blocks the action whatever is set here.',
  },
  {
    id: 'policies',
    label: 'Policies',
    desc: 'Which states pending invoices and contracts may reach, by region. A violation blocks the action; Guardrails then decide whether autopilot acts or asks you.',
  },
  { id: 'notifications', label: 'Notifications', desc: 'Where the agent reaches you when something needs a decision.' },
] as const

const channels = [
  { name: 'In-app', detail: 'Approvals inbox and notification panel', on: true, locked: true },
  { name: 'Email', detail: 'meera.rao@northwind.in · instant for approvals', on: true },
  { name: 'Slack', detail: '#finance-approvals · approve from Slack', on: true },
  { name: 'Microsoft Teams', detail: 'Finance Ops channel', on: false },
  { name: 'Daily digest', detail: '8:00 AM · what the agent did overnight', on: true },
  { name: 'Escalate if unanswered', detail: 'After 4 hours, route to delegate', on: true },
]

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e))

export function SettingsPage() {
  const [tab, setTab] = useState<(typeof settingsTabs)[number]['id']>('guardrails')
  const [rows, setRows] = useState<Boundary[]>(LIVE ? [] : boundaries)
  const [loaded, setLoaded] = useState(!LIVE)
  const [guardError, setGuardError] = useState<string | null>(null)
  const [chan, setChan] = useState(channels)
  const count = (m: Mode) => rows.filter((r) => r.mode === m).length
  const active = settingsTabs.find((t) => t.id === tab)!

  useEffect(() => {
    if (!LIVE || tab !== 'guardrails') return // reloaded on every visit: policies decide which actions are listed
    api
      .guardrails()
      .then((g) => {
        setRows(g.map(toBoundary))
        setGuardError(null)
      })
      .catch((e) => setGuardError(`Could not load guardrails: ${errorText(e)}`))
      .finally(() => setLoaded(true))
  }, [tab])

  // Optimistic update: the row changes at once and is reverted if the server refuses the change.
  const changeMode = (row: Boundary, m: Mode) => {
    const previous = row.mode
    const setMode = (mode: Mode) => setRows((prev) => prev.map((x) => (x.action === row.action ? { ...x, mode } : x)))
    setMode(m)
    if (!LIVE || !row.id) return
    api
      .setGuardrail(row.id, m)
      .then(() => setGuardError(null))
      .catch((e) => {
        setMode(previous)
        setGuardError(`Could not save "${row.action}": ${errorText(e)}`)
      })
  }

  return (
    <Page title="Settings" subtitle="Control what the agent may do on its own, the rules it follows, and how it reaches you.">
      <div className="mb-5 flex gap-5 border-b border-line text-[13px]">
        {settingsTabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`-mb-px border-b-2 pb-2.5 transition ${
              tab === t.id ? 'border-ink font-medium text-ink' : 'border-transparent text-muted hover:text-ink'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <p className="mb-4 text-[13px] text-muted">{active.desc}</p>

      {tab === 'guardrails' && (
        <>
      {guardError && <div className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-[12px] text-rose-700">{guardError}</div>}
      {!loaded && <div className="mb-3 text-[12px] text-muted">Loading guardrails…</div>}
      {LIVE && loaded && !rows.length && !guardError && (
        <div className="mb-3 rounded-xl border border-line bg-white px-4 py-6 text-center text-[12px] text-muted">
          No guardrails yet. Autopilot only acts on pending invoices or contracts that a policy covers; add one in the Policies tab and
          its actions appear here.
        </div>
      )}
      <div className="mb-3 flex items-center gap-2 text-[12px]">
        {(['auto', 'approve', 'never'] as Mode[]).map((m) => (
          <span key={m} className="flex items-center gap-1.5 rounded-full border border-line bg-white px-2.5 py-1">
            <span className={`h-2 w-2 rounded-full ${modeMeta[m].on.split(' ')[0]}`} />
            {modeMeta[m].label} · <b>{count(m)}</b>
          </span>
        ))}
      </div>
      <div className="overflow-hidden rounded-xl border border-line bg-white text-[13px]">
        {rows.map((r) => (
          <div key={r.action} className="flex items-center gap-4 border-b border-line px-4 py-3 last:border-0">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5 font-medium">
                {r.locked && <Lock size={12} className="text-stone-400" />}
                {r.action}
              </div>
              <div className="text-[11px] text-stone-400">
                {r.agent} agent · {r.limit}
              </div>
            </div>
            <div className={`flex rounded-lg bg-stone-100 p-0.5 text-[11px] ${r.locked ? 'opacity-60' : ''}`}>
              {(['auto', 'approve', 'never'] as Mode[]).map((m) => (
                <button
                  key={m}
                  disabled={r.locked}
                  onClick={() => changeMode(r, m)}
                  className={`rounded-md px-2.5 py-1 font-medium transition ${
                    r.mode === m ? modeMeta[m].on + ' shadow-sm' : 'text-stone-500 hover:text-ink'
                  } ${r.locked ? 'cursor-not-allowed' : ''}`}
                >
                  {modeMeta[m].label}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

        </>
      )}

      {tab === 'policies' && LIVE && <PoliciesPanel />}
      {tab === 'policies' && !LIVE && (
      <div className="overflow-hidden rounded-xl border border-line bg-white text-[13px]">
        <table className="w-full">
          <thead className="bg-stone-50 text-left text-[11px] uppercase tracking-wider text-muted">
            <tr>
              <th className="px-4 py-2 font-medium">Policy</th>
              <th className="px-4 py-2 font-medium">Rule</th>
              <th className="px-4 py-2 font-medium">Owner</th>
              <th className="px-4 py-2 font-medium">Max autonomy</th>
            </tr>
          </thead>
          <tbody>
            {policies.map((p) => (
              <tr key={p.id} className="border-t border-line">
                <td className="px-4 py-3">
                  <div className="font-medium">{p.name}</div>
                  <div className="text-[11px] text-stone-400">
                    {p.id} · {p.version}
                  </div>
                </td>
                <td className="px-4 py-3 text-stone-600">{p.rule}</td>
                <td className="px-4 py-3 text-stone-600">{p.owner}</td>
                <td className="px-4 py-3">
                  <span className="inline-flex items-center gap-1 rounded bg-stone-100 px-1.5 py-0.5 text-[11px] font-semibold text-stone-600">
                    {p.autonomy === 'L1' && <Lock size={10} />}
                    {p.autonomy}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      )}

      {tab === 'notifications' && (
        <div className="overflow-hidden rounded-xl border border-line bg-white text-[13px]">
          {chan.map((c, i) => (
            <div key={c.name} className="flex items-center justify-between border-b border-line px-4 py-3 last:border-0">
              <div>
                <div className="font-medium">{c.name}</div>
                <div className="text-[11px] text-stone-400">{c.detail}</div>
              </div>
              <button
                disabled={c.locked}
                onClick={() => setChan((prev) => prev.map((x, j) => (j === i ? { ...x, on: !x.on } : x)))}
                className={`relative h-5 w-9 rounded-full transition ${c.on ? 'bg-brand' : 'bg-stone-300'} ${c.locked ? 'opacity-60' : ''}`}
              >
                <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${c.on ? 'left-[18px]' : 'left-0.5'}`} />
              </button>
            </div>
          ))}
        </div>
      )}
    </Page>
  )
}

export function SourcesPage({ items = sources }: { items?: Source[] }) {
  return (
    <Page title="Sources" subtitle="Connected systems with the minimum access each one needs. Stale data is flagged, not used as if it were current.">
      <div className="grid grid-cols-2 gap-3">
        {items.map((s) => (
          <div key={s.id} className={`rounded-xl border bg-white p-4 ${s.stale ? 'border-rose-200' : 'border-line'}`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <SourceDot color={s.color} letter={s.name[0]} />
                <span className="text-[14px] font-medium">{s.name}</span>
              </div>
              {s.stale ? (
                <button className="flex items-center gap-1 rounded-md bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-700">
                  <RefreshCw size={11} /> Resync
                </button>
              ) : (
                <CheckCircle2 size={15} className="text-emerald-600" />
              )}
            </div>
            <div className="mt-2 text-[12px] text-muted">{s.system}</div>
            <div className="mt-0.5 text-[13px]">{s.detail}</div>
            <div className={`mt-2 text-[11px] ${s.stale ? 'font-medium text-rose-600' : 'text-stone-400'}`}>Last sync {s.freshness}</div>
          </div>
        ))}
      </div>
    </Page>
  )
}
