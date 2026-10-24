// Draws the chart plans the backend attaches to an answer. Values come from the verified result rows.
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
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
}

const PALETTE = ['#0b5cab', '#14b8a6', '#f59e0b', '#8b5cf6', '#ef4444', '#10b981', '#ec4899', '#64748b']
const tick = { fontSize: 11, fill: '#6b6a66' }

const toNum = (v: unknown) => (typeof v === 'number' ? v : Number(v ?? 0)) || 0

function format(v: unknown, unit: string = 'number', compact = true): string {
  const n = toNum(v)
  if (unit === 'percent') return `${(Math.abs(n) <= 1 ? n * 100 : n).toFixed(1)}%`
  const opts: Intl.NumberFormatOptions = compact && Math.abs(n) >= 10_000 ? { notation: 'compact', maximumFractionDigits: 1 } : { maximumFractionDigits: unit === 'count' ? 0 : 2 }
  const s = n.toLocaleString('en-US', opts)
  return unit === 'usd' ? `${n < 0 ? '−' : ''}$${s.replace('-', '')}` : s
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-line bg-white p-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
      <div className="mb-3 text-[13px] font-medium text-ink">{title}</div>
      {children}
    </div>
  )
}

function TooltipBox({ active, payload, label, unit }: { active?: boolean; payload?: { name?: string; value?: unknown; color?: string }[]; label?: unknown; unit: string }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-line bg-white/95 px-3 py-2 text-[12px] shadow-lg backdrop-blur">
      {label !== undefined && <div className="mb-1 font-medium text-ink">{String(label)}</div>}
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2 text-stone-600">
          <span className="h-2 w-2 rounded-full" style={{ background: p.color }} />
          <span>{p.name}</span>
          <span className="ml-auto pl-3 font-medium tabular-nums text-ink">{format(p.value, unit, false)}</span>
        </div>
      ))}
    </div>
  )
}

