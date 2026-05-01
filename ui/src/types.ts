export interface ExpertResponse {
  specialty: string
  confidence: number
  response: string
}

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'status'
  content: string
  experts?: ExpertResponse[]
  orchestrated_by?: string
  peers_queried?: string[]
  timestamp: Date
}

export interface Conversation {
  id: string
  title: string
  messages: Message[]
  createdAt: Date
}

export interface Model {
  name: string
  size: number
}

export interface NetworkStatus {
  running: boolean
  nodes: number
}
