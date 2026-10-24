import { CheckCircle2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { ActionDrawer } from './components/ActionDrawer'
import { AutopilotPage, type FeedItem } from './components/Autopilot'
import { Home } from './components/Home'
import { ActivityPage, ApprovalsPage, SettingsPage, SourcesPage } from './components/Pages'
import { Sidebar, type View } from './components/Sidebar'
import { Thread } from './components/Thread'
import { initialActions, liveEvents, overnightRuns, type Action } from './data'
import { NotificationButton, NotificationsPanel } from './components/NotificationsPanel'
import {
  LIVE,
  leakageKpis,
  toAction,
  toActivity,
  toFeed,
  toGoals,
  toLeakageFeed,
  toLeakageGoals,
  toSource,
  toTimeline,
  useLiveData,
} from './live/adapter'
import { api, type AskRequest, type ConversationRow, type PrepFix, type PrepJob } from './live/api'
import { LiveThread, type Turn } from './live/LiveThread'
import type { PrepState } from './live/PrepCard'

// Questions the connected finance warehouse can answer (live mode Home / Related suggestions).
const LIVE_EXAMPLES = [
  'How many invoices have settlement status OVERDUE?',
  'What is the total open balance in USD by settlement status?',
  'Which are the top 5 conglomerates by total invoice subtotal in USD, excluding VOID invoices?',
  'What is the total gross invoice amount in USD for invoices billed in EUR currency?',
]

export default function App() {
  const [view, setView] = useState<View>('home')
  const [query, setQuery] = useState('')
  const [actions, setActions] = useState<Action[]>(LIVE ? [] : initialActions)
  const [openId, setOpenId] = useState<string | null>(null)
  const [paused, setPaused] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  const [demoFeed, setFeed] = useState<FeedItem[]>([])
  const [notifOpen, setNotifOpen] = useState(false)
  const [awaySeen, setAwaySeen] = useState(false)
  const [seenFeed, setSeenFeed] = useState(0)

  const live = useLiveData(LIVE)
  const [request, setRequest] = useState<AskRequest | null>(null)
  const [workspace, setWorkspace] = useState<string | null>(null)
  const [attachments, setAttachments] = useState<string[]>([])
  const [prep, setPrep] = useState<PrepState | null>(null)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [turns, setTurns] = useState<Turn[]>([]) // earlier turns of the open conversation
  const [lastDone, setLastDone] = useState<Turn | null>(null) // the current question, once answered
  const [conversations, setConversations] = useState<ConversationRow[]>([])
  const [runningId, setRunningId] = useState<string | null>(null) // chat streaming in this view ('new' before it has an id)
  const [backgroundId, setBackgroundId] = useState<string | null>(null) // chat left while still answering
  const liveSources = useMemo(() => live.sources.map(toSource), [live.sources])
  const liveFeed = useMemo(() => toFeed(live.events, live.investigations), [live.events, live.investigations])
  const feed = LIVE ? liveFeed : demoFeed

  useEffect(() => {
    if (notifOpen) setSeenFeed(feed.length)
  }, [notifOpen, feed.length])

  useEffect(() => {
    if (LIVE || paused || demoFeed.length >= liveEvents.length) return
    const t = setTimeout(
      () => {
        const ev = liveEvents[demoFeed.length]
        setFeed((f) => [{ ...ev, at: new Date().toTimeString().slice(0, 5) }, ...f])
        if (ev.action) {
          setActions((prev) => [ev.action!, ...prev])
          setToast(`Agent escalated: ${ev.title}`)
        }
      },
      demoFeed.length === 0 ? 2500 : 5000,
    )
    return () => clearTimeout(t)
  }, [demoFeed.length, paused])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 2800)
    return () => clearTimeout(t)
  }, [toast])

  useEffect(() => {
    if (LIVE && live.backend === 'down') setToast('Backend unreachable — start it with: uvicorn backend.api.app:app --port 8000')
  }, [live.backend])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setView('home')
      }
      if (e.key === 'Escape') setOpenId(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Live approval queue: the backend is the source of truth; poll so new items and decisions show up.
  useEffect(() => {
    if (!LIVE) return
    let alive = true
    const load = () =>
      api
        .approvals('all')
        .then((rows) => alive && setActions(rows.map(toAction)))
        .catch(() => undefined) // backend-down is already reported by useLiveData
    load()
    const t = setInterval(load, 5000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

  useEffect(() => {
    if (!LIVE) return
    api.conversations().then(setConversations).catch(() => undefined)
  }, [lastDone, conversationId, runningId, backgroundId])

  const turnsFrom = (messages: { role: string; content: string; meta: Record<string, unknown> }[]): Turn[] => {
    const pairs: Turn[] = []
    for (let i = 0; i < messages.length; i++) {
      if (messages[i].role !== 'user') continue
      const reply = messages[i + 1]?.role === 'assistant' ? messages[i + 1] : null
      const meta = (reply?.meta ?? {}) as Record<string, unknown>
      const route = meta.route as string | undefined
      pairs.push({
        question: messages[i].content,
        answer: reply?.content ?? '',
        data: (meta.data as Turn['data']) ?? [],
        detail: (meta.detail as Turn['detail']) ?? null,
        verified: route === 'investigate' ? meta.verification_status === 'verified_fact' : undefined,
        totalMs: route === 'investigate' ? (meta.total_ms as number | undefined) : undefined,
        route,
      })
    }
    return pairs
  }

  // A chat left while it was still answering keeps answering on the server; pick up the reply when it is stored.
  useEffect(() => {
    if (!LIVE || !backgroundId) return
    const t = setInterval(async () => {
      try {
        const { messages } = await api.conversation(backgroundId)
        if (messages.length && messages[messages.length - 1].role === 'assistant') {
          if (conversationId === backgroundId && !request) setTurns(turnsFrom(messages))
          setBackgroundId(null)
        }
      } catch {
        setBackgroundId(null)
      }
    }, 4000)
    return () => clearInterval(t)
  }, [backgroundId, conversationId, request])

  // Follow-up in the open conversation.
  const ask = (q: string) => {
    setQuery(q)
    if (LIVE) {
      if (lastDone) setTurns((t) => [...t, lastDone])
      setLastDone(null)
      setRequest({ question: q, workspace_id: workspace, conversation_id: conversationId })
    }
    setView('thread')
  }

  // A question from Home starts a new conversation (attachments made on Home come along).
  const askNew = (q: string) => {
    setQuery(q)
    if (LIVE) {
      leaveRunningChat()
      setTurns([])
      setLastDone(null)
      setConversationId(null)
      setRequest({ question: q, workspace_id: workspace, conversation_id: null })
    }
    setView('thread')
  }

  const leaveRunningChat = () => {
    if (runningId && runningId !== 'new') setBackgroundId(runningId)
    setRunningId(null)
  }

  const newConversation = () => {
    leaveRunningChat()
    setConversationId(null)
    setTurns([])
    setLastDone(null)
    setRequest(null)
    setWorkspace(null)
    setAttachments([])
    setPrep(null)
    setView('home')
  }

  const openConversation = async (index: number) => {
    const row = conversations[index]
    if (!row) return
    if (row.conversation_id === conversationId && request) return // already open and answering
    try {
      const { conversation, messages } = await api.conversation(row.conversation_id)
      if (row.conversation_id !== runningId) leaveRunningChat()
      setConversationId(row.conversation_id)
      setTurns(turnsFrom(messages))
      setLastDone(null)
      setRequest(null)
      setWorkspace(conversation.workspace_id ?? null)
      setAttachments((conversation.files ?? []).map((f) => f.filename))
      setPrep(null)
      setView('thread')
    } catch (err) {
      setToast(`Could not open conversation: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const deleteConversation = async (index: number) => {
    const row = conversations[index]
    if (!row) return
    try {
      await api.deleteConversation(row.conversation_id)
      setConversations((list) => list.filter((c) => c.conversation_id !== row.conversation_id))
      if (row.conversation_id === conversationId) newConversation()
      if (row.conversation_id === backgroundId) setBackgroundId(null)
    } catch (err) {
      setToast(`Could not delete: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  // Attached files are checked by the data-prep agent; the result (and any decision it needs) shows in the chat.
  const attach = async (files: File[]) => {
    const names = files.map((f) => f.name)
    setPrep({ busy: true, names, jobs: [] })
    setView('thread')
    try {
      const stored = await api.upload(files)
      const jobs = await api.prepFiles(stored.map((f) => f.file_id))
      const ws = await api.createWorkspace(jobs.map((j) => j.checked_file_id ?? j.input_file_id))
      setWorkspace(ws.workspace_id)
      setAttachments(ws.files.map((f) => f.filename))
      setPrep({ busy: false, names, jobs })
    } catch (err) {
      setPrep(null)
      setToast(`Upload failed: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const decideFix = async (job: PrepJob, fix: PrepFix, approved: boolean) => {
    try {
      await api.decideApproval(`${job.job_id}-${fix.id}`, approved)
      const updated = await api.prepJob(job.job_id)
      const jobs = (prep?.jobs ?? []).map((j) => (j.job_id === job.job_id ? updated : j))
      setPrep((p) => (p ? { ...p, jobs } : p))
      if (updated.status === 'done' && updated.checked_file_id !== job.checked_file_id) {
        const ws = await api.createWorkspace(jobs.map((j) => j.checked_file_id ?? j.input_file_id))
        setWorkspace(ws.workspace_id)
        setToast(`${job.filename}: fix applied in the sandbox; questions now use the new version`)
      } else {
        setToast(approved ? `${job.filename}: approved` : `${job.filename}: rejected, the data stays as it was`)
      }
    } catch (err) {
      setToast(`Decision not saved: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  const decide = async (id: string, approved: boolean) => {
    if (LIVE) {
      // Records the decision in the backend audit trail. It does not execute anything: no action connector exists yet.
      try {
        const row = await api.decideApproval(id, approved)
        setActions((prev) => prev.map((a) => (a.id === id ? toAction(row) : a)))
        setToast(approved ? `${id} approved and recorded · not executed (no connector yet)` : `${id} rejected and recorded`)
      } catch (err) {
        setToast(`Decision not saved: ${err instanceof Error ? err.message : String(err)}`)
      }
      setOpenId(null)
      return
    }
    const now = new Date().toTimeString().slice(0, 8)
    setActions((prev) =>
      prev.map((a) =>
        a.id === id
          ? {
              ...a,
              status: approved ? 'verified' : 'recommended',
              audit: [
                ...a.audit,
                { time: now, actor: 'Meera Rao (CFO)', event: approved ? 'Approved — bound to reviewed parameters' : 'Rejected' },
                ...(approved ? [{ time: now, actor: 'Payments connector', event: 'Verified: payment scheduled in sandbox' }] : []),
              ],
            }
          : a,
      ),
    )
    setToast(approved ? `${id} approved · scheduled and verified in sandbox` : `${id} rejected · logged to audit trail`)
    setOpenId(null)
  }

  const pendingCount = actions.filter((a) => a.status === 'pending').length
  const handledTotal = LIVE ? live.events.filter((e) => e.status === 'processed').length : undefined
  const away = LIVE ? 0 : overnightRuns.length
  const unread = notifOpen ? 0 : (awaySeen ? 0 : away) + feed.length - seenFeed
  const bell = (
    <NotificationButton
      unread={Math.max(0, unread)}
      active={notifOpen}
      onClick={() => {
        setNotifOpen((o) => !o)
        setAwaySeen(true)
        setSeenFeed(feed.length)
      }}
    />
  )
  const open = actions.find((a) => a.id === openId) ?? null
  const sourceCount = LIVE ? live.sources.length : undefined
  const verifiedAnswers = live.answers.filter((a) => !a.stale).length
  const staleAnswers = live.answers.filter((a) => a.stale).length

  return (
    <div className="flex h-full">
      <Sidebar
        view={view}
        onNavigate={setView}
        onNew={LIVE ? newConversation : () => setView('home')}
        pendingCount={pendingCount}
        paused={paused}
        onTogglePause={() => {
          setPaused((p) => !p)
          setToast(paused ? 'Agent resumed' : 'Emergency stop: autonomous execution paused')
        }}
        hasThread={!!query}
        recent={LIVE ? conversations.map((c) => c.title) : undefined}
        onRecent={LIVE ? (_title, index) => openConversation(index) : undefined}
        activeIndex={LIVE && view === 'thread' ? conversations.findIndex((c) => c.conversation_id === conversationId) : undefined}
        runningIndex={LIVE ? conversations.findIndex((c) => c.conversation_id === (runningId ?? backgroundId)) : undefined}
        onDeleteRecent={LIVE ? deleteConversation : undefined}
        watchSummary={LIVE ? `Autopilot on · ${live.sources.length} sources watched · ${live.answers.length} saved answers` : undefined}
      />
      <main className="relative min-w-0 flex-1">
        {view !== 'thread' && <div className="absolute right-5 top-3 z-10">{bell}</div>}
        {view === 'home' && (
          <Home
            onSubmit={LIVE ? askNew : ask}
            pendingCount={pendingCount}
            paused={paused}
            handledLive={feed.filter((f) => f.handled !== 'escalated').length}
            onOpenAutopilot={() => setView('autopilot')}
            handledTotal={handledTotal}
            examples={LIVE ? LIVE_EXAMPLES : undefined}
            sourceCount={sourceCount}
            onAttach={LIVE ? attach : undefined}
            attachments={LIVE ? attachments : undefined}
            onRemoveAttachment={
              LIVE
                ? () => {
                    setWorkspace(null)
                    setAttachments([])
                  }
                : undefined
            }
          />
        )}
        {view === 'autopilot' && (
          <AutopilotPage
            onSubmit={ask}
            pendingCount={pendingCount}
            paused={paused}
            feed={LIVE ? [...toLeakageFeed(live.leakageCases), ...feed] : feed}
            onOpenAction={setOpenId}
            goals={LIVE ? [...toLeakageGoals(live.leakage), ...toGoals(live.sources, live.events)] : undefined}
            handledTotal={handledTotal}
            sourceCount={sourceCount}
            kpis={LIVE ? leakageKpis(live.leakage) : undefined}
            onGoal={
              LIVE
                ? (g) =>
                    live.leakage?.by_type[g.id] &&
                    ask(`Show the open ${g.title.toLowerCase()} cases with the highest recoverable value, with supplier and root cause`)
                : undefined
            }
          />
        )}
        {view === 'thread' &&
          (LIVE && (request || turns.length > 0 || prep) ? (
            <LiveThread
              request={request}
              history={turns}
              onConversation={(id, ws) => {
                setConversationId(id)
                setRunningId(id)
                if (ws) setWorkspace(ws)
              }}
              onRunning={(running) => setRunningId(running ? (conversationId ?? 'new') : null)}
              onComplete={setLastDone}
              attachments={attachments}
              onFollowUp={ask}
              onRerun={() => request && setRequest({ ...request })}
              headerRight={bell}
              related={LIVE_EXAMPLES.filter((q) => q !== request?.question)}
              known={live.sources}
              models={live.models}
              sourceCount={sourceCount}
              onAttach={attach}
              prep={prep}
              onDecideFix={decideFix}
            />
          ) : (
            <Thread
              query={query}
              actions={actions}
              onOpenAction={setOpenId}
              onApprove={(id) => decide(id, true)}
              onFollowUp={ask}
              paused={paused}
              headerRight={bell}
            />
          ))}
        {view === 'approvals' && <ApprovalsPage actions={actions} onOpen={setOpenId} onApprove={(id) => decide(id, true)} />}
        {view === 'activity' && (
          <ActivityPage
            actions={actions}
            onOpen={setOpenId}
            entries={
              LIVE
                ? toActivity(live.answers, live.events, live.investigations).map((e) => ({ ...e, onClick: e.question ? () => ask(e.question!) : undefined }))
                : undefined
            }
          />
        )}
        {view === 'settings' && <SettingsPage />}
        {view === 'sources' && <SourcesPage items={LIVE ? liveSources : undefined} />}
      </main>

      <NotificationsPanel
        open={notifOpen}
        onClose={() => setNotifOpen(false)}
        pendingCount={pendingCount}
        onReview={() => setView('approvals')}
        {...(LIVE
          ? {
              windowLabel: 'latest sync events',
              dataLabel: live.backend === 'up' ? 'live backend' : 'backend unreachable',
              runs: toTimeline(live.investigations),
              stats: [
                { l: 'Handled on its own', v: String(handledTotal ?? 0), s: 'sync events investigated', c: 'text-ink' },
                { l: 'Needed you', v: String(feed.filter((f) => f.handled !== 'auto').length), s: 'escalated or blocked', c: 'text-amber-700' },
                { l: 'Answers current', v: String(verifiedAnswers), s: `${staleAnswers} stale after data changes`, c: 'text-emerald-700' },
                { l: 'Sources watched', v: String(live.sources.length), s: `${live.investigations.length} investigations`, c: 'text-ink' },
              ],
            }
          : {})}
      />

      <ActionDrawer action={open} onClose={() => setOpenId(null)} onApprove={(id) => decide(id, true)} onReject={(id) => decide(id, false)} />

      {toast && (
        <div className="fixed bottom-6 left-1/2 z-50 flex -translate-x-1/2 items-center gap-2 rounded-full bg-ink px-4 py-2 text-[13px] text-white shadow-lg fade-up">
          <CheckCircle2 size={14} className="text-emerald-400" /> {toast}
        </div>
      )}
    </div>
  )
}