function Kpis({ c }: { c: ChartSpec }) {
  const row = c.data[0] ?? {}
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.min(c.series.length, 4)}, minmax(0, 1fr))` }}>
      {c.series.map((s, i) => (
        <div key={s.key} className="relative overflow-hidden rounded-2xl border border-line bg-white px-4 py-3.5">
          <div className="absolute inset-x-0 top-0 h-1" style={{ background: PALETTE[i % PALETTE.length] }} />
          <div className="text-[11px] font-medium uppercase tracking-wider text-muted">{s.label}</div>
          <div className="mt-1 font-serif text-[28px] leading-tight tracking-tight tabular-nums">
            {format(row[s.key], c.units?.[s.key] ?? c.unit)}
          </div>
          {i === 0 && c.series.length === 1 && <div className="text-[11px] text-stone-400">{c.title}</div>}
        </div>
      ))}
    </div>
  )
}

function Donut({ c }: { c: ChartSpec }) {
  const key = c.series[0].key
  const unit = c.unit ?? 'number'
  const total = c.data.reduce((t, d) => t + toNum(d[key]), 0)
  return (
    <Card title={c.title}>
      <div className="flex flex-col items-center gap-4">
        <div className="relative h-44 w-44 shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={c.data} dataKey={key} nameKey="label" innerRadius="62%" outerRadius="95%" paddingAngle={2} stroke="none">
                {c.data.map((_, i) => (
                  <Cell key={i} fill={PALETTE[i % PALETTE.length]} />
                ))}
              </Pie>
              <Tooltip content={<TooltipBox unit={unit} />} />
            </PieChart>
          </ResponsiveContainer>
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <div className="font-serif text-[20px] tabular-nums">{format(total, unit)}</div>
            <div className="text-[10px] uppercase tracking-wider text-muted">total</div>
          </div>
        </div>
        <div className="w-full space-y-1.5 text-[12px]">
          {c.data.map((d, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: PALETTE[i % PALETTE.length] }} />
              <span className="truncate text-stone-600">{String(d.label)}</span>
              <span className="ml-auto pl-2 tabular-nums text-stone-400">{total ? `${Math.round((toNum(d[key]) / total) * 100)}%` : ''}</span>
              <span className="w-20 text-right font-medium tabular-nums">{format(d[key], unit)}</span>
            </div>
          ))}
        </div>
      </div>
    </Card>
  )
}

function Trend({ c }: { c: ChartSpec }) {
  const unit = c.unit ?? 'number'
  return (
    <Card title={c.title}>
      <div className="h-60">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={c.data} margin={{ top: 8, right: 8, left: 4, bottom: 0 }}>
            <defs>
              {c.series.map((s, i) => (
                <linearGradient key={s.key} id={`fill-${i}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={PALETTE[i % PALETTE.length]} stopOpacity={0.28} />
                  <stop offset="100%" stopColor={PALETTE[i % PALETTE.length]} stopOpacity={0} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#eceae5" vertical={false} />
            <XAxis dataKey="label" tick={tick} tickLine={false} axisLine={{ stroke: '#e5e3de' }} minTickGap={16} />
            <YAxis tick={tick} width={64} tickLine={false} axisLine={false} tickFormatter={(v) => format(v, unit)} />
            <Tooltip content={<TooltipBox unit={unit} />} />
            {c.series.length > 1 && <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />}
            {c.series.map((s, i) => (
              <Area key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={PALETTE[i % PALETTE.length]} strokeWidth={2.25}
                fill={`url(#fill-${i})`} dot={c.data.length <= 24 ? { r: 3, strokeWidth: 0, fill: PALETTE[i % PALETTE.length] } : false}
                activeDot={{ r: 5 }} />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Card>
  )
}

function Bars({ c }: { c: ChartSpec }) {
  const unit = c.unit ?? 'number'
  const stacked = c.type === 'stacked_bar'
  const horizontal = c.layout === 'horizontal'
  const single = c.series.length === 1 && !stacked
  const many = c.data.length > 6
  return (
    <Card title={c.title}>
      <div style={{ height: horizontal ? Math.max(160, c.data.length * 34 + 20) : 256 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={c.data} layout={horizontal ? 'vertical' : 'horizontal'}
            margin={{ top: 8, right: 12, left: 4, bottom: !horizontal && many ? 44 : 0 }} barCategoryGap={horizontal ? 8 : '22%'}>
            <defs>
              {c.series.map((s, i) => (
                <linearGradient key={s.key} id={`bar-${i}`} x1="0" y1="0" x2={horizontal ? '1' : '0'} y2={horizontal ? '0' : '1'}>
                  <stop offset="0%" stopColor={PALETTE[i % PALETTE.length]} stopOpacity={horizontal ? 0.75 : 1} />
                  <stop offset="100%" stopColor={PALETTE[i % PALETTE.length]} stopOpacity={horizontal ? 1 : 0.7} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#eceae5" horizontal={!horizontal} vertical={horizontal} />
            {horizontal ? (
              <>
                <XAxis type="number" tick={tick} tickLine={false} axisLine={false} tickFormatter={(v) => format(v, unit)} />
                <YAxis type="category" dataKey="label" tick={tick} width={140} tickLine={false} axisLine={false}
                  tickFormatter={(v: string) => (v.length > 22 ? `${v.slice(0, 21)}…` : v)} />
              </>
            ) : (
              <>
                <XAxis dataKey="label" tick={tick} tickLine={false} axisLine={{ stroke: '#e5e3de' }} interval={0}
                  angle={many ? -30 : 0} textAnchor={many ? 'end' : 'middle'}
                  tickFormatter={(v: string) => (v.length > 18 ? `${v.slice(0, 17)}…` : v)} />
                <YAxis tick={tick} width={64} tickLine={false} axisLine={false} tickFormatter={(v) => format(v, unit)} />
              </>
            )}
            <Tooltip content={<TooltipBox unit={unit} />} cursor={{ fill: '#eef3fa' }} />
            {!single && <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12, paddingTop: 4 }} />}
            {c.series.map((s, i) => (
              <Bar key={s.key} dataKey={s.key} name={s.label} stackId={stacked ? 'stack' : undefined} fill={`url(#bar-${i})`}
                radius={stacked ? (i === c.series.length - 1 ? (horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]) : 0) : horizontal ? [0, 6, 6, 0] : [6, 6, 0, 0]}
                maxBarSize={horizontal ? 22 : 52} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Card>
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
        <Kpis key={`k${i}`} c={c} />
      ))}
      {plots.length > 0 && (
        <div className={`grid gap-3 ${pair ? 'md:grid-cols-2' : ''}`}>
          {plots.map((c, i) =>
            c.type === 'donut' ? (
              <Donut key={i} c={c} />
            ) : c.type === 'line' ? (
              <Trend key={i} c={c} />
            ) : (
              <Bars key={i} c={c} />
            ),
          )}
        </div>
      )}
    </div>
  )
}
