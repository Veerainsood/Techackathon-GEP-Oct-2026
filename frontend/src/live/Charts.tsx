// Draws the chart plans the backend attaches to an answer. Values come from the verified result rows.
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
  unit?: 'usd' | 'count' | 'percent' | 'number'
  units?: Record<string, string>
  series: { key: string; label: string }[]
  data: Record<string, unknown>[]
  layout?: 'horizontal'
  chosen_by?: string
  note?: string
}

const PALETTE = ['#38bdf8', '#34d399', '#fbbf24', '#a78bfa', '#f472b6', '#22d3ee', '#fb7185', '#94a3b8']
const DEEP = ['#6366f1', '#059669', '#d97706', '#7c3aed', '#db2777', '#0891b2', '#e11d48', '#64748b']
const tick = { fontSize: 11, fill: 'rgba(226,232,240,0.55)' }
const grid = 'rgba(148,163,184,0.12)'

const toNum = (v: unknown) => (typeof v === 'number' ? v : Number(v ?? 0)) || 0

function format(v: unknown, unit: string = 'number', compact = true): string {
  const n = toNum(v)
  if (unit === 'percent') return `${(Math.abs(n) <= 1 ? n * 100 : n).toFixed(1)}%`
  const opts: Intl.NumberFormatOptions =
    compact && Math.abs(n) >= 10_000 ? { notation: 'compact', maximumFractionDigits: 1 } : { maximumFractionDigits: unit === 'count' ? 0 : 2 }
  const s = Math.abs(n).toLocaleString('en-US', opts)
  return `${n < 0 ? '−' : ''}${unit === 'usd' ? '$' : ''}${s}`
}

function useCountUp(target: number, ms = 1400) {
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

function Stage({ title, headline, children, note, delay = 0 }: { title: string; headline?: ReactNode; children: ReactNode; note?: string; delay?: number }) {
  return (
    <div
      className="fade-up relative overflow-hidden rounded-2xl border border-white/5 bg-[#0b1324] p-5 text-white shadow-[0_24px_60px_-28px_rgba(37,99,235,0.6)]"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="pointer-events-none absolute -right-24 -top-28 h-64 w-64 rounded-full bg-sky-500/20 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-32 -left-20 h-64 w-64 rounded-full bg-indigo-600/20 blur-3xl" />
      <div className="relative">
        <div className="text-[12px] font-medium tracking-wide text-slate-300/80">{title}</div>
        {headline && <div className="mt-1">{headline}</div>}
        <div className="mt-4">{children}</div>
        {note && (
          <div className="mt-3 flex items-center gap-1.5 text-[11px] text-emerald-300/80">
            <Check size={12} strokeWidth={2.5} /> {note}
          </div>
        )}
      </div>
    </div>
  )
}

function TooltipBox({ active, payload, label, unit }: { active?: boolean; payload?: { name?: string; value?: unknown; color?: string; payload?: { fill?: string } }[]; label?: unknown; unit: string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-xl border border-white/10 bg-[#0f1a2e]/95 px-3 py-2 text-[12px] text-white shadow-2xl backdrop-blur">
      {label !== undefined && label !== '' && <div className="mb-1 font-medium">{String(label)}</div>}
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2 text-slate-300">
          <span className="h-2 w-2 rounded-full" style={{ background: p.color ?? p.payload?.fill }} />
          <span>{p.name}</span>
          <span className="ml-auto pl-4 font-semibold tabular-nums text-white">{format(p.value, unit, false)}</span>
        </div>
      ))}
    </div>
  )
}

