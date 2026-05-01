import type { Conversation, NetworkStatus } from '../types'

interface Props {
  conversations: Conversation[]
  activeId: string | null
  status: NetworkStatus | null
  onSelect: (id: string) => void
  onNew: () => void
  onToggleNetwork: () => void
}

export default function Sidebar({ conversations, activeId, status, onSelect, onNew, onToggleNetwork }: Props) {
  const running = status?.running ?? false

  return (
    <aside className="flex flex-col w-64 shrink-0 bg-[#171717] border-r border-[#2a2a2a] h-full">

      {/* Logo */}
      <div className="flex items-center gap-2.5 px-4 py-4 border-b border-[#2a2a2a]">
        <NetworkIcon />
        <span className="font-semibold text-[15px] text-white tracking-tight">MoE Network</span>
      </div>

      {/* New chat button */}
      <div className="px-3 pt-3">
        <button
          onClick={onNew}
          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-[#ececec] hover:bg-[#262626] transition-colors"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 5v14M5 12h14" strokeLinecap="round"/>
          </svg>
          New Chat
        </button>
      </div>

      {/* Conversation list */}
      <nav className="flex-1 overflow-y-auto px-3 py-2 space-y-0.5">
        {conversations.length === 0 && (
          <p className="text-xs text-[#666] px-3 py-4 text-center">No conversations yet</p>
        )}
        {conversations.map(c => (
          <button
            key={c.id}
            onClick={() => onSelect(c.id)}
            className={`w-full text-left px-3 py-2 rounded-lg text-sm truncate transition-colors ${
              c.id === activeId
                ? 'bg-[#262626] text-white'
                : 'text-[#a3a3a3] hover:bg-[#1f1f1f] hover:text-white'
            }`}
          >
            {c.title}
          </button>
        ))}
      </nav>

      {/* Network status footer */}
      <div className="px-3 pb-3 border-t border-[#2a2a2a] pt-3">
        <button
          onClick={onToggleNetwork}
          className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors ${
            running
              ? 'text-[#a3a3a3] hover:bg-[#1f1f1f]'
              : 'text-[#a3a3a3] hover:bg-[#1f1f1f]'
          }`}
        >
          <span className={`w-2 h-2 rounded-full shrink-0 ${running ? 'bg-green-500' : 'bg-[#555]'}`} />
          <span className="truncate">
            {running ? `Network running (${status?.nodes ?? 0} nodes)` : 'Network stopped'}
          </span>
        </button>
      </div>
    </aside>
  )
}

function NetworkIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" fill="#1e293b" stroke="#334155" strokeWidth="1.5"/>
      <circle cx="12" cy="5"  r="2" fill="#3b82f6"/>
      <circle cx="19" cy="12" r="2" fill="#22c55e"/>
      <circle cx="12" cy="19" r="2" fill="#f97316"/>
      <circle cx="5"  cy="12" r="2" fill="#a855f7"/>
      <circle cx="12" cy="12" r="1.5" fill="white"/>
      <line x1="12" y1="7"  x2="12" y2="11" stroke="white" strokeOpacity="0.4" strokeWidth="1"/>
      <line x1="17" y1="12" x2="13" y2="12" stroke="white" strokeOpacity="0.4" strokeWidth="1"/>
      <line x1="12" y1="17" x2="12" y2="13" stroke="white" strokeOpacity="0.4" strokeWidth="1"/>
      <line x1="7"  y1="12" x2="11" y2="12" stroke="white" strokeOpacity="0.4" strokeWidth="1"/>
    </svg>
  )
}
