// Draws the chart plans the backend attaches to an answer. Every value, reference line and takeaway comes from the
// backend, which binds them from the verified result rows; this file only renders them.
import { Check } from 'lucide-react'
import { useEffect, useId, useState, type ReactNode } from 'react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Pie,
  PieChart,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

export interface ChartSpec {
  type: 'bar' | 'line' | 'stacked_bar' | 'donut' | 'kpi'
  title: string
  x?: string | null
  unit?: string // usd, inr, eur, gbp, count, percent, number
  units?: Record<string, string>
  series: { key: string; label: string; dashed?: boolean }[]
  data: Record<string, unknown>[]
  layout?: 'horizontal'
  boundary?: string // last actual point; dashed series continue from here
  references?: { kind: 'average' | 'peak' | 'previous' | 'target'; label: string; value: number; at?: string }[]
  highlight?: string | null
  takeaway?: string
  chosen_by?: string
  note?: string
}

const INK = ['#0b5cab', '#0d9488', '#d97706', '#7c3aed', '#e11d48', '#0891b2', '#db2777', '#64748b']
const TINT = ['#60a5fa', '#2dd4bf', '#fbbf24', '#a78bfa', '#fb7185', '#22d3ee', '#f472b6', '#94a3b8']
const REF_COLOR: Record<string, string> = { average: '#b45309', previous: '#78716c', target: '#be123c', peak: '#0b5cab' }
const tick = { fontSize: 11, fill: '#8a8580' }
const grid = '#efede8'
const SYMBOL: Record<string, string> = { usd: '$', inr: '₹', eur: '€', gbp: '£', jpy: '¥', aud: 'A$', cad: 'C$', sgd: 'S$', aed: 'AED ' }

const toNum = (v: unknown) => (typeof v === 'number' ? v : Number(v ?? 0)) || 0

function format(v: unknown, unit: string = 'number', compact = true): string {
  const n = toNum(v)
  if (unit === 'percent') return `${(Math.abs(n) <= 1 ? n * 100 : n).toFixed(1)}%`
  const opts: Intl.NumberFormatOptions =
    compact && Math.abs(n) >= 10_000 ? { notation: 'compact', maximumFractionDigits: 1 } : { maximumFractionDigits: unit === 'count' ? 0 : 2 }
  const s = Math.abs(n).toLocaleString('en-US', opts)
  return `${n < 0 ? '−' : ''}${SYMBOL[unit.toLowerCase()] ?? ''}${s}`
}

