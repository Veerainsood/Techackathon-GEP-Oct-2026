import { ArrowUp, ChevronDown, Database, Paperclip, Target } from 'lucide-react'
import { useRef, useState } from 'react'

export const modes = [
  { id: 'L1', name: 'Analyze', desc: 'Read data, surface issues' },
  { id: 'L2', name: 'Recommend', desc: 'Propose actions for you to decide' },
  { id: 'L3', name: 'Execute', desc: 'Act within configured limits' },
  { id: 'L4', name: 'Orchestrate', desc: 'Pursue a goal across agents' },
] as const

interface Props {
  onSubmit: (q: string) => void
  placeholder?: string
  compact?: boolean
  initial?: string
  sourceCount?: number
  onAttach?: (files: File[]) => void
  attachments?: string[]
  onRemoveAttachment?: () => void
}

export function Composer({ onSubmit, placeholder = 'Set a financial goal or ask anything…', compact, initial = '', sourceCount = 6, onAttach, attachments = [], onRemoveAttachment }: Props) {
  const [value, setValue] = useState(initial)
  const fileRef = useRef<HTMLInputElement>(null)
  const [mode, setMode] = useState<(typeof modes)[number]>(modes[3])
  const [open, setOpen] = useState(false)

  const submit = () => {
    if (!value.trim()) return
    onSubmit(value.trim())
    setValue('')
  }

  return (
    <div className="relative rounded-2xl border border-line bg-white shadow-[0_1px_2px_rgba(0,0,0,0.04),0_4px_16px_rgba(0,0,0,0.04)] transition focus-within:border-stone-300 focus-within:shadow-[0_1px_2px_rgba(0,0,0,0.04),0_8px_24px_rgba(0,0,0,0.06)]">
      {attachments.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 px-4 pt-3">
          {(attachments.length > 3 ? attachments.slice(0, 2) : attachments).map((name) => (
            <span key={name} className="flex items-center gap-1 rounded-md border border-line bg-stone-50 px-1.5 py-0.5 text-[11px] text-stone-600">
              <Paperclip size={11} />
              {name}
            </span>
          ))}
          {attachments.length > 3 && (
            <span title={attachments.slice(2).join('\n')} className="rounded-md border border-line bg-stone-50 px-1.5 py-0.5 text-[11px] text-stone-600">
              +{attachments.length - 2} more
            </span>
          )}
          {onRemoveAttachment && (
            <button onClick={onRemoveAttachment} title="Detach files" className="rounded px-1 text-[11px] text-stone-400 hover:text-ink">
              ×
            </button>
          )}
        </div>
      )}
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            submit()
          }
        }}
        rows={compact ? 1 : 2}
        placeholder={placeholder}
        className="w-full resize-none bg-transparent px-4 pt-3.5 text-[15px] text-ink outline-none placeholder:text-stone-400"
      />
      <div className="flex items-center justify-between px-2.5 pb-2.5">
        <div className="flex items-center gap-1">
          <div className="relative">
            <button
              onClick={() => setOpen((o) => !o)}
              className="flex items-center gap-1.5 rounded-lg bg-brand-soft px-2.5 py-1.5 text-xs font-medium text-brand transition hover:bg-brand/15"
            >
              <Target size={13} />
              {mode.name}
              <span className="rounded bg-white/70 px-1 text-[10px]">{mode.id}</span>
              <ChevronDown size={12} />
            </button>
            {open && (
              <div className="absolute bottom-full left-0 z-20 mb-2 w-64 rounded-xl border border-line bg-white p-1.5 shadow-lg fade-up">
                <div className="px-2 py-1 text-[11px] font-medium uppercase tracking-wider text-stone-400">Autonomy level</div>
                {modes.map((m) => (
                  <button
                    key={m.id}
                    onClick={() => {
                      setMode(m)
                      setOpen(false)
                    }}
                    className={`flex w-full items-start gap-2.5 rounded-lg px-2 py-2 text-left hover:bg-stone-50 ${
                      m.id === mode.id ? 'bg-stone-50' : ''
                    }`}
                  >
                    <span className="mt-0.5 rounded bg-stone-100 px-1 text-[10px] font-semibold text-stone-600">{m.id}</span>
                    <span>
                      <span className="block text-[13px] font-medium">{m.name}</span>
                      <span className="block text-[11px] text-muted">{m.desc}</span>
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <button className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-stone-600 hover:bg-stone-100">
            <Database size={13} /> {sourceCount} sources
          </button>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={() => fileRef.current?.click()} className="rounded-lg p-2 text-stone-500 hover:bg-stone-100">
            <Paperclip size={15} />
          </button>
          {onAttach && (
            <input
              ref={fileRef}
              type="file"
              multiple
              hidden
              accept=".csv,.tsv,.xlsx,.xls,.json,.jsonl,.parquet"
              onChange={(e) => {
                const files = Array.from(e.target.files ?? [])
                if (files.length) onAttach(files)
                e.target.value = ''
              }}
            />
          )}
          <button
            onClick={submit}
            disabled={!value.trim()}
            className="flex h-8 w-8 items-center justify-center rounded-full bg-brand text-white transition hover:bg-brand/90 disabled:bg-stone-200 disabled:text-stone-400"
          >
            <ArrowUp size={16} />
          </button>
        </div>
      </div>
    </div>
  )
}
