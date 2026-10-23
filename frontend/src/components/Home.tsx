import { ArrowRight, Sparkles } from 'lucide-react'
import { awaySummary, suggestions } from '../data'
import { Composer } from './Composer'

interface Props {
  onSubmit: (q: string) => void
  pendingCount: number
  paused: boolean
  handledLive: number
  onOpenAutopilot: () => void
  handledTotal?: number
  examples?: string[]
  sourceCount?: number
  onAttach?: (files: File[]) => void
}

export function Home({ onSubmit, pendingCount, paused, handledLive, onOpenAutopilot, handledTotal, examples = suggestions, sourceCount, onAttach }: Props) {
  const hour = new Date().getHours()
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'

  return (
    <div className="flex h-full flex-col items-center overflow-y-auto px-6 scrollbar-thin">
      <div className="flex w-full max-w-[680px] flex-1 flex-col justify-center py-16">
        <h1 className="text-center font-serif text-[44px] leading-none tracking-tight text-ink">
          {greeting}, <em className="text-brand">Meera</em>
        </h1>
        <button
          onClick={onOpenAutopilot}
          className="group mx-auto mb-8 mt-3 flex items-center gap-2 rounded-full px-3 py-1 text-[14px] text-muted transition hover:bg-white hover:text-ink"
        >
          <span className={`h-1.5 w-1.5 rounded-full ${paused ? 'bg-rose-500' : 'animate-pulse bg-emerald-500'}`} />
          {paused
            ? 'Autopilot paused — the agent is only watching and recommending'
            : `Autopilot is on · handled ${handledTotal ?? awaySummary.handled + handledLive} things on its own · ${pendingCount} need you`}
          <ArrowRight size={13} className="opacity-0 transition group-hover:opacity-100" />
        </button>

        <Composer onSubmit={onSubmit} placeholder="Give the agent a new goal, or ask about anything it did…" sourceCount={sourceCount} onAttach={onAttach} />
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          {examples.map((s) => (
            <button
              key={s}
              onClick={() => onSubmit(s)}
              className="flex items-center gap-1.5 rounded-full border border-line bg-white/60 px-3 py-1.5 text-[13px] text-stone-600 transition hover:border-stone-300 hover:bg-white hover:text-ink"
            >
              <Sparkles size={12} className="text-brand" />
              {s}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
