import { ArrowUp, Database, FileUp, Loader2, Paperclip, Sparkles, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { ago, api, type AskRequest, type RuntimeSource, type StoredFile, type Workspace } from './api'

const EXAMPLES = [
  'What is the total open balance in USD by settlement status?',
  'Which are the top 5 conglomerates by total invoice subtotal in USD, excluding VOID invoices?',
  'How many invoices have settlement status OVERDUE?',
]

export function LiveHome({ onAsk }: { onAsk: (r: AskRequest) => void }) {
  const [q, setQ] = useState('')
  const [sources, setSources] = useState<RuntimeSource[] | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [files, setFiles] = useState<StoredFile[]>([])
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.sources().then(setSources).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  const attach = async (list: FileList | null) => {
    if (!list?.length) return
    setError(null)
    try {
      setBusy(`Uploading ${list.length} file(s)…`)
      const uploaded = await api.upload(Array.from(list))
      const all = [...files, ...uploaded]
      setFiles(all)
      setBusy('Profiling uploaded files in a private workspace…')
      setWorkspace(await api.createWorkspace(all.map((f) => f.file_id)))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const submit = (question: string) => {
    if (!question.trim() || busy) return
    onAsk({ question: question.trim(), workspace_id: workspace?.workspace_id ?? null, sources: !workspace && selected.length ? selected : null })
  }

  const failing = sources?.filter((s) => !['ok', 'no events yet'].includes(s.sync_status)) ?? []

  return (
    <div className="flex h-full flex-col items-center overflow-y-auto px-6 scrollbar-thin">
      <div className="flex w-full max-w-[720px] flex-1 flex-col justify-center py-14">
        <h1 className="text-center font-serif text-[40px] leading-none tracking-tight">Ask your data</h1>
        <p className="mb-7 mt-3 text-center text-[14px] text-muted">
          Explorer and Hercules investigate independently on pinned source versions; answers are verified against executed evidence.
        </p>

        <div className="rounded-2xl border border-line bg-white shadow-[0_1px_2px_rgba(0,0,0,0.04),0_4px_16px_rgba(0,0,0,0.04)]">
          <textarea
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                submit(q)
              }
            }}
            rows={2}
            placeholder={workspace ? 'Ask about the uploaded files…' : 'Ask a question about your sources…'}
            className="w-full resize-none bg-transparent px-4 pt-3.5 text-[15px] outline-none placeholder:text-stone-400"
          />
          {files.length > 0 && (
            <div className="flex flex-wrap gap-1.5 px-3">
              {files.map((f) => (
                <span key={f.file_id} className="flex items-center gap-1 rounded-md border border-line bg-stone-50 px-1.5 py-0.5 text-[11px]">
                  <FileUp size={11} /> {f.filename} · {(f.size / 1024).toFixed(1)} KB · sha256 {f.sha256.slice(0, 8)}
                </span>
              ))}
              <button onClick={() => { setFiles([]); setWorkspace(null) }} className="flex items-center gap-0.5 text-[11px] text-muted hover:text-ink"><X size={11} /> clear</button>
            </div>
          )}
          <div className="flex items-center justify-between px-2.5 pb-2.5 pt-1">
            <div className="flex items-center gap-1 text-xs text-stone-600">
              <input ref={input} type="file" multiple accept=".csv,.tsv,.parquet,.json,.jsonl,.ndjson,.xlsx,.xls" className="hidden" onChange={(e) => attach(e.target.files)} />
              <button onClick={() => input.current?.click()} className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 hover:bg-stone-100"><Paperclip size={13} /> Upload files</button>
              {busy && <span className="flex items-center gap-1 text-muted"><Loader2 size={12} className="animate-spin" /> {busy}</span>}
              {workspace && !busy && <span className="text-emerald-700">Workspace ready · {workspace.collections.join(', ')}</span>}
            </div>
            <button onClick={() => submit(q)} disabled={!q.trim() || !!busy} className="flex h-8 w-8 items-center justify-center rounded-full bg-brand text-white disabled:bg-stone-200 disabled:text-stone-400">
              <ArrowUp size={16} />
            </button>
          </div>
        </div>

        {error && <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-800">{error}</div>}

        {!workspace && sources && (
          <div className="mt-4">
            <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-stone-400">Sources {selected.length ? `· restricted to ${selected.length}` : '· all authorized'}</div>
            <div className="flex flex-wrap gap-1.5">
              {sources.map((s) => {
                const on = selected.includes(s.source_id)
                return (
                  <button key={s.source_id} onClick={() => setSelected((p) => (on ? p.filter((x) => x !== s.source_id) : [...p, s.source_id]))}
                    className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] ${on ? 'border-brand bg-brand-soft text-brand' : 'border-line bg-white/70 text-stone-600'}`}>
                    <Database size={11} /> {s.source_id}
                    <span className="text-[10px] text-stone-400">{s.data_version ?? s.mode} · {s.last_sync_at ? ago(s.last_sync_at) : s.snapshot_synced_at ? ago(s.snapshot_synced_at) : s.mode}</span>
                  </button>
                )
              })}
            </div>
            {failing.length > 0 && <div className="mt-2 text-[12px] text-rose-700">Sync problems: {failing.map((s) => `${s.source_id} (${s.sync_status})`).join(', ')}</div>}
          </div>
        )}

        <div className="mt-5 flex flex-wrap justify-center gap-2">
          {EXAMPLES.map((s) => (
            <button key={s} onClick={() => submit(s)} className="flex items-center gap-1.5 rounded-full border border-line bg-white/60 px-3 py-1.5 text-[13px] text-stone-600 hover:border-stone-300 hover:bg-white">
              <Sparkles size={12} className="text-brand" /> {s}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
