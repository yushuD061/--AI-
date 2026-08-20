import { useEffect, useRef, useState } from 'react'
import { ArrowRight, Bot, Check, ChevronDown, Database, MessageSquarePlus, MoreHorizontal, Send, Settings, Trash2, X } from 'lucide-react'
import { assistantApi, AssistantConfig, AssistantMessage, Conversation, DashboardTarget } from './api/assistant'

const fieldNames: Record<string, string> = { intent: '分析目标', metric: '指标', start_date: '开始日期', end_date: '结束日期', product_name: '商品', store_id: '门店', category: '品类', dimensions: '分析维度' }

function dashboardUrl(target: DashboardTarget) {
  const query = new URLSearchParams({ start_date: target.start_date, end_date: target.end_date, metric: target.metric, view: target.view })
  if (target.store_id) query.set('store_id', target.store_id)
  return `/?${query}`
}

function AssistantAnswerMeta({ message }: { message: AssistantMessage }) {
  if (message.role !== 'assistant') return null
  const context = message.context
  return <div className="fact-card">
    <span className={`status-dot ${message.status}`}>{message.status === 'answered_local' ? '本地查询' : message.status === 'provider_error' ? '模型不可用 · 已用本地结果' : message.status === 'clarification_required' ? '需要补充条件' : message.status}</span>
    {!!context?.inherited_fields?.length && <div className="context-tags"><span>沿用</span>{context.inherited_fields.map((field) => <b key={field}>{fieldNames[field] || field}</b>)}</div>}
    {!!context?.changed_fields?.length && <div className="context-tags changed"><span>本轮修改</span>{context.changed_fields.map((field) => <b key={field}>{fieldNames[field] || field}</b>)}</div>}
    {message.dashboard_target && <a className="dashboard-link" href={dashboardUrl(message.dashboard_target)}>在看板中查看 <ArrowRight size={13} /></a>}
    {message.facts && <details><summary>查看查询事实 <ChevronDown size={13} /></summary><div className="facts-grid"><span>数据来源</span><b>sales_clean.sqlite</b>{Object.entries((message.facts.filters || {}) as Record<string, unknown>).filter(([, value]) => value).map(([key, value]) => <span className="fact-row" key={key}><span>{fieldNames[key] || key}</span><b>{String(value)}</b></span>)}</div></details>}
  </div>
}

