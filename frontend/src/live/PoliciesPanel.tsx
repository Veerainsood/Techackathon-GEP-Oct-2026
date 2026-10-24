// Settings → Policies (live): state-based policies for pending invoices and contracts, by region. Every choice comes from the
// data (fields, values, ranges, regions); the backend rejects a policy that would leave a field with no allowed value.
import { Pencil, Plus, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api, errorMessage, type PolicyCatalog, type PolicyInput, type PolicyRow } from './api'

const empty = (collection: PolicyInput['collection'] = 'pending_contracts'): PolicyInput => ({
  name: '',
  collection,
  region: 'ALL',
  variable: '',
  allowed_values: null,
  min_value: null,
  max_value: null,
  hitl_values: [],
  hitl_above: null,
  active: true,
})

const money = (v: number | null | undefined) => (v == null ? '' : `$${Math.round(v).toLocaleString()}`)
const num = (s: string) => (s.trim() === '' ? null : Number(s.replace(/[$,\s]/g, '')))

function Editor({
  catalog,
  initial,
  onSave,
  onCancel,
}: {
  catalog: PolicyCatalog
  initial: PolicyInput
  onSave: (p: PolicyInput) => Promise<void>
  onCancel: () => void
}) {
  const [p, setP] = useState<PolicyInput>(initial)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const coll = catalog.collections.find((c) => c.id === p.collection)!
  const field = coll.fields.find((f) => f.id === p.variable)
  const set = (patch: Partial<PolicyInput>) => setP((x) => ({ ...x, ...patch }))
  const pickField = (id: string) => {
    const f = coll.fields.find((x) => x.id === id)
    set({ variable: id, allowed_values: f?.kind === 'categorical' ? [...(f.values ?? [])] : null, hitl_values: [], min_value: null, max_value: null, hitl_above: null })
  }
  const toggle = (key: 'allowed_values' | 'hitl_values', v: string) => {
    const cur = new Set(p[key] ?? [])
    if (cur.has(v)) cur.delete(v)
    else cur.add(v)
    const next: Partial<PolicyInput> = { [key]: [...cur] }
    if (key === 'allowed_values' && !cur.has(v)) next.hitl_values = (p.hitl_values ?? []).filter((x) => x !== v)
    set(next)
  }
  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await onSave(p)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setSaving(false)
    }
  }
  const input = 'w-full rounded-lg border border-line bg-white px-2.5 py-1.5 text-[13px] outline-none focus:border-stone-400'
  return (
    <div className="mb-4 rounded-xl border border-line bg-white p-4 text-[13px]">
      <div className="grid grid-cols-2 gap-3">
        <label className="col-span-2">
          <div className="mb-1 text-[11px] text-muted">Policy name</div>
          <input className={input} value={p.name} onChange={(e) => set({ name: e.target.value })} placeholder="e.g. Americas contracts up to $1M" />
        </label>
        <label>
          <div className="mb-1 text-[11px] text-muted">Applies to</div>
          <select className={input} value={p.collection} onChange={(e) => setP({ ...empty(e.target.value as PolicyInput['collection']), name: p.name })}>
            {catalog.collections.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label} ({c.records})
              </option>
            ))}
          </select>
        </label>
        <label>
          <div className="mb-1 text-[11px] text-muted">Region</div>
          <select className={input} value={p.region} onChange={(e) => set({ region: e.target.value as PolicyInput['region'] })}>
            {catalog.regions.map((r) => (
              <option key={r.code} value={r.code}>
                {r.name}
                {r.code !== 'ALL' && coll.by_region[r.code] ? ` (${coll.by_region[r.code]})` : ''}
              </option>
            ))}
          </select>
        </label>
        <label className="col-span-2">
          <div className="mb-1 text-[11px] text-muted">State variable</div>
          <select className={input} value={p.variable} onChange={(e) => pickField(e.target.value)}>
            <option value="">Choose a field…</option>
            {coll.fields.map((f) => (
              <option key={f.id} value={f.id}>
                {f.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {field?.kind === 'categorical' && (
        <div className="mt-3 rounded-lg border border-line">
          <div className="grid grid-cols-[1fr_auto_auto] gap-x-6 border-b border-line bg-stone-50 px-3 py-1.5 text-[11px] text-muted">
            <span>Value</span>
            <span>Allowed</span>
            <span>Needs a person</span>
          </div>
          {(field.values ?? []).map((v) => (
            <div key={v} className="grid grid-cols-[1fr_auto_auto] items-center gap-x-6 border-b border-line px-3 py-1.5 last:border-0">
              <span>{v}</span>
              <input type="checkbox" className="justify-self-center" checked={(p.allowed_values ?? []).includes(v)} onChange={() => toggle('allowed_values', v)} />
              <input
                type="checkbox"
                className="justify-self-center"
                disabled={!(p.allowed_values ?? []).includes(v)}
                checked={(p.hitl_values ?? []).includes(v)}
                onChange={() => toggle('hitl_values', v)}
              />
            </div>
          ))}
        </div>
      )}
      {field?.kind === 'numeric' && (
        <div className="mt-3 grid grid-cols-3 gap-3">
          {(
            [
              ['min_value', 'Minimum (USD)'],
              ['max_value', 'Maximum (USD)'],
              ['hitl_above', 'A person approves above (USD)'],
            ] as const
          ).map(([key, label]) => (
            <label key={key}>
              <div className="mb-1 text-[11px] text-muted">{label}</div>
              <input className={input} value={p[key] ?? ''} onChange={(e) => set({ [key]: num(e.target.value) })} placeholder="—" />
            </label>
          ))}
          <div className="col-span-3 text-[11px] text-muted">
            Pending records today range from {money(field.min)} to {money(field.max)}.
          </div>
        </div>
      )}

      {error && <div className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-[12px] text-rose-700">{error}</div>}
      <div className="mt-4 flex items-center justify-between">
        <label className="flex items-center gap-2 text-[12px] text-muted">
          <input type="checkbox" checked={p.active ?? true} onChange={(e) => set({ active: e.target.checked })} /> Active
        </label>
        <div className="flex gap-2">
          <button onClick={onCancel} className="rounded-lg border border-line bg-white px-3 py-1.5 text-[12px] hover:bg-stone-50">
            Cancel
          </button>
          <button
            onClick={save}
            disabled={saving || !p.name.trim() || !p.variable}
            className="rounded-lg bg-ink px-3 py-1.5 text-[12px] font-medium text-white hover:bg-stone-800 disabled:opacity-50"
          >
            {saving ? 'Checking…' : 'Save policy'}
          </button>
        </div>
      </div>
    </div>
  )
}

export function PoliciesPanel() {
  const [rows, setRows] = useState<PolicyRow[]>([])
  const [catalog, setCatalog] = useState<PolicyCatalog | null>(null)
  const [editing, setEditing] = useState<{ id: string | null; value: PolicyInput } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = () =>
    api
      .policies()
      .then(setRows)
      .catch((e) => setError(`Could not load policies: ${errorMessage(e)}`))
  useEffect(() => {
    load()
    api
      .policyCatalog()
      .then(setCatalog)
      .catch((e) => setError(`Could not load the policy fields: ${errorMessage(e)}`))
    const t = setInterval(load, 15000) // violations update as the state check runs
    return () => clearInterval(t)
  }, [])
  const label = useMemo(() => Object.fromEntries((catalog?.collections ?? []).map((c) => [c.id, c.label])), [catalog])

  const save = async (p: PolicyInput) => {
    if (editing?.id) await api.updatePolicy(editing.id, p)
    else await api.createPolicy(p)
    setEditing(null)
    load()
  }
  const remove = async (row: PolicyRow) => {
    try {
      await api.deletePolicy(row.policy_id)
      load()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <div>
      {error && <div className="mb-3 rounded-lg bg-rose-50 px-3 py-2 text-[12px] text-rose-700">{error}</div>}
      <div className="mb-3 flex items-center justify-between text-[12px] text-muted">
        <span>
          {rows.length} polic{rows.length === 1 ? 'y' : 'ies'} · checked on every autopilot action and whenever invoices or contracts change
        </span>
        {!editing && catalog && (
          <button
            onClick={() => setEditing({ id: null, value: empty() })}
            className="flex items-center gap-1.5 rounded-lg bg-ink px-3 py-1.5 text-[12px] font-medium text-white hover:bg-stone-800"
          >
            <Plus size={13} /> New policy
          </button>
        )}
      </div>
      {editing && catalog && <Editor catalog={catalog} initial={editing.value} onSave={save} onCancel={() => setEditing(null)} />}
      <div className="overflow-hidden rounded-xl border border-line bg-white text-[13px]">
        {rows.map((r) => (
          <div key={r.policy_id} className={`flex items-start gap-4 border-b border-line px-4 py-3 last:border-0 ${r.active ? '' : 'opacity-50'}`}>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 font-medium">
                {r.name}
                <span className="rounded bg-stone-100 px-1.5 py-0.5 text-[10px] font-medium text-stone-600">{r.region_name}</span>
                <span className="rounded bg-stone-100 px-1.5 py-0.5 text-[10px] font-medium text-stone-600">{label[r.collection] ?? r.collection}</span>
              </div>
              <div className="mt-0.5 text-[12px] text-stone-600">{r.rule}</div>
              <div className="mt-0.5 text-[11px] text-stone-400">
                {r.policy_id} · v{r.version} · {r.in_scope ?? 0} records in scope
                {r.violations ? <span className="text-rose-600"> · {r.violations} violating now</span> : ''}
              </div>
            </div>
            <div className="flex shrink-0 gap-1">
              <button
                title="Edit"
                onClick={() =>
                  setEditing({
                    id: r.policy_id,
                    value: {
                      name: r.name,
                      collection: r.collection,
                      region: r.region,
                      variable: r.variable,
                      allowed_values: r.allowed_values,
                      min_value: r.min_value,
                      max_value: r.max_value,
                      hitl_values: r.hitl_values,
                      hitl_above: r.hitl_above,
                      active: r.active,
                    },
                  })
                }
                className="rounded-lg p-1.5 text-stone-500 hover:bg-stone-100"
              >
                <Pencil size={14} />
              </button>
              <button title="Delete" onClick={() => remove(r)} className="rounded-lg p-1.5 text-stone-500 hover:bg-rose-50 hover:text-rose-600">
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        ))}
        {!rows.length && <div className="px-4 py-6 text-center text-[12px] text-muted">No policies yet. Without a policy, autopilot takes no action.</div>}
      </div>
    </div>
  )
}
