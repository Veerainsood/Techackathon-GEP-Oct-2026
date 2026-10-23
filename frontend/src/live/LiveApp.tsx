import { useEffect, useState } from 'react'
import { Sidebar, type View } from '../components/Sidebar'
import { api, type AskRequest } from './api'
import { LiveHome } from './LiveHome'
import { LiveActivityPage, LiveChangesPage, LiveSourcesPage } from './LiveSources'
import { LiveThread } from './LiveThread'

export function LiveApp() {
  const [view, setView] = useState<View>('home')
  const [request, setRequest] = useState<AskRequest | null>(null)
  const [recent, setRecent] = useState<string[]>([])
  const [backend, setBackend] = useState<'up' | 'down' | 'checking'>('checking')

  useEffect(() => {
    const check = () => api.health().then(() => setBackend('up')).catch(() => setBackend('down'))
    check()
    const t = setInterval(check, 15000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    api.answers().then((a) => setRecent(a.slice(0, 8).map((x) => x.question))).catch(() => setRecent([]))
  }, [request])

  const ask = (r: AskRequest) => {
    setRequest({ ...r })
    setView('thread')
  }

  return (
    <div className="flex h-full">
      <Sidebar
        view={view}
        onNavigate={setView}
        onNew={() => setView('home')}
        pendingCount={0}
        paused={false}
        onTogglePause={() => undefined}
        hasThread={!!request}
        live
        backend={backend}
        recent={recent}
        onRecent={(q) => ask({ question: q })}
      />
      <main className="relative min-w-0 flex-1">
        {view === 'home' && <LiveHome onAsk={ask} />}
        {view === 'thread' && request && <LiveThread request={request} onFollowUp={(q) => ask({ ...request, question: q })} />}
        {view === 'thread' && !request && <LiveHome onAsk={ask} />}
        {view === 'sources' && <LiveSourcesPage />}
        {view === 'autopilot' && <LiveChangesPage />}
        {view === 'activity' && <LiveActivityPage onAsk={(q) => ask({ question: q })} />}
      </main>
    </div>
  )
}
