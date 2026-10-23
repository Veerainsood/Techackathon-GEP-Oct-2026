import { CheckCircle2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { ActionDrawer } from './components/ActionDrawer'
import { AutopilotPage, type FeedItem } from './components/Autopilot'
import { Home } from './components/Home'
import { ActivityPage, ApprovalsPage, SettingsPage, SourcesPage } from './components/Pages'
import { Sidebar, type View } from './components/Sidebar'
import { Thread } from './components/Thread'
import { initialActions, liveEvents, overnightRuns, type Action } from './data'
import { NotificationButton, NotificationsPanel } from './components/NotificationsPanel'
import { LiveApp } from './live/LiveApp'

// Live mode (default) is wired to the FastAPI backend; demo mode keeps the original static mockup.
const LIVE = ((import.meta.env.VITE_UDRE_MODE as string | undefined) ?? 'live') !== 'demo'

export default function App() {
  return LIVE ? <LiveApp /> : <DemoApp />
}

function DemoApp() {
  const [view, setView] = useState<View>('home')
  const [query, setQuery] = useState('')
  const [actions, setActions] = useState<Action[]>(initialActions)
  const [openId, setOpenId] = useState<string | null>(null)
  const [paused, setPaused] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  const [feed, setFeed] = useState<FeedItem[]>([])
  const [notifOpen, setNotifOpen] = useState(false)
  const [awaySeen, setAwaySeen] = useState(false)
  const [seenFeed, setSeenFeed] = useState(0)

  useEffect(() => {
    if (notifOpen) setSeenFeed(feed.length)
  }, [notifOpen, feed.length])

  useEffect(() => {
    if (paused || feed.length >= liveEvents.length) return
    const t = setTimeout(
      () => {
        const ev = liveEvents[feed.length]
        setFeed((f) => [{ ...ev, at: new Date().toTimeString().slice(0, 5) }, ...f])
        if (ev.action) {
          setActions((prev) => [ev.action!, ...prev])
          setToast(`Agent escalated: ${ev.title}`)
        }
      },
      feed.length === 0 ? 2500 : 5000,
    )
    return () => clearTimeout(t)
  }, [feed.length, paused])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 2800)
    return () => clearTimeout(t)
  }, [toast])

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

  const ask = (q: string) => {
    setQuery(q)
    setView('thread')
  }

  const decide = (id: string, approved: boolean) => {
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
  const unread = notifOpen ? 0 : (awaySeen ? 0 : overnightRuns.length) + feed.length - seenFeed
  const bell = (
    <NotificationButton
      unread={unread}
      active={notifOpen}
      onClick={() => {
        setNotifOpen((o) => !o)
        setAwaySeen(true)
        setSeenFeed(feed.length)
      }}
    />
  )
  const open = actions.find((a) => a.id === openId) ?? null

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
          />
        )}
        {view === 'autopilot' && (
          <AutopilotPage onSubmit={ask} pendingCount={pendingCount} paused={paused} feed={feed} onOpenAction={setOpenId} />
        )}
        {view === 'thread' && (
          <Thread
            query={query}
            actions={actions}
            onOpenAction={setOpenId}
            onApprove={(id) => decide(id, true)}
            onFollowUp={ask}
            paused={paused}
            headerRight={bell}
          />
        )}
        {view === 'approvals' && <ApprovalsPage actions={actions} onOpen={setOpenId} onApprove={(id) => decide(id, true)} />}
        {view === 'activity' && <ActivityPage actions={actions} onOpen={setOpenId} />}
        {view === 'settings' && <SettingsPage />}
        {view === 'sources' && <SourcesPage />}
      </main>

      <NotificationsPanel
        open={notifOpen}
        onClose={() => setNotifOpen(false)}
        pendingCount={pendingCount}
        onReview={() => setView('approvals')}
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
