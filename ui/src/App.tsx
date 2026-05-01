import { useCallback, useEffect, useRef, useState } from 'react'
import type { Conversation, ExpertResponse, Message, NetworkStatus } from './types'
import { fetchStatus, startNodes, stopNodes, streamChat } from './api'
import Sidebar from './components/Sidebar'
import Chat from './components/Chat'

function makeId() {
  return Math.random().toString(36).slice(2)
}

function titleFromMessage(text: string): string {
  return text.length > 40 ? text.slice(0, 40) + '…' : text
}

export default function App() {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState<NetworkStatus | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // Poll network status every 5 s
  useEffect(() => {
    const poll = async () => {
      try { setStatus(await fetchStatus()) } catch { /* server not up yet */ }
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => clearInterval(id)
  }, [])

  const activeConversation = conversations.find(c => c.id === activeId) ?? null

  function newConversation(): Conversation {
    const c: Conversation = {
      id: makeId(),
      title: 'New Chat',
      messages: [],
      createdAt: new Date(),
    }
    setConversations(prev => [c, ...prev])
    setActiveId(c.id)
    return c
  }

  function handleNew() {
    newConversation()
  }

  const handleSend = useCallback(async (text: string) => {
    if (loading) return

    // Get or create conversation
    let convId = activeId
    if (!convId || !conversations.find(c => c.id === convId)) {
      const c = newConversation()
      convId = c.id
    }

    const userMsg: Message = {
      id: makeId(),
      role: 'user',
      content: text,
      timestamp: new Date(),
    }

    // Update title on first message
    setConversations(prev => prev.map(c =>
      c.id === convId
        ? {
            ...c,
            title: c.messages.length === 0 ? titleFromMessage(text) : c.title,
            messages: [...c.messages, userMsg],
          }
        : c
    ))

    setLoading(true)

    // Placeholder assistant message that we'll update as events arrive
    const asstId = makeId()
    const asstMsg: Message = {
      id: asstId,
      role: 'status',
      content: 'Thinking…',
      timestamp: new Date(),
    }
    setConversations(prev => prev.map(c =>
      c.id === convId ? { ...c, messages: [...c.messages, asstMsg] } : c
    ))

    const experts: ExpertResponse[] = []

    try {
      for await (const event of streamChat(text)) {
        if (event.type === 'status') {
          setConversations(prev => prev.map(c =>
            c.id === convId
              ? { ...c, messages: c.messages.map(m =>
                  m.id === asstId ? { ...m, content: event.text } : m
                )}
              : c
          ))
        } else if (event.type === 'expert') {
          experts.push({ specialty: event.specialty, confidence: event.confidence, response: event.response })
        } else if (event.type === 'answer') {
          setConversations(prev => prev.map(c =>
            c.id === convId
              ? { ...c, messages: c.messages.map(m =>
                  m.id === asstId
                    ? {
                        ...m,
                        role: 'assistant',
                        content: event.text,
                        experts,
                        orchestrated_by: event.orchestrated_by,
                        peers_queried: event.peers_queried,
                      }
                    : m
                )}
              : c
          ))
        } else if (event.type === 'error') {
          setConversations(prev => prev.map(c =>
            c.id === convId
              ? { ...c, messages: c.messages.map(m =>
                  m.id === asstId
                    ? { ...m, role: 'assistant', content: `Error: ${event.text}` }
                    : m
                )}
              : c
          ))
        }
      }
    } catch (err) {
      setConversations(prev => prev.map(c =>
        c.id === convId
          ? { ...c, messages: c.messages.map(m =>
              m.id === asstId
                ? { ...m, role: 'assistant', content: `Connection error: ${err}` }
                : m
            )}
          : c
      ))
    } finally {
      setLoading(false)
    }
  }, [activeId, conversations, loading])

  async function handleToggleNetwork() {
    try {
      const next = status?.running ? await stopNodes() : await startNodes()
      setStatus(next)
    } catch { /* ignore */ }
  }

  return (
    <div className="flex h-full w-full">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        status={status}
        onSelect={setActiveId}
        onNew={handleNew}
        onToggleNetwork={handleToggleNetwork}
      />
      <main className="flex flex-1 flex-col min-w-0">
        <Chat
          conversation={activeConversation}
          loading={loading}
          onSend={handleSend}
        />
      </main>
    </div>
  )
}