function Delta({ from, to, since }: { from: number; to: number; since: string }) {
  if (!from) return null
  const pct = ((to - from) / Math.abs(from)) * 100
  const up = pct >= 0
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${up ? 'bg-emerald-400/15 text-emerald-300' : 'bg-rose-400/15 text-rose-300'}`}>
      {up ? '▲' : '▼'} {Math.abs(pct).toFixed(pct > -10 && pct < 10 ? 1 : 0)}% <span className="text-white/40">since {since}</span>
    </span>
  )
}

function Kpis({ c, delay }: { c: ChartSpec; delay: number }) {
  const row = c.data[0] ?? {}
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.min(c.series.length, 4)}, minmax(0, 1fr))` }}>
      {c.series.map((s, i) => (
        <div
          key={s.key}
          className="fade-up relative overflow-hidden rounded-2xl border border-white/5 bg-[#0b1324] px-5 py-4 text-white shadow-[0_20px_50px_-28px_rgba(37,99,235,0.7)]"
          style={{ animationDelay: `${delay + i * 120}ms` }}
        >
          <div className="absolute inset-x-0 top-0 h-[3px]" style={{ background: `linear-gradient(90deg, ${PALETTE[i % PALETTE.length]}, ${DEEP[i % DEEP.length]})` }} />
          <div className="pointer-events-none absolute -right-10 -top-14 h-36 w-36 rounded-full blur-2xl" style={{ background: `${PALETTE[i % PALETTE.length]}33` }} />
          <div className="relative text-[11px] font-medium uppercase tracking-[0.14em] text-slate-400">{s.label}</div>
          <CountUp value={toNum(row[s.key])} unit={c.units?.[s.key] ?? c.unit ?? 'number'} className="relative mt-1 block font-serif text-[34px] leading-tight tracking-tight tabular-nums" />
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
  const main = series[0]
  const values = c.data.map((d) => toNum(d[main.key]))
  const last = values[values.length - 1] ?? 0
  const peak = Math.max(...values)
  const peakAt = c.data[values.indexOf(peak)]
  const avg = values.reduce((a, b) => a + b, 0) / Math.max(1, values.length)
  const lastIndex = c.data.length - 1
  const headline = (
    <div className="flex flex-wrap items-baseline gap-3">
      <CountUp value={last} unit={unit} className="font-serif text-[30px] leading-none tracking-tight tabular-nums" />
      <span className="text-[11px] text-slate-400">latest · {String(c.data[lastIndex]?.label ?? '')}</span>
      <Delta from={values[0]} to={last} since={String(c.data[0]?.label ?? '')} />
    </div>
  )
  return (
    <Stage title={c.title} headline={headline} note={c.note} delay={delay}>
      {c.series.length > 1 && (
        <div className="mb-2 flex flex-wrap gap-1.5 text-[11px]">
          {[{ key: '', label: 'All' }, ...c.series].map((s, i) => (
            <button
              key={s.key || 'all'}
              onClick={() => setOnly(s.key || null)}
              className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 transition ${
                (only ?? '') === s.key ? 'border-white/20 bg-white/10 text-white' : 'border-white/5 text-slate-400 hover:text-white'
              }`}
            >
              {s.key && <span className="h-1.5 w-1.5 rounded-full" style={{ background: PALETTE[(i - 1) % PALETTE.length] }} />}
              {s.label}
            </button>
          ))}
        </div>
      )}
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={c.data} margin={{ top: 22, right: 18, left: 4, bottom: 0 }}>
            <defs>
              <filter id={`glow-${id}`} x="-20%" y="-50%" width="140%" height="200%">
                <feGaussianBlur stdDeviation="4" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
              {c.series.map((s, i) => (
                <g key={s.key}>
                  <linearGradient id={`stroke-${id}-${i}`} x1="0" y1="0" x2="1" y2="0">
                    <stop offset="0%" stopColor={DEEP[i % DEEP.length]} />
                    <stop offset="100%" stopColor={PALETTE[i % PALETTE.length]} />
                  </linearGradient>
                  <linearGradient id={`fill-${id}-${i}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={PALETTE[i % PALETTE.length]} stopOpacity={0.35} />
                    <stop offset="100%" stopColor={PALETTE[i % PALETTE.length]} stopOpacity={0} />
                  </linearGradient>
                </g>
              ))}
            </defs>
            <CartesianGrid stroke={grid} vertical={false} />
            <XAxis dataKey="label" tick={tick} tickLine={false} axisLine={false} minTickGap={24} />
            <YAxis tick={tick} width={60} tickLine={false} axisLine={false} tickFormatter={(v) => format(v, unit)} />
            <Tooltip content={<TooltipBox unit={unit} />} cursor={{ stroke: 'rgba(255,255,255,0.25)', strokeDasharray: '3 3' }} />
            {series.length === 1 && c.data.length > 2 && (
              <ReferenceLine y={avg} stroke="rgba(251,191,36,0.55)" strokeDasharray="4 4"
                label={{ value: `avg ${format(avg, unit)}`, position: 'insideTopLeft', fontSize: 10, fill: 'rgba(251,191,36,0.85)' }} />
            )}
            {series.map((s) => {
              const i = c.series.indexOf(s)
              return (
                <Area key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={`url(#stroke-${id}-${i})`} strokeWidth={3}
                  fill={`url(#fill-${id}-${i})`} filter={`url(#glow-${id})`} animationDuration={1800} animationEasing="ease-out"
                  animationBegin={delay + i * 250} dot={false}
                  activeDot={{ r: 6, fill: '#fff', stroke: PALETTE[i % PALETTE.length], strokeWidth: 3 }} />
              )
            })}
            {series.length === 1 && peakAt && c.data.length > 2 && (
              <ReferenceDot x={String(peakAt.label)} y={peak} r={5} fill="#fff" stroke={PALETTE[0]} strokeWidth={3}
                label={{ value: `peak ${format(peak, unit)}`, position: 'top', fontSize: 10, fill: '#e2e8f0' }} />
            )}
            {series.length === 1 && (
              <ReferenceDot x={String(c.data[lastIndex]?.label)} y={last} r={0}
                shape={(p: { cx?: number; cy?: number }) => (
                  <g>
                    <circle cx={p.cx} cy={p.cy} r={10} fill={PALETTE[0]} opacity={0.35} className="animate-ping" style={{ transformBox: 'fill-box', transformOrigin: 'center' }} />
                    <circle cx={p.cx} cy={p.cy} r={4.5} fill="#fff" stroke={PALETTE[0]} strokeWidth={2.5} />
                  </g>
                )} />
            )}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Stage>
  )
}

function Bars({ c, delay }: { c: ChartSpec; delay: number }) {
  const id = useId().replace(/:/g, '')
  const unit = c.unit ?? 'number'
  const stacked = c.type === 'stacked_bar'
  const horizontal = c.layout === 'horizontal'
  const single = c.series.length === 1 && !stacked
  const many = c.data.length > 6
  const key = c.series[0].key
  const totals = c.data.map((d) => (stacked ? c.series.reduce((t, s) => t + toNum(d[s.key]), 0) : toNum(d[key])))
  const sum = totals.reduce((a, b) => a + b, 0)
  const top = totals.indexOf(Math.max(...totals))
  const headline = c.data[top] ? (
    <div className="flex flex-wrap items-baseline gap-3">
      <span className="font-serif text-[26px] leading-none tracking-tight">{String(c.data[top].label)}</span>
      <span className="text-[12px] text-slate-400">
        leads with <span className="font-semibold text-white">{format(totals[top], unit)}</span>
        {sum > 0 && totals.length > 1 && <> · {Math.round((totals[top] / sum) * 100)}% of the total</>}
      </span>
    </div>
  ) : undefined
  return (
    <Stage title={c.title} headline={headline} note={c.note} delay={delay}>
      <div style={{ height: horizontal ? Math.max(170, c.data.length * 36 + 24) : 270 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={c.data} layout={horizontal ? 'vertical' : 'horizontal'}
            margin={{ top: 18, right: horizontal ? 56 : 12, left: 4, bottom: !horizontal && many ? 44 : 0 }} barCategoryGap={horizontal ? 8 : '24%'}>
            <defs>
              {(stacked ? c.series : [c.series[0], ...c.series.slice(1)]).map((s, i) => (
                <linearGradient key={s.key} id={`bar-${id}-${i}`} x1="0" y1={horizontal ? '0' : '1'} x2={horizontal ? '1' : '0'} y2="0">
                  <stop offset="0%" stopColor={DEEP[i % DEEP.length]} />
                  <stop offset="100%" stopColor={PALETTE[i % PALETTE.length]} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid stroke={grid} horizontal={!horizontal} vertical={horizontal} />
            {horizontal ? (
              <>
                <XAxis type="number" tick={tick} tickLine={false} axisLine={false} tickFormatter={(v) => format(v, unit)} />
                <YAxis type="category" dataKey="label" tick={{ ...tick, fill: 'rgba(226,232,240,0.8)' }} width={150} tickLine={false} axisLine={false}
                  tickFormatter={(v: string) => (v.length > 22 ? `${v.slice(0, 21)}…` : v)} />
              </>
            ) : (
              <>
                <XAxis dataKey="label" tick={tick} tickLine={false} axisLine={false} interval={0} angle={many ? -30 : 0}
                  textAnchor={many ? 'end' : 'middle'} tickFormatter={(v: string) => (v.length > 18 ? `${v.slice(0, 17)}…` : v)} />
                <YAxis tick={tick} width={60} tickLine={false} axisLine={false} tickFormatter={(v) => format(v, unit)} />
              </>
            )}
            <Tooltip content={<TooltipBox unit={unit} />} cursor={{ fill: 'rgba(255,255,255,0.05)' }} />
            {c.series.map((s, i) => (
              <Bar key={s.key} dataKey={s.key} name={s.label} stackId={stacked ? 'stack' : undefined} fill={`url(#bar-${id}-${i})`}
                radius={stacked ? (i === c.series.length - 1 ? (horizontal ? [0, 5, 5, 0] : [5, 5, 0, 0]) : 0) : horizontal ? [0, 7, 7, 0] : [7, 7, 0, 0]}
                maxBarSize={horizontal ? 22 : 54} animationDuration={1300} animationBegin={delay + i * 180} animationEasing="ease-out">
                {single &&
                  c.data.map((_, j) => <Cell key={j} fillOpacity={j === top ? 1 : 0.55} />)}
                {single && c.data.length <= 12 && (
                  <LabelList dataKey={s.key} position={horizontal ? 'right' : 'top'} formatter={(v: unknown) => format(v, unit)}
                    style={{ fontSize: 11, fill: 'rgba(226,232,240,0.85)', fontWeight: 500 }} />
                )}
              </Bar>
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      {!single && (
        <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-slate-300">
          {c.series.map((s, i) => (
            <span key={s.key} className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full" style={{ background: PALETTE[i % PALETTE.length] }} /> {s.label}
            </span>
          ))}
        </div>
      )}
    </Stage>
  )
}

function Donut({ c, delay }: { c: ChartSpec; delay: number }) {
  const key = c.series[0].key
  const unit = c.unit ?? 'number'
  const total = c.data.reduce((t, d) => t + toNum(d[key]), 0)
  const [active, setActive] = useState<number | undefined>(undefined)
  const focus = active !== undefined ? c.data[active] : undefined
  return (
    <Stage title={c.title} note={c.note} delay={delay}>
      <div className="flex flex-col items-center gap-5">
        <div className="relative h-48 w-48 shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={c.data} dataKey={key} nameKey="label" innerRadius="64%" outerRadius="92%" paddingAngle={2.5} stroke="none"
                startAngle={90} endAngle={-270} cornerRadius={4} animationDuration={1500} animationBegin={delay}
                onMouseEnter={(_, i) => setActive(i)} onMouseLeave={() => setActive(undefined)}>
                {c.data.map((_, i) => (
                  <Cell key={i} fill={PALETTE[i % PALETTE.length]} fillOpacity={active === undefined || active === i ? 1 : 0.3}
                    style={{ transition: 'fill-opacity 200ms' }} />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
            {focus ? (
              <>
                <div className="font-serif text-[22px] tabular-nums">{format(focus[key], unit)}</div>
                <div className="max-w-[110px] truncate text-[10px] uppercase tracking-wider text-slate-400">{String(focus.label)}</div>
              </>
            ) : (
              <>
                <CountUp value={total} unit={unit} className="font-serif text-[22px] tabular-nums" />
                <div className="text-[10px] uppercase tracking-wider text-slate-400">total</div>
              </>
            )}
          </div>
        </div>
        <div className="w-full space-y-2 text-[12px]">
          {c.data.map((d, i) => {
            const share = total ? (toNum(d[key]) / total) * 100 : 0
            return (
              <div key={i} onMouseEnter={() => setActive(i)} onMouseLeave={() => setActive(undefined)}
                className={`rounded-lg px-2 py-1 transition ${active === i ? 'bg-white/5' : ''}`}>
                <div className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: PALETTE[i % PALETTE.length] }} />
                  <span className="truncate text-slate-200">{String(d.label)}</span>
                  <span className="ml-auto pl-2 tabular-nums text-slate-400">{share.toFixed(share < 10 ? 1 : 0)}%</span>
                  <span className="w-20 text-right font-semibold tabular-nums">{format(d[key], unit)}</span>
                </div>
                <div className="mt-1 h-1 overflow-hidden rounded-full bg-white/5">
                  <div className="h-full rounded-full transition-[width] duration-1000 ease-out" style={{ width: `${share}%`, background: PALETTE[i % PALETTE.length] }} />
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </Stage>
  )
}

export function Charts({ charts }: { charts: ChartSpec[] }) {
  const kpis = charts.filter((c) => c.type === 'kpi' && c.data.length && c.series.length)
  const plots = charts.filter((c) => c.type !== 'kpi' && c.data.length && c.series.length)
  if (!kpis.length && !plots.length) return null
  const pair = plots.length === 2 && plots.some((c) => c.type === 'donut')
  return (
    <div className="space-y-3">
      {kpis.map((c, i) => (
        <Kpis key={`k${i}`} c={c} delay={i * 120} />
      ))}
      {plots.length > 0 && (
        <div className={`grid gap-3 ${pair ? 'md:grid-cols-2' : ''}`}>
          {plots.map((c, i) => {
            const delay = 150 + i * 200
            return c.type === 'donut' ? <Donut key={i} c={c} delay={delay} /> : c.type === 'line' ? <Trend key={i} c={c} delay={delay} /> : <Bars key={i} c={c} delay={delay} />
          })}
        </div>
      )}
    </div>
  )
}
