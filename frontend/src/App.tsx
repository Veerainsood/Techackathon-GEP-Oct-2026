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
import { LIVE, toAction, toActivity, toFeed, toGoals, toSource, toTimeline, useLiveData } from './live/adapter'
import { api, type AskRequest } from './live/api'
import { LiveThread } from './live/LiveThread'

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

  const ask = (q: string) => {
    setQuery(q)
    if (LIVE) setRequest({ question: q, workspace_id: workspace })
    setView('thread')
  }

  const attach = async (files: File[]) => {
    try {
      setToast(`Uploading ${files.map((f) => f.name).join(', ')}…`)
      const stored = await api.upload(files)
      const ws = await api.createWorkspace(stored.map((f) => f.file_id))
      setWorkspace(ws.workspace_id)
      setToast(`${files.length} file${files.length > 1 ? 's' : ''} ready · next questions use ${ws.collections.join(', ')}`)
    } catch (err) {
      setToast(`Upload failed: ${err instanceof Error ? err.message : String(err)}`)
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
        onNew={() => setView('home')}
        pendingCount={pendingCount}
        paused={paused}
        onTogglePause={() => {
          setPaused((p) => !p)
          setToast(paused ? 'Agent resumed' : 'Emergency stop: autonomous execution paused')
        }}
        hasThread={!!query}
        recent={LIVE ? live.answers.slice(0, 6).map((a) => a.question) : undefined}
        onRecent={LIVE ? ask : undefined}
        watchSummary={LIVE ? `Autopilot on · ${live.sources.length} sources watched · ${live.answers.length} saved answers` : undefined}
      />
      <main className="relative min-w-0 flex-1">
        {view !== 'thread' && <div className="absolute right-5 top-3 z-10">{bell}</div>}
        {view === 'home' && (
          <Home
            onSubmit={ask}
            pendingCount={pendingCount}
            paused={paused}
            handledLive={feed.filter((f) => f.handled !== 'escalated').length}
            onOpenAutopilot={() => setView('autopilot')}
            handledTotal={handledTotal}
            examples={LIVE ? LIVE_EXAMPLES : undefined}
            sourceCount={sourceCount}
            onAttach={LIVE ? attach : undefined}
          />
        )}
        {view === 'autopilot' && (
          <AutopilotPage
            onSubmit={ask}
            pendingCount={pendingCount}
            paused={paused}
            feed={feed}
            onOpenAction={setOpenId}
            goals={LIVE ? toGoals(live.sources, live.events) : undefined}
            handledTotal={handledTotal}
            sourceCount={sourceCount}
          />
        )}
        {view === 'thread' &&
          (LIVE && request ? (
            <LiveThread
              request={request}
              onFollowUp={ask}
              onRerun={() => setRequest({ ...request })}
              headerRight={bell}
              related={LIVE_EXAMPLES.filter((q) => q !== request.question)}
              known={live.sources}
              models={live.models}
              sourceCount={sourceCount}
              onAttach={attach}
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
