import { useState } from 'react'
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { forecast, formatL } from '../data'

export function ForecastChart({ revisedReady }: { revisedReady: boolean }) {
  const [show, setShow] = useState<'both' | 'baseline'>('both')
  const showRevised = revisedReady && show === 'both'

  return (
    <div className="rounded-2xl border border-line bg-white p-4">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <div className="text-[13px] font-medium">30-day cash position</div>
          <div className="text-[11px] text-muted">Confirmed cash + expected inflows − scheduled outflows · daily opex ₹1L</div>
        </div>
        <div className="flex rounded-lg bg-stone-100 p-0.5 text-[11px]">
          {(['both', 'baseline'] as const).map((k) => (
            <button
              key={k}
              onClick={() => setShow(k)}
              className={`rounded-md px-2 py-1 ${show === k ? 'bg-white font-medium shadow-sm' : 'text-stone-500'}`}
            >
              {k === 'both' ? 'With agent plan' : 'Baseline only'}
            </button>
          ))}
        </div>
      </div>
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={forecast} margin={{ top: 10, right: 8, left: -14, bottom: 0 }}>
            <defs>
              <linearGradient id="rev" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#0b5cab" stopOpacity={0.22} />
                <stop offset="100%" stopColor="#0b5cab" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="base" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#a8a29e" stopOpacity={0.15} />
                <stop offset="100%" stopColor="#a8a29e" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="#f0eee9" />
            <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#a8a29e' }} tickLine={false} axisLine={false} interval={4} />
            <YAxis
              domain={[20, 110]}
              ticks={[30, 50, 70, 90, 110]}
              tick={{ fontSize: 10, fill: '#a8a29e' }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v) => `₹${v}L`}
            />
            <Tooltip
              contentStyle={{ borderRadius: 10, border: '1px solid #e8e6e1', fontSize: 12, boxShadow: '0 4px 16px rgba(0,0,0,0.06)' }}
              formatter={(v, name) => [formatL(Number(v)), name === 'revised' ? 'With agent plan' : 'Baseline']}
            />
            <ReferenceLine y={50} stroke="#d97706" strokeDasharray="4 4" label={{ value: 'Target ₹50L', position: 'insideTopRight', fontSize: 10, fill: '#b45309' }} />
            <ReferenceLine y={30} stroke="#e11d48" strokeDasharray="4 4" label={{ value: 'Hard minimum ₹30L', position: 'insideBottomRight', fontSize: 10, fill: '#be123c' }} />
            <Area type="stepAfter" dataKey="baseline" stroke="#a8a29e" strokeWidth={1.5} strokeDasharray={showRevised ? '4 3' : undefined} fill="url(#base)" />
            {showRevised && <Area type="stepAfter" dataKey="revised" stroke="#0b5cab" strokeWidth={2} fill="url(#rev)" />}
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2 flex items-center gap-4 text-[11px] text-muted">
        <span className="flex items-center gap-1.5">
          <span className="h-0.5 w-4 bg-stone-400" /> Baseline
        </span>
        {revisedReady && (
          <span className="flex items-center gap-1.5">
            <span className="h-0.5 w-4 bg-brand" /> With agent plan (if approvals granted)
          </span>
        )}
        <span className="ml-auto">Forecast, not a guarantee</span>
      </div>
    </div>
  )
}
