export type AssistantConfig = { provider: string; model: string; base_url: string; has_api_key: boolean; api_key_masked: string | null; timeout_seconds: number; source: string }
export type Conversation = { conversation_id: string; title: string; message_count: number; created_at: string; updated_at: string }
export type AssistantMessage = { message_id: string; role: string; content: string; status: string; facts?: Record<string, unknown> | null; created_at: string }
const json = async (response: Response) => { if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || '请求失败'); return response.json() }
export const assistantApi = {
  config: () => fetch('/api/v1/ai/config').then(json).then((body) => body.data as AssistantConfig),
  updateConfig: (payload: Omit<AssistantConfig, 'has_api_key'|'api_key_masked'|'source'>) => fetch('/api/v1/ai/config', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }).then(json).then((body) => body.data as AssistantConfig),
  testConfig: () => fetch('/api/v1/ai/config/test', { method: 'POST' }).then(json),
  conversations: () => fetch('/api/v1/ai/conversations').then(json).then((body) => body.data as Conversation[]),
  createConversation: () => fetch('/api/v1/ai/conversations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).then(json) as Promise<Conversation>,
  deleteConversation: (id: string) => fetch(`/api/v1/ai/conversations/${id}`, { method: 'DELETE' }).then(json),
  messages: (id: string) => fetch(`/api/v1/ai/conversations/${id}/messages`).then(json).then((body) => body.data as AssistantMessage[]),
  ask: (conversation_id: string, question: string) => fetch('/api/v1/ai/query', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ conversation_id, question }) }).then(json),
}
