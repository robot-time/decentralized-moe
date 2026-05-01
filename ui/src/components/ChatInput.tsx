import { useRef, useState } from 'react'

interface Props {
  onSend: (text: string) => void
  disabled: boolean
}

export default function ChatInput({ onSend, disabled }: Props) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  function submit() {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setValue('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  function onInput(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setValue(e.target.value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  }

  return (
    <div className="px-4 pb-4 pt-2">
      <div className={`flex items-end gap-2 bg-[#1c1c1c] border rounded-2xl px-4 py-3 transition-colors ${
        disabled ? 'border-[#2a2a2a] opacity-60' : 'border-[#333] focus-within:border-[#444]'
      }`}>
        <textarea
          ref={textareaRef}
          value={value}
          onChange={onInput}
          onKeyDown={onKeyDown}
          disabled={disabled}
          rows={1}
          placeholder="Message MoE Network"
          className="flex-1 bg-transparent text-[#ececec] placeholder-[#555] text-sm resize-none outline-none leading-relaxed min-h-[24px] max-h-[200px]"
        />
        <button
          onClick={submit}
          disabled={disabled || !value.trim()}
          className={`shrink-0 w-8 h-8 rounded-full flex items-center justify-center transition-colors ${
            value.trim() && !disabled
              ? 'bg-white text-black hover:bg-[#e5e5e5]'
              : 'bg-[#2a2a2a] text-[#555]'
          }`}
        >
          {disabled ? (
            /* spinner */
            <svg className="animate-spin" width="14" height="14" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeOpacity="0.25"/>
              <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round"/>
            </svg>
          ) : (
            /* send arrow */
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 4l8 8-8 8M4 12h16" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
            </svg>
          )}
        </button>
      </div>
      <p className="text-center text-[11px] text-[#444] mt-2">
        Shift+Enter for new line · Expert responses may vary
      </p>
    </div>
  )
}