function useCountUp(target: number, ms = 1200) {
  const [v, setV] = useState(0)
  useEffect(() => {
    let raf = 0
    const t0 = performance.now()
    const step = (t: number) => {
      const p = Math.min(1, (t - t0) / ms)
      setV(target * (1 - Math.pow(1 - p, 3)))
      if (p < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return v
}

function CountUp({ value, unit, className }: { value: number; unit: string; className?: string }) {
  const v = useCountUp(value)
  return <span className={className}>{format(unit === 'count' ? Math.round(v) : v, unit)}</span>
}

function Card({ c, headline, children, delay = 0 }: { c: ChartSpec; headline?: ReactNode; children: ReactNode; delay?: number }) {
  return (
    <div
      className="fade-up relative overflow-hidden rounded-2xl border border-line bg-white p-5 shadow-[0_1px_2px_rgba(16,24,40,0.04),0_18px_40px_-28px_rgba(11,92,171,0.35)]"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-28 bg-gradient-to-b from-sky-50/80 to-transparent" />
      <div className="relative">
        <div className="text-[13px] font-medium text-ink">{c.title}</div>
        {headline && <div className="mt-1.5">{headline}</div>}
        {c.takeaway && <div className="mt-1 text-[12.5px] leading-snug text-stone-600">{c.takeaway}</div>}
        <div className="mt-4">{children}</div>
        {c.note && (
          <div className="mt-3 flex items-center gap-1.5 text-[11px] text-emerald-700">
            <Check size={12} strokeWidth={2.5} /> {c.note}
          </div>
        )}
      </div>
    </div>
  )
}

function TooltipBox({ active, payload, label, unit }: { active?: boolean; payload?: { name?: string; value?: unknown; color?: string; payload?: { fill?: string } }[]; label?: unknown; unit: string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-xl border border-line bg-white/95 px-3 py-2 text-[12px] shadow-[0_8px_24px_rgba(16,24,40,0.12)] backdrop-blur">
      {label !== undefined && label !== '' && <div className="mb-1 font-medium text-ink">{String(label)}</div>}
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2 text-stone-600">
          <span className="h-2 w-2 rounded-full" style={{ background: p.color ?? p.payload?.fill }} />
          <span>{p.name}</span>
          <span className="ml-auto pl-4 font-semibold tabular-nums text-ink">{format(p.value, unit, false)}</span>
        </div>
      ))}
    </div>
  )
}

function Legend({ series }: { series: ChartSpec['series'] }) {
  return (
    <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-stone-600">
      {series.map((s, i) => (
        <span key={s.key} className="flex items-center gap-1.5">
          <svg width="18" height="6">
            <line x1="0" y1="3" x2="18" y2="3" stroke={INK[i % INK.length]} strokeWidth="2.5" strokeDasharray={s.dashed ? '4 3' : undefined} strokeLinecap="round" />
          </svg>
          {s.label}
        </span>
      ))}
    </div>
  )
}

function Kpis({ c, delay }: { c: ChartSpec; delay: number }) {
  const row = c.data[0] ?? {}
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.min(c.series.length, 4)}, minmax(0, 1fr))` }}>
      {c.series.map((s, i) => (
        <div
          key={s.key}
          className="fade-up relative overflow-hidden rounded-2xl border border-line bg-white px-5 py-4 shadow-[0_1px_2px_rgba(16,24,40,0.04),0_14px_32px_-24px_rgba(11,92,171,0.4)]"
          style={{ animationDelay: `${delay + i * 100}ms` }}
        >
          <div className="absolute inset-x-0 top-0 h-[3px]" style={{ background: `linear-gradient(90deg, ${INK[i % INK.length]}, ${TINT[i % TINT.length]})` }} />
          <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted">{s.label}</div>
          <CountUp value={toNum(row[s.key])} unit={c.units?.[s.key] ?? c.unit ?? 'number'} className="mt-1 block font-serif text-[32px] leading-tight tracking-tight text-ink tabular-nums" />
        </div>
      ))}
    </div>
  )
}

function Trend({ c, delay }: { c: ChartSpec; delay: number }) {
  const id = useId().replace(/:/g, '')
  const unit = c.unit ?? 'number'
  const [only, setOnly] = useState<string | null>(null)
  const series = c.series.filter((s) => !only || s.key === only)
  const lastRow = c.data[c.data.length - 1]
  const lastKey = [...series].reverse().find((s) => typeof lastRow?.[s.key] === 'number')?.key ?? series[0].key
  const firstKey = series.find((s) => !s.dashed)?.key ?? series[0].key
  const first = toNum(c.data.find((d) => typeof d[firstKey] === 'number')?.[firstKey])
  const latest = toNum(lastRow?.[lastKey])
  const change = first ? ((latest - first) / Math.abs(first)) * 100 : null
  const refs = (c.references ?? []).filter(() => series.length === c.series.length || series.length === 1)
  const headline = (
    <div className="flex flex-wrap items-baseline gap-2.5">
      <CountUp value={latest} unit={unit} className="font-serif text-[28px] leading-none tracking-tight text-ink tabular-nums" />
      <span className="text-[11px] text-muted">{String(lastRow?.label ?? '')}</span>
      {change !== null && Number.isFinite(change) && (
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${change >= 0 ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>
          {change >= 0 ? '▲' : '▼'} {Math.abs(change).toFixed(Math.abs(change) < 10 ? 1 : 0)}%
        </span>
      )}
    </div>
  )
  const lastLabel = String(lastRow?.label ?? '')
  return (
    <Card c={c} headline={headline} delay={delay}>
      {c.series.length > 1 && !c.boundary && (
        <div className="mb-2 flex flex-wrap gap-1.5 text-[11px]">
          {[{ key: '', label: 'All' }, ...c.series].map((s) => (
            <button
              key={s.key || 'all'}
              onClick={() => setOnly(s.key || null)}
              className={`rounded-full border px-2.5 py-1 transition ${(only ?? '') === s.key ? 'border-ink/20 bg-stone-100 text-ink' : 'border-line text-stone-500 hover:text-ink'}`}
            >
              {s.label}
            </button>
          ))}
        </div>
      )}
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={c.data} margin={{ top: 22, right: 18, left: 4, bottom: 0 }}>
            <defs>
              <filter id={`soft-${id}`} x="-10%" y="-40%" width="120%" height="180%">
                <feDropShadow dx="0" dy="6" stdDeviation="6" floodColor="#0b5cab" floodOpacity="0.18" />
              </filter>
              {c.series.map((s, i) => (
                <g key={s.key}>
                  <linearGradient id={`stroke-${id}-${i}`} x1="0" y1="0" x2="1" y2="0">
                    <stop offset="0%" stopColor={INK[i % INK.length]} />
                    <stop offset="100%" stopColor={TINT[i % TINT.length]} />
                  </linearGradient>
                  <linearGradient id={`fill-${id}-${i}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={TINT[i % TINT.length]} stopOpacity={s.dashed ? 0.12 : 0.28} />
                    <stop offset="100%" stopColor={TINT[i % TINT.length]} stopOpacity={0} />
                  </linearGradient>
                </g>
              ))}
            </defs>
            <CartesianGrid stroke={grid} vertical={false} />
            {c.boundary && c.boundary !== lastLabel && (
              <ReferenceArea x1={c.boundary} x2={lastLabel} fill="#eef2ff" fillOpacity={0.6} strokeOpacity={0}
                label={{ value: 'Forecast', position: 'insideTop', fontSize: 10, fill: '#6366f1' }} />
            )}
            <XAxis dataKey="label" tick={tick} tickLine={false} axisLine={false} minTickGap={10} interval="preserveStartEnd" />
            <YAxis tick={tick} width={62} tickLine={false} axisLine={false} tickFormatter={(v) => format(v, unit)} />
            <Tooltip content={<TooltipBox unit={unit} />} cursor={{ stroke: '#d6d3cd', strokeDasharray: '3 3' }} />
            {refs.filter((r) => r.kind !== 'peak').map((r) => (
              <ReferenceLine key={r.kind} y={r.value} stroke={REF_COLOR[r.kind]} strokeOpacity={0.7} strokeDasharray="4 4"
                label={{ value: `${r.label} ${format(r.value, unit)}`, position: 'insideTopLeft', fontSize: 10, fill: REF_COLOR[r.kind] }} />
            ))}
            {series.map((s) => {
              const i = c.series.indexOf(s)
              return (
                <Area key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={`url(#stroke-${id}-${i})`} strokeWidth={2.75}
                  strokeDasharray={s.dashed ? '7 5' : undefined} fill={`url(#fill-${id}-${i})`} filter={`url(#soft-${id})`}
                  connectNulls animationDuration={1600} animationEasing="ease-out" animationBegin={delay + i * 300} dot={false}
                  activeDot={{ r: 5.5, fill: '#fff', stroke: INK[i % INK.length], strokeWidth: 2.5 }} />
              )
            })}
            {refs.filter((r) => r.kind === 'peak' && r.at && r.at !== lastLabel).map((r) => (
              <ReferenceDot key="peak" x={r.at} y={r.value} r={4.5} fill="#fff" stroke={INK[0]} strokeWidth={2.5}
                label={{ value: `Peak ${format(r.value, unit)}`, position: 'top', fontSize: 10, fill: '#44403c' }} />
            ))}
            <ReferenceDot x={lastLabel} y={latest} r={0}
              shape={(p: { cx?: number; cy?: number }) => (
                <g>
                  <circle cx={p.cx} cy={p.cy} r={9} fill={INK[Math.max(0, c.series.findIndex((s) => s.key === lastKey))]} opacity={0.25}
                    className="animate-ping" style={{ transformBox: 'fill-box', transformOrigin: 'center' }} />
                  <circle cx={p.cx} cy={p.cy} r={4.5} fill="#fff" stroke={INK[Math.max(0, c.series.findIndex((s) => s.key === lastKey))]} strokeWidth={2.5} />
                </g>
              )} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      {c.series.length > 1 && <Legend series={c.series} />}
    </Card>
  )
}

