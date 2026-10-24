import {
  Bot,
  CircleDashed,
  Compass,
  Trash2,
  Inbox,
  Database,
  History,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  ScrollText,
  Settings,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { recentThreads } from '../data'

export type View = 'home' | 'thread' | 'autopilot' | 'approvals' | 'activity' | 'settings' | 'sources'

interface Props {
  view: View
  onNavigate: (v: View) => void
  onNew: () => void
  pendingCount: number
  paused: boolean
  hasThread: boolean
  recent?: string[]
  onRecent?: (q: string, index: number) => void
  watchSummary?: string
  activeIndex?: number // the open chat
  runningIndex?: number // the chat that is answering right now
  onDeleteRecent?: (index: number) => void
}

const STORAGE_KEY = 'finsight.sidebar.collapsed'

export function Sidebar({
  view,
  onNavigate,
  onNew,
  pendingCount,
  paused,
  hasThread,
  recent,
  onRecent,
  watchSummary,
  activeIndex,
  runningIndex,
  onDeleteRecent,
}: Props) {
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(STORAGE_KEY) === '1')

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0')
  }, [collapsed])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'b') {
        e.preventDefault()
        setCollapsed((c) => !c)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const items: { id: View; label: string; icon: typeof Compass; badge?: number; live?: boolean }[] = [
    { id: 'home', label: 'Home', icon: Compass },
    { id: 'autopilot', label: 'Autopilot', icon: Bot, live: true },
    { id: 'approvals', label: 'Approvals', icon: Inbox, badge: pendingCount },
    { id: 'activity', label: 'Activity log', icon: ScrollText },
    { id: 'sources', label: 'Sources', icon: Database },
    { id: 'settings', label: 'Settings', icon: Settings },
  ]
  const library = recent ?? recentThreads

  const Logo = (
    <div className="flex h-7 w-9 shrink-0 items-center justify-center rounded-md bg-brand text-[11px] font-bold tracking-wide text-white">
      GEP
    </div>
  )

  return (
    <aside
      className={`flex shrink-0 flex-col border-r border-line bg-[#f5f4f0] py-4 transition-[width] duration-200 ${
        collapsed ? 'w-[60px] items-center px-2' : 'w-60 px-3'
      }`}
    >
      {collapsed ? (
        <button
          onClick={() => setCollapsed(false)}
          title="Expand sidebar (⌘B)"
          className="group relative mb-5 flex h-9 w-9 items-center justify-center rounded-lg hover:bg-white"
        >
          <span className="group-hover:hidden">{Logo}</span>
          <PanelLeftOpen size={17} className="hidden text-stone-600 group-hover:block" />
        </button>
      ) : (
        <div className="mb-5 flex items-center justify-between pl-2">
          <div className="flex items-center gap-2">
            {Logo}
            <span className="text-[19px] font-semibold leading-none tracking-tight">FinSight</span>
          </div>
          <button
            onClick={() => setCollapsed(true)}
            title="Collapse sidebar (⌘B)"
            className="rounded-lg p-1.5 text-stone-500 hover:bg-white hover:text-ink"
          >
            <PanelLeftClose size={17} />
          </button>
        </div>
      )}

      {collapsed ? (
        <button
          onClick={onNew}
          title="New goal (⌘K)"
          className="mb-4 flex h-9 w-9 items-center justify-center rounded-full border border-line bg-white text-ink shadow-sm hover:border-stone-300"
        >
          <Plus size={16} />
        </button>
      ) : (
        <button
          onClick={onNew}
          className="mb-4 flex items-center justify-between rounded-full border border-line bg-white px-4 py-2 text-sm text-ink shadow-sm transition hover:border-stone-300"
        >
          <span className="flex items-center gap-2">
            <Plus size={16} /> New goal
          </span>
          <kbd className="rounded border border-line px-1.5 text-[10px] text-muted">⌘K</kbd>
        </button>
      )}

      <nav className={`flex flex-col gap-0.5 ${collapsed ? 'items-center' : ''}`}>
        {items.map(({ id, label, icon: Icon, badge, live }) => {
          const active = view === id || (id === 'home' && view === 'thread')
          const tone = active ? 'bg-white font-medium text-ink shadow-sm' : 'text-stone-600 hover:bg-white/60'
          if (collapsed) {
            return (
              <button
                key={id}
                onClick={() => onNavigate(id)}
                title={label}
                className={`relative flex h-9 w-9 items-center justify-center rounded-lg transition ${tone}`}
              >
                <Icon size={17} />
                {live && !paused && <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-emerald-500" />}
                {!!badge && (
                  <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-500 px-1 text-[9px] font-semibold text-white">
                    {badge}
                  </span>
                )}
              </button>
            )
          }
          return (
            <button
              key={id}
              onClick={() => onNavigate(id)}
              className={`flex items-center justify-between rounded-lg px-3 py-2 text-sm transition ${tone}`}
            >
              <span className="flex items-center gap-2.5">
                <Icon size={16} />
                {label}
              </span>
              {!!badge && (
                <span className="rounded-full bg-amber-500 px-1.5 text-[10px] font-semibold text-white">{badge}</span>
              )}
              {live && !paused && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />}
            </button>
          )
        })}
      </nav>

      {!collapsed && (
        <>
          <div className="mt-6 px-3 text-[11px] font-medium uppercase tracking-wider text-stone-400">Library</div>
          <div className="mt-1 flex max-h-[45vh] flex-col gap-0.5 overflow-y-auto scrollbar-thin">
            {library.map((t, i) => (
              <div
                key={`${t}-${i}`}
                onClick={() => (onRecent ? onRecent(t, i) : i === 0 && hasThread && onNavigate('thread'))}
                className={`group flex cursor-pointer items-center gap-2 rounded-lg px-3 py-1.5 text-left text-[13px] ${
                  activeIndex === i ? 'bg-white font-medium text-ink shadow-sm' : 'text-stone-600 hover:bg-white/60'
                }`}
              >
                {runningIndex === i ? (
                  <CircleDashed size={13} className="shrink-0 animate-spin text-brand" />
                ) : (
                  <History size={13} className="shrink-0 text-stone-400" />
                )}
                <span className="min-w-0 flex-1 truncate">{t}</span>
                {onDeleteRecent && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      onDeleteRecent(i)
                    }}
                    title="Delete chat"
                    className="hidden shrink-0 rounded p-0.5 text-stone-400 hover:bg-stone-100 hover:text-rose-600 group-hover:block"
                  >
                    <Trash2 size={12} />
                  </button>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {collapsed ? (
        <div className="mt-auto flex flex-col items-center gap-3">
          <div
            title="Meera Rao · CFO"
            className="flex h-8 w-8 items-center justify-center rounded-full bg-stone-800 text-xs font-medium text-white"
          >
            MR
          </div>
        </div>
      ) : (
        <div className="mt-auto space-y-3">
          <div className="rounded-xl border border-line bg-white p-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium">Autopilot</span>
              <span className={`h-2 w-2 rounded-full ${paused ? 'bg-rose-500' : 'animate-pulse bg-emerald-500'}`} />
            </div>
            <p className="mt-1 text-[11px] leading-snug text-muted">
              {paused ? 'Paused. Agent will only analyze and recommend.' : watchSummary ?? 'Autopilot on · 3 standing goals · 6 sources watched'}
            </p>
          </div>
          <div className="flex items-center gap-2.5 px-1">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-stone-800 text-xs font-medium text-white">MR</div>
            <div className="leading-tight">
              <div className="text-[13px] font-medium">Meera Rao</div>
              <div className="text-[11px] text-muted">CFO · Northwind India</div>
            </div>
          </div>
        </div>
      )}
    </aside>
  )
}
