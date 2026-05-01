import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message as Msg } from '../types'

interface Props {
  message: Msg
}

export default function Message({ message }: Props) {
  if (message.role === 'status') {
    return (
      <div className="flex justify-center py-2">
        <span className="text-xs text-[#666] flex items-center gap-1.5">
          <span className="flex gap-1">
            <span className="typing-dot w-1.5 h-1.5 rounded-full bg-[#555] inline-block"/>
            <span className="typing-dot w-1.5 h-1.5 rounded-full bg-[#555] inline-block"/>
            <span className="typing-dot w-1.5 h-1.5 rounded-full bg-[#555] inline-block"/>
          </span>
          {message.content}
        </span>
      </div>
    )
  }

  if (message.role === 'user') {
    return (
      <div className="flex justify-end px-4 py-2">
        <div className="max-w-[75%] bg-[#262626] text-[#ececec] rounded-2xl rounded-tr-sm px-4 py-2.5 text-sm leading-relaxed">
          {message.content}
        </div>
      </div>
    )
  }

  // Assistant message
  return (
    <div className="px-4 py-3">
      <div className="flex gap-3 max-w-3xl mx-auto">
        {/* Avatar */}
        <div className="shrink-0 w-7 h-7 rounded-full bg-[#1e293b] border border-[#334155] flex items-center justify-center mt-0.5">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="9" stroke="#94a3b8" strokeWidth="1.5"/>
            <circle cx="12" cy="12" r="2" fill="#94a3b8"/>
          </svg>
        </div>

        <div className="flex-1 min-w-0">
          {/* Expert badges */}
          {message.peers_queried && message.peers_queried.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-2">
              {message.peers_queried.map(s => {
                const expert = message.experts?.find(e => e.specialty === s)
                return (
                  <span
                    key={s}
                    title={expert ? `confidence: ${expert.confidence}/10` : undefined}
                    className="text-[11px] px-2 py-0.5 rounded-full border border-[#2a2a2a] text-[#888] bg-[#1a1a1a]"
                  >
                    {s}
                    {expert && (
                      <span className="ml-1 text-[#555]">{expert.confidence}/10</span>
                    )}
                  </span>
                )
              })}
            </div>
          )}

          {/* Main answer */}
          <div className="prose prose-invert prose-sm max-w-none text-[#ececec] leading-relaxed">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.content}
            </ReactMarkdown>
          </div>

          {/* Footer */}
          {message.orchestrated_by && (
            <p className="text-[11px] text-[#555] mt-2">
              Orchestrated by {message.orchestrated_by}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