function Bars({ c, delay }: { c: ChartSpec; delay: number }) {
  const id = useId().replace(/:/g, '')
  const unit = c.unit ?? 'number'
  const stacked = c.type === 'stacked_bar'
  const horizontal = c.layout === 'horizontal'
  const single = c.series.length === 1 && !stacked
  const many = c.data.length > 6
  const focus = c.highlight ?? null
  const refs = (c.references ?? []).filter((r) => r.kind !== 'peak')
  return (
    <Card c={c} delay={delay}>
      <div style={{ height: horizontal ? Math.max(170, c.data.length * 36 + 24) : 270 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={c.data} layout={horizontal ? 'vertical' : 'horizontal'}
            margin={{ top: 18, right: horizontal ? 56 : 12, left: 4, bottom: !horizontal && many ? 44 : 0 }} barCategoryGap={horizontal ? 8 : '24%'}>
            <defs>
              {c.series.map((s, i) => (
                <linearGradient key={s.key} id={`bar-${id}-${i}`} x1="0" y1={horizontal ? '0' : '1'} x2={horizontal ? '1' : '0'} y2="0">
                  <stop offset="0%" stopColor={INK[i % INK.length]} />
                  <stop offset="100%" stopColor={TINT[i % TINT.length]} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid stroke={grid} horizontal={!horizontal} vertical={horizontal} />
            {horizontal ? (
              <>
                <XAxis type="number" tick={tick} tickLine={false} axisLine={false} tickFormatter={(v) => format(v, unit)} />
                <YAxis type="category" dataKey="label" tick={{ ...tick, fill: '#57534e' }} width={150} tickLine={false} axisLine={false}
                  tickFormatter={(v: string) => (v.length > 22 ? `${v.slice(0, 21)}…` : v)} />
              </>
            ) : (
              <>
                <XAxis dataKey="label" tick={tick} tickLine={false} axisLine={false} interval={0} angle={many ? -30 : 0}
                  textAnchor={many ? 'end' : 'middle'} tickFormatter={(v: string) => (v.length > 18 ? `${v.slice(0, 17)}…` : v)} />
                <YAxis tick={tick} width={62} tickLine={false} axisLine={false} tickFormatter={(v) => format(v, unit)} />
              </>
            )}
            <Tooltip content={<TooltipBox unit={unit} />} cursor={{ fill: 'rgba(11,92,171,0.05)' }} />
            {refs.map((r) =>
              horizontal ? (
                <ReferenceLine key={r.kind} x={r.value} stroke={REF_COLOR[r.kind]} strokeDasharray="4 4"
                  label={{ value: r.label, position: 'top', fontSize: 10, fill: REF_COLOR[r.kind] }} />
              ) : (
                <ReferenceLine key={r.kind} y={r.value} stroke={REF_COLOR[r.kind]} strokeDasharray="4 4"
                  label={{ value: `${r.label} ${format(r.value, unit)}`, position: 'insideTopRight', fontSize: 10, fill: REF_COLOR[r.kind] }} />
              ),
            )}
            {c.series.map((s, i) => (
              <Bar key={s.key} dataKey={s.key} name={s.label} stackId={stacked ? 'stack' : undefined} fill={`url(#bar-${id}-${i})`}
                radius={stacked ? (i === c.series.length - 1 ? (horizontal ? [0, 5, 5, 0] : [5, 5, 0, 0]) : 0) : horizontal ? [0, 7, 7, 0] : [7, 7, 0, 0]}
                maxBarSize={horizontal ? 22 : 54} animationDuration={1200} animationBegin={delay + i * 160} animationEasing="ease-out">
                {single && focus && c.data.map((d, j) => <Cell key={j} fillOpacity={d.label === focus ? 1 : 0.45} />)}
                {single && c.data.length <= 12 && (
                  <LabelList dataKey={s.key} position={horizontal ? 'right' : 'top'} formatter={(v: unknown) => format(v, unit)}
                    style={{ fontSize: 11, fill: '#44403c', fontWeight: 500 }} />
                )}
              </Bar>
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      {!single && <Legend series={c.series} />}
    </Card>
  )
}

function Donut({ c, delay }: { c: ChartSpec; delay: number }) {
  const key = c.series[0].key
  const unit = c.unit ?? 'number'
  const total = c.data.reduce((t, d) => t + toNum(d[key]), 0)
  const [active, setActive] = useState<number | undefined>(undefined)
  const focus = active !== undefined ? c.data[active] : undefined
  return (
    <Card c={c} delay={delay}>
      <div className="flex flex-col items-center gap-5">
        <div className="relative h-48 w-48 shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={c.data} dataKey={key} nameKey="label" innerRadius="64%" outerRadius="92%" paddingAngle={2.5} stroke="#fff" strokeWidth={2}
                startAngle={90} endAngle={-270} cornerRadius={4} animationDuration={1400} animationBegin={delay}
                onMouseEnter={(_, i) => setActive(i)} onMouseLeave={() => setActive(undefined)}>
                {c.data.map((_, i) => (
                  <Cell key={i} fill={INK[i % INK.length]} fillOpacity={active === undefined || active === i ? 1 : 0.3} style={{ transition: 'fill-opacity 200ms' }} />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
            {focus ? (
              <>
                <div className="font-serif text-[22px] text-ink tabular-nums">{format(focus[key], unit)}</div>
                <div className="max-w-[110px] truncate text-[10px] uppercase tracking-wider text-muted">{String(focus.label)}</div>
              </>
            ) : (
              <>
                <CountUp value={total} unit={unit} className="font-serif text-[22px] text-ink tabular-nums" />
                <div className="text-[10px] uppercase tracking-wider text-muted">total</div>
              </>
            )}
          </div>
        </div>
        <div className="w-full space-y-2 text-[12px]">
          {c.data.map((d, i) => {
            const share = total ? (toNum(d[key]) / total) * 100 : 0
            return (
              <div key={i} onMouseEnter={() => setActive(i)} onMouseLeave={() => setActive(undefined)}
                className={`rounded-lg px-2 py-1 transition ${active === i ? 'bg-stone-50' : ''}`}>
                <div className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: INK[i % INK.length] }} />
                  <span className="truncate text-stone-700">{String(d.label)}</span>
                  <span className="ml-auto pl-2 tabular-nums text-muted">{share.toFixed(share < 10 ? 1 : 0)}%</span>
                  <span className="w-20 text-right font-semibold text-ink tabular-nums">{format(d[key], unit)}</span>
                </div>
                <div className="mt-1 h-1 overflow-hidden rounded-full bg-stone-100">
                  <div className="h-full rounded-full transition-[width] duration-1000 ease-out" style={{ width: `${share}%`, background: INK[i % INK.length] }} />
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </Card>
  )
}

export function Charts({ charts }: { charts: ChartSpec[] }) {
  const kpis = charts.filter((c) => c.type === 'kpi' && c.data.length && c.series.length)
  const plots = charts.filter((c) => c.type !== 'kpi' && c.data.length && c.series.length)
  if (!kpis.length && !plots.length) return null
  const pair = plots.length === 2 && (plots.some((c) => c.type === 'donut') || plots.every((c) => c.type === 'line' && c.boundary))
  return (
    <div className="space-y-3">
      {kpis.map((c, i) => (
        <Kpis key={`k${i}`} c={c} delay={i * 100} />
      ))}
      {plots.length > 0 && (
        <div className={`grid gap-3 ${pair ? 'md:grid-cols-2' : ''}`}>
          {plots.map((c, i) => {
            const delay = 120 + i * 180
            return c.type === 'donut' ? <Donut key={i} c={c} delay={delay} /> : c.type === 'line' ? <Trend key={i} c={c} delay={delay} /> : <Bars key={i} c={c} delay={delay} />
          })}
        </div>
      )}
    </div>
  )
}