export default function Assistant() {
  const [items, setItems] = useState<Conversation[]>([])
  const [active, setActive] = useState<Conversation | null>(null)
  const activeIdRef = useRef<string | null>(null)
  const [messages, setMessages] = useState<AssistantMessage[]>([])
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [configOpen, setConfigOpen] = useState(false)
  const [config, setConfig] = useState<AssistantConfig | null>(null)
  const [draft, setDraft] = useState<Partial<AssistantConfig>>({})
  const [testing, setTesting] = useState(false)
  const [testMessage, setTestMessage] = useState('')

  const refresh = async () => {
    const list = await assistantApi.conversations(); setItems(list)
    const current = activeIdRef.current ? list.find((item) => item.conversation_id === activeIdRef.current) : list[0]
    if (current && current.conversation_id !== activeIdRef.current) {
      activeIdRef.current = current.conversation_id
      setActive(current); setMessages(await assistantApi.messages(current.conversation_id))
    } else if (!current && !activeIdRef.current) {
      setActive(null); setMessages([])
    }
  }
  useEffect(() => { refresh().catch(() => setError('无法加载对话')) }, [])

  const create = async () => { const conversation = await assistantApi.createConversation(); activeIdRef.current = conversation.conversation_id; setItems((previous) => [conversation, ...previous]); setActive(conversation); setMessages([]) }
  const select = async (conversation: Conversation) => { activeIdRef.current = conversation.conversation_id; setActive(conversation); setMessages(await assistantApi.messages(conversation.conversation_id)) }
  const remove = async (conversation: Conversation) => {
    if (!window.confirm(`删除“${conversation.title}”？`)) return
    await assistantApi.deleteConversation(conversation.conversation_id)
    const next = items.filter((item) => item.conversation_id !== conversation.conversation_id); setItems(next)
    if (activeIdRef.current === conversation.conversation_id) { const replacement = next[0] || null; activeIdRef.current = replacement?.conversation_id || null; setActive(replacement); setMessages(replacement ? await assistantApi.messages(replacement.conversation_id) : []) }
  }
  const ask = async (value = question) => {
    const conversationId = activeIdRef.current
    if (!conversationId || !value.trim() || loading) return
    setQuestion(''); setLoading(true); setError('')
    const localMessageId = `local-${Date.now()}`
    setMessages((previous) => [...previous, { message_id: localMessageId, role: 'user', content: value, status: 'pending', created_at: new Date().toISOString() }])
    try {
      const result = await assistantApi.ask(conversationId, value)
      setMessages((previous) => [...previous.map((message) => message.message_id === localMessageId ? { ...message, status: 'sent' } : message), { ...result.message, context: result.context, query_plan: result.query_plan, dashboard_target: result.dashboard_target }])
      const list = await assistantApi.conversations(); setItems(list)
      const current = list.find((item) => item.conversation_id === conversationId); if (current) setActive(current)
    } catch { setMessages((previous) => previous.map((message) => message.message_id === localMessageId ? { ...message, status: 'error' } : message)); setError('问答请求失败，请检查后端服务') } finally { setLoading(false) }
  }
  const openConfig = async () => { const value = await assistantApi.config(); setConfig(value); setDraft(value); setTestMessage(''); setConfigOpen(true) }
  const saveConfig = async () => { if (!draft.provider || !draft.model || !draft.base_url) return; const value = await assistantApi.updateConfig({ provider: draft.provider, model: draft.model, base_url: draft.base_url, timeout_seconds: Number(draft.timeout_seconds || 30) }); setConfig(value); setDraft(value); setTestMessage('配置已保存') }
  const test = async () => { setTesting(true); setTestMessage('正在测试连接...'); try { const value = await assistantApi.testConfig(); setTestMessage(`连接成功 · ${value.latency_ms}ms`) } catch { setTestMessage('连接失败，系统仍可使用本地真实数据回答') } finally { setTesting(false) } }

  return <div className="assistant-shell">
    <header className="assistant-header"><div className="assistant-brand"><div className="assistant-logo"><Bot size={20} /></div><div><b>AI 数据问答</b><small>基于已清洗的门店销售数据</small></div></div><div className="assistant-actions"><span className="truth-badge"><Database size={13} />真实数据模式</span><button className="ghost-button" onClick={() => void openConfig()}><Settings size={16} />LLM 配置</button><a href="/" className="back-link">返回看板</a></div></header>
    <main className="assistant-layout"><aside className="conversation-sidebar"><button className="new-chat" onClick={() => void create()}><MessageSquarePlus size={17} />新建对话</button><div className="conversation-heading"><span>最近对话</span><MoreHorizontal size={16} /></div>{items.map((item) => <div className={`conversation-item ${active?.conversation_id === item.conversation_id ? 'active' : ''}`} key={item.conversation_id} onClick={() => void select(item)}><div><b>{item.title}</b><small>{item.message_count ? `${item.message_count} 条消息` : '空对话'}</small></div><button title="删除对话" onClick={(event) => { event.stopPropagation(); void remove(item) }}><Trash2 size={14} /></button></div>)}{!items.length && <div className="sidebar-empty">还没有对话<br />从一个问题开始</div>}</aside>
      <section className="chat-panel">{active ? <><div className="chat-title"><div><span className="panel-kicker">OPERATIONS COPILOT</span><h1>{active.title}</h1></div><span className="secure-note"><Check size={14} />数字来自查询结果</span></div><div className="message-list">{messages.length === 0 && <div className="welcome"><div className="welcome-icon"><Bot size={25} /></div><h2>今天想了解哪项经营表现？</h2><p>直接用自然语言提问，我会先查询真实数据，再给出答案。</p><div className="prompt-grid">{['牛肉poke 六月卖了多少钱？', '哪个品类的营业额最高？', '客单价最近是涨了还是跌了？'].map((prompt) => <button key={prompt} onClick={() => void ask(prompt)}>{prompt}<Send size={13} /></button>)}</div></div>}{messages.map((message) => <div className={`message-row ${message.role}`} key={message.message_id}><div className="message-avatar">{message.role === 'assistant' ? <Bot size={15} /> : '我'}</div><div className="message-body"><div className="message-bubble">{message.content}</div><AssistantAnswerMeta message={message} /></div></div>)}</div>{error && <div className="assistant-error">{error}</div>}<div className="composer"><div className="quick-prompts">{['六月营业额', 'Top 商品', '那五月呢？'].map((prompt) => <button key={prompt} onClick={() => setQuestion(prompt)}>{prompt}</button>)}</div><div className="composer-box"><textarea value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void ask() } }} placeholder="问问门店的经营数据..." maxLength={1000} /><button className="send-button" onClick={() => void ask()} disabled={loading || !question.trim()}><Send size={17} /></button></div><small>Enter 发送 · Shift + Enter 换行</small></div></> : <div className="no-conversation"><Bot size={30} /><h2>开始一个新的数据对话</h2><button className="new-chat" onClick={() => void create()}>新建对话</button></div>}</section>
    </main>
    {configOpen && <div className="config-overlay" onClick={() => setConfigOpen(false)}><div className="config-modal" onClick={(event) => event.stopPropagation()}><div className="config-modal-head"><div><span className="panel-kicker">MODEL CONNECTION</span><h2>LLM 配置</h2></div><button className="icon-close" onClick={() => setConfigOpen(false)}><X size={18} /></button></div><p className="config-safe">API Key 由后端环境变量管理，前端只显示配置状态，不会保存或发送密钥。</p><label>Provider<select value={draft.provider || ''} onChange={(event) => setDraft({ ...draft, provider: event.target.value })}><option value="deepseek">DeepSeek</option><option value="openai-compatible">OpenAI Compatible</option></select></label><label>Model<input value={draft.model || ''} onChange={(event) => setDraft({ ...draft, model: event.target.value })} placeholder="deepseek-chat" /></label><label>Base URL<input value={draft.base_url || ''} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} /></label><label>Timeout（秒）<input type="number" min="5" max="120" value={draft.timeout_seconds || 30} onChange={(event) => setDraft({ ...draft, timeout_seconds: Number(event.target.value) })} /></label><div className="key-state"><span>API Key 状态</span><b>{config?.has_api_key ? `已配置 · ${config.api_key_masked}` : '未配置 · 使用本地规则回答'}</b></div><div className="config-actions"><button className="ghost-button" onClick={() => void test()} disabled={testing}>{testing ? '测试中...' : '测试连接'}</button><button className="primary-config" onClick={() => void saveConfig()}>保存配置</button></div>{testMessage && <div className="test-message">{testMessage}</div>}</div></div>}
  </div>
}
