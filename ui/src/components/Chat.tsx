import { useEffect, useRef } from 'react'
import type { Conversation } from '../types'
import Message from './Message'
import ChatInput from './ChatInput'

interface Props {
  conversation: Conversation | null
  loading: boolean
  onSend: (text: string) => void
}

export default function Chat({ conversation, loading, onSend }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [conversation?.messages])

  // Empty state
  if (!conversation || conversation.messages.length === 0) {
    return (
      <div className="flex flex-col flex-1 h-full">
        <div className="flex-1 flex flex-col items-center justify-center gap-6 px-4">
          <div className="text-center space-y-2">
            <h1 className="text-2xl font-semibold text-white">MoE Network</h1>
            <p className="text-sm text-[#666]">
              A decentralized mixture of expert models running locally
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 max-w-lg w-full">
            {SUGGESTIONS.map(s => (
              <button
                key={s}
                onClick={() => onSend(s)}
                disabled={loading}
                className="text-left px-4 py-3 rounded-xl border border-[#2a2a2a] bg-[#171717] hover:bg-[#1f1f1f] text-sm text-[#a3a3a3] hover:text-white transition-colors"
              >
                {s}
              </button>
            ))}
          </div>
        </div>
        <ChatInput onSend={onSend} disabled={loading} />
      </div>
    )
  }

  return (
    <div className="flex flex-col flex-1 h-full">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto py-4 space-y-1">
        {conversation.messages.map(m => (
          <Message key={m.id} message={m} />
        ))}
        <div ref={bottomRef} />
      </div>

      <ChatInput onSend={onSend} disabled={loading} />
    </div>
  )
}

const SUGGESTIONS = [
  'Explain quantum entanglement',
  'Solve: x² + 5x + 6 = 0',
  'Write a binary search in Python',
  'What causes the northern lights?',
]
