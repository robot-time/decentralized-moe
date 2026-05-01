import type { Model, NetworkStatus } from './types'

const BASE = '/api'

export async function fetchStatus(): Promise<NetworkStatus> {
  const r = await fetch(`${BASE}/status`)
  return r.json()
}

export async function fetchModels(): Promise<Model[]> {
  const r = await fetch(`${BASE}/models`)
  const data = await r.json()
  return data.models ?? []
}

export async function startNodes(): Promise<NetworkStatus> {
  const r = await fetch(`${BASE}/nodes/start`, { method: 'POST' })
  return r.json()
}

export async function stopNodes(): Promise<NetworkStatus> {
  const r = await fetch(`${BASE}/nodes/stop`, { method: 'POST' })
  return r.json()
}

export type ChatEvent =
  | { type: 'status'; text: string }
  | { type: 'expert'; specialty: string; confidence: number; response: string }
  | { type: 'answer'; text: string; orchestrated_by: string; peers_queried: string[] }
  | { type: 'error'; text: string }

export async function* streamChat(message: string): AsyncGenerator<ChatEvent> {
  const resp = await fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  })

  if (!resp.ok || !resp.body) {
    yield { type: 'error', text: `HTTP ${resp.status}` }
    return
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const lines = buf.split('\n')
    buf = lines.pop() ?? ''
    for (const line of lines) {
      if (line.trim()) {
        try {
          yield JSON.parse(line) as ChatEvent
        } catch {
          // skip malformed lines
        }
      }
    }
  }
}
