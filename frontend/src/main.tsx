function AutonomousCopilot({ onAsk, onSubmit, query, setQuery }: { onAsk: (c?: string) => void; onSubmit: (question: string) => void; query: string; setQuery: (s: string) => void }) { return <><div className="welcome"><div><span className="eyebrow">WEDNESDAY, AUGUST 26, 2026</span><h2>Good afternoon, Kenzie</h2><p>What would you like to understand today?</p></div><div className="online"><i className="live-dot" /> NEXUS-4-OMNI <span>online</span></div></div><div className="copilot-input"><div className="input-row"><Sparkles size={20} /><input value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') onSubmit(query) }} placeholder="Why did our revenue decrease this month?" /><button className="primary" onClick={() => onSubmit(query)}><Send size={15} /> Ask AI</button></div><p className="agent-hint">Nexus automatically selects the right tools for your question.</p><div className="input-tools"><button type="button" onClick={() => document.getElementById('document-upload')?.click()}><FileText size={14} /> Documents</button></div></div><Panel title="Suggested Actions" eyebrow="START WITH A QUESTION" className="suggestions"><div className="suggestion-grid">{[['Autonomous Analysis', 'Give me a complete business health report.'], ['Forecast', 'Forecast revenue for the next 14 days.'], ['Risk Analysis', 'What are the biggest risks to our business?'], ['External Intelligence', 'What recent news could affect our business?']].map(([a, b]) => <button key={a} onClick={() => setQuery(b)}><span>{a}</span><b>{b}</b><ArrowRight size={15} /></button>)}</div></Panel><Panel title="Ask Nexus for live analysis" eyebrow="GROUNDED IN YOUR CONNECTED TOOLS"><p className="muted-copy">Ask a natural-language question and Nexus will retrieve only the evidence needed from MCP.</p></Panel></> }
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import { Activity, AlertTriangle, ArrowRight, Bell, Check, Database, Download, FileText, Globe2, Image as ImageIcon, LineChart, Menu, Play, Plus, Search, Send, Settings, Sparkles, Terminal, Upload, Workflow, X, Zap } from 'lucide-react'
import './styles.css'
import { callMcp, enableMcpLogging, getMcpSnapshot, unwrapMcp } from './lib/mcp'
import { runAgent, type ActivityEvent, type AgentResult, type ToolStep } from './lib/orchestrator'
import type { ChatSource } from './lib/responseNormalizer'

type Route = 'copilot' | 'command-center' | 'ai-insights' | 'forecast' | 'news-intelligence' | 'documents' | 'data-explorer' | 'pipelines' | 'mcp-tools' | 'agent-activity'
type DocumentAttachment = { id: string; document_id: string; filename: string; type: string; size: number; object_key: string; status: 'uploading' | 'ingesting' | 'ready' | 'failed'; progress?: number; error?: string }
type ImageAttachment = { id: string; filename: string; type: string; size: number; object_key: string; status: 'uploading' | 'ready' | 'failed'; content_base64?: string; error?: string }
type ChatMessage = { id: string; role: 'user' | 'ai'; text: string; result?: AgentResult; status?: 'processing' | 'completed' | 'partial' | 'failed'; activity?: ActivityEvent[]; attachments?: DocumentAttachment[]; imageAttachments?: ImageAttachment[] }
type ForecastStage = 'idle' | 'uploading' | 'bronze' | 'silver' | 'gold' | 'training' | 'forecasting' | 'completed' | 'failed'
type ForecastPoint = { date: string; actual?: number | null; forecast?: number | null }
type ForecastResult = { series: ForecastPoint[]; metrics: Record<string, number | string | null>; rows: Record<string, unknown>[] }
type NewsTopic = { topic: string; article_count: number; positive: number; negative: number; neutral: number; last_updated?: string }
type NewsArticle = { artifact_id?: string; topic?: string; title?: string; summary?: string; content?: string; source?: string; url?: string; published_at?: string; collected_at?: string; category?: string; impact?: string; entities?: string[]; keywords?: string[] }
type Icon = typeof Activity
const nav: { label: string; items: [Route, string, Icon][] }[] = [
  { label: 'AI WORKSPACE', items: [['copilot', 'Nexus', Sparkles]] },
  { label: 'INTELLIGENCE', items: [['forecast', 'Forecast', LineChart], ['news-intelligence', 'News Intelligence', Globe2]] },
  { label: 'DATA', items: [['data-explorer', 'Data Explorer', Database]] },
  { label: 'AI SYSTEM', items: [['agent-activity', 'Agent Activity', Activity], ['mcp-tools', 'MCP Tools', Terminal]] },
]
const API_BASE_URL = import.meta.env.VITE_FORECAST_API_URL || ''
const MCP_URL = import.meta.env.VITE_MCP_URL || 'http://localhost:8000'
const news = ['Government announces new retail economic policy changes', 'Consumer spending trends shift towards luxury and tech in Q3', 'Competitor expansion registered in Southern corridors']

function App() {
  const [route, setRoute] = useState<Route>('copilot')
  const [collapsed, setCollapsed] = useState(false)
  const [modal, setModal] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [toast, setToast] = useState('')
  const [backendStatus, setBackendStatus] = useState<'checking' | 'online' | 'offline'>('checking')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [agentLoading, setAgentLoading] = useState(false)
  const [responseLength, setResponseLength] = useState<'short' | 'medium' | 'long'>('medium')
  const [pageContext, setPageContext] = useState('')
  const [uploadedDocuments, setUploadedDocuments] = useState<Array<{ name: string; object_key?: string; uploadedAt: string }>>([])
  const [composerAttachments, setComposerAttachments] = useState<DocumentAttachment[]>([])
  const [composerImages, setComposerImages] = useState<ImageAttachment[]>([])
  const title = nav.flatMap(group => group.items).find(item => item[0] === route)?.[1] || 'Nexus'
  const ask = (context = title) => { setRoute('copilot'); setPageContext(context); setToast(`Nexus context loaded: ${context}`); window.setTimeout(() => setToast(''), 2400) }
  const handleDocumentUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || [])
    if (!files.length) return
    event.target.value = ''
    
    for (const file of files) {
      const attachmentId = crypto.randomUUID()
      const attachment: DocumentAttachment = {
        id: attachmentId,
        document_id: '',
        filename: file.name,
        type: file.type || 'application/pdf',
        size: file.size,
        object_key: '',
        status: 'uploading',
        progress: 0,
      }
      setComposerAttachments(current => [...current, attachment])
      setToast(`Uploading ${file.name}...`)

      try {
        // Step 1: Read file
        const content = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(String(reader.result || '').split(',')[1] || '')
          reader.onerror = () => reject(new Error(`Unable to read ${file.name}`))
          reader.readAsDataURL(file)
        })

        // Step 2: Upload to MinIO
        const uploadPayload = unwrapMcp<{ status?: string; object_key?: string; bucket?: string; message?: string }>(
          await callMcp('upload_document_to_minio', {
            filename: file.name,
            content_base64: content,
            content_type: file.type || 'application/octet-stream',
          })
        )
        
        if (uploadPayload?.status !== 'success' || !uploadPayload.object_key) {
          throw new Error(uploadPayload?.message || 'Document upload failed')
        }

        // Update attachment with MinIO info
        const objectKey = uploadPayload.object_key as string
        setComposerAttachments(current =>
          current.map(a =>
            a.id === attachmentId
              ? { ...a, object_key: objectKey, status: 'ingesting', progress: 50 }
              : a
          )
        )
        setToast(`Stored in MinIO. Indexing ${file.name}...`)

        // Step 3: Ingest into RAG
        const ingestionPayload = unwrapMcp<{ status?: string; message?: string }>(
          await callMcp('ingest_rag_documents')
        )
        
        if (ingestionPayload?.status !== 'success') {
          throw new Error(ingestionPayload?.message || 'Document indexing failed')
        }

        // Step 4: Mark as ready
        setComposerAttachments(current =>
          current.map(a =>
            a.id === attachmentId
              ? {
                  ...a,
                  document_id: attachmentId, // Use attachment ID as document_id for now
                  status: 'ready',
                  progress: 100,
                }
              : a
          )
        )

        // Update uploaded documents for later reference
        setUploadedDocuments(current => [
          ...current,
          {
            name: file.name,
            object_key: uploadPayload.object_key,
            uploadedAt: new Date().toISOString(),
          },
        ])

        setToast(`${file.name} ready to use`)
        window.setTimeout(() => setToast(''), 2400)
      } catch (error) {
        const errorMsg = error instanceof Error ? error.message : 'Unknown error'
        setComposerAttachments(current =>
          current.map(a =>
            a.id === attachmentId ? { ...a, status: 'failed', error: errorMsg } : a
          )
        )
        setToast(`Failed to upload ${file.name}: ${errorMsg}`)
        window.setTimeout(() => setToast(''), 4000)
      }
    }
  }
  const handleImageUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || [])
    if (!files.length) return
    event.target.value = ''
    
    const acceptedTypes = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
    
    for (const file of files) {
      if (!acceptedTypes.includes(file.type)) {
        setToast(`Invalid file type: ${file.name}. Accepted: JPG, PNG, WEBP`)
        window.setTimeout(() => setToast(''), 3000)
        continue
      }
      
      const imageId = crypto.randomUUID()
      const image: ImageAttachment = {
        id: imageId,
        filename: file.name,
        type: file.type || 'image/jpeg',
        size: file.size,
        object_key: '',
        status: 'uploading',
      }
      setComposerImages(current => [...current, image])
      console.info('[IMAGE_UPLOAD]', {
        filename: file.name,
        mime_type: file.type || 'image/jpeg',
        size: file.size,
        upload_success: false,
      })
      setToast(`Ready to send: ${file.name}`)

      try {
        const content = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(String(reader.result || '').split(',')[1] || '')
          reader.onerror = () => reject(new Error(`Unable to read ${file.name}`))
          reader.readAsDataURL(file)
        })

        setComposerImages(current =>
          current.map(img =>
            img.id === imageId
              ? { ...img, content_base64: content, status: 'ready' }
              : img
          )
        )

        console.info('[IMAGE_UPLOAD]', {
          filename: file.name,
          mime_type: file.type || 'image/jpeg',
          size: file.size,
          upload_success: true,
        })
      } catch (error) {
        const errorMsg = error instanceof Error ? error.message : 'Unknown error'
        setComposerImages(current =>
          current.map(img =>
            img.id === imageId ? { ...img, status: 'failed', error: errorMsg } : img
          )
        )
        console.error('[IMAGE_UPLOAD]', {
          filename: file.name,
          mime_type: file.type || 'image/jpeg',
          size: file.size,
          upload_success: false,
          error: errorMsg,
        })
        setToast(`Failed to load ${file.name}: ${errorMsg}`)
        window.setTimeout(() => setToast(''), 4000)
      }
    }
  }
  const submitQuestion = async (question: string) => {
    if (!question.trim() || agentLoading) return
    console.info('[CHAT] question =', question)
    const assistantId = crypto.randomUUID()
    const userMessageId = crypto.randomUUID()
    const selectedImages = [...composerImages]
    const selectedDocuments = [...composerAttachments]
    
    const userMessage: ChatMessage = {
      id: userMessageId,
      role: 'user',
      text: question,
      attachments: selectedDocuments.length > 0 ? [...selectedDocuments] : undefined,
      imageAttachments: selectedImages.length > 0 ? [...selectedImages] : undefined,
    }
    
    setMessages(current => [
      ...current,
      userMessage,
      { id: assistantId, role: 'ai', text: '', status: 'processing', activity: [{ stage: 'understanding', status: 'running', message: 'Understanding the question' }] }
    ])
    
    setComposerAttachments([])
    setComposerImages([])
    setAgentLoading(true)
    console.info('[CHAT_PAYLOAD]', {
      image_count: selectedImages.length,
      image_payload_present: selectedImages.some(img => Boolean(img.content_base64)),
      image_urls: selectedImages.map(img => img.content_base64 ? `data:${img.type};base64` : null).filter(Boolean),
      mime_types: selectedImages.map(img => img.type),
    })
    
    try {
      const conversationContext = messages.slice(-6).map(message => `${message.role === 'user' ? 'User' : 'Nexus AI'}: ${message.text}`).join('\n')
      const contextualQuestion = conversationContext ? `${conversationContext}\nUser: ${question}` : question
      const result = await runAgent(
        contextualQuestion,
        pageContext || title,
        event => {
          console.info('[CHAT STATE] activity', event)
          setMessages(current => current.map(message => {
            if (message.id !== assistantId) return message
            const currentActivity = [...(message.activity || [])]
            const existing = currentActivity.findIndex(item => (item.id || item.stage) === (event.id || event.stage))
            if (existing >= 0) {
              const previous = currentActivity[existing]
              if ((previous.status === 'completed' || previous.status === 'failed') && event.status !== previous.status) return message
              currentActivity[existing] = event
            } else currentActivity.push(event)
            return { ...message, activity: currentActivity }
          }))
        },
        responseLength,
        selectedImages,
        selectedDocuments,
      )
      console.info('[CHAT] response =', result)
      const responseText = result.status === 'failed'
        ? `Nexus request failed at ${result.error?.stage || 'unknown stage'}: ${result.error?.code || 'BACKEND_ERROR'}${result.error?.message ? ` - ${result.error.message}` : ''}`
        : result.answer
      setMessages(current => current.map(message => message.id === assistantId ? { ...message, text: responseText, result, status: result.status } : message))
    } catch (error) {
      setMessages(current => current.map(message => message.id === assistantId ? { ...message, text: `Nexus tidak dapat menyelesaikan analisis: ${error instanceof Error ? error.message : 'backend error'}`, status: 'failed', activity: [{ stage: 'understanding', status: 'failed', message: 'Understanding the question', error: error instanceof Error ? error.message : 'MCP request failed' }] } : message))
    } finally { setAgentLoading(false); console.info('[CHAT STATE] loading', false); setQuery('') }
  }
  useEffect(() => {
    const checkBackends = async () => {
      try {
        await enableMcpLogging()
        const mcpCheck = await getMcpSnapshot()
        const forecastCheck = await fetch(`${API_BASE_URL}/health`, { cache: 'no-store' })
        if (mcpCheck && forecastCheck.ok) {
          setBackendStatus('online')
        } else {
          setBackendStatus('offline')
        }
      } catch {
        setBackendStatus('offline')
      }
    }
    checkBackends()
  }, [])
  useEffect(() => {
    const handleBackendAction = async (event: MouseEvent) => {
      const target = (event.target as HTMLElement).closest('button')
      const label = target?.textContent?.trim() || ''
      if (!target) return
      let tool = ''
      let args: Record<string, unknown> = {}
      if (target.closest('.drawer-input') || label.includes('Ask AI')) return
      else if (label.includes('Analyze Document')) {
        const input = document.querySelector('#document-upload') as HTMLInputElement | null
        const files = input?.files ? Array.from(input.files) : []
        if (!files.length) { setToast('Pilih 3 file halaman dokumen terlebih dahulu.'); return }
        const contents = await Promise.all(files.map(file => new Promise<string>((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(String(reader.result).split(',')[1] || '')
          reader.onerror = () => reject(new Error(`Tidak dapat membaca ${file.name}`))
          reader.readAsDataURL(file)
        })))
        tool = 'upload_document_pages'; args = { filenames: files.map(file => file.name), contents_base64: contents }
      }
      else if (label.includes('Retrain Model')) { tool = 'get_forecasting_preview'; args = { limit: 14 } }
      else if (label.includes('Index RAG')) { tool = 'ingest_rag_documents' }
      else if (label.includes('Test Tool')) { tool = 'search_rag'; args = { query: 'top selling products', top_k: 5 } }
      else if (label.includes('Run pipeline')) { tool = 'run_dag'; args = { dag_id: 'mobile_sales_medallion_etl' } }
      else return
      target.setAttribute('aria-busy', 'true')
      try {
        const result = unwrapMcp(await callMcp(tool, args))
      } catch (cause) { setToast(`${tool} gagal: ${cause instanceof Error ? cause.message : 'backend error'}`) }
      finally { target.removeAttribute('aria-busy'); window.setTimeout(() => setToast(''), 4200) }
    }
    document.addEventListener('click', handleBackendAction)
    return () => document.removeEventListener('click', handleBackendAction)
  }, [])
  return <div className={`app ${collapsed ? 'collapsed' : ''}`}><input id="document-upload" type="file" accept=".pdf,.doc,.docx,.txt,.md,.csv,.json" multiple onChange={handleDocumentUpload} />
    <input id="image-upload" type="file" accept="image/jpeg,image/jpg,image/png,image/webp" onChange={handleImageUpload} />
    <aside className="sidebar"><div className="brand"><div className="logo">N</div><div><b>NEXUS<span> AI</span></b><small>BUSINESS INTELLIGENCE</small></div></div><button className="collapse" onClick={() => setCollapsed(!collapsed)}>{collapsed ? '>' : '<'}</button><nav>{nav.map(group => <div className="nav-group" key={group.label}><small>{group.label}</small>{group.items.map(([id, label, I]) => <button className={route === id ? 'nav-item active' : 'nav-item'} key={id} onClick={() => setRoute(id)} title={label}><I size={16} /><span>{label}</span>{id === 'agent-activity' && <i className="live-dot" />}</button>)}</div>)}</nav><div className="sidebar-footer"><div className="operational"><i className="live-dot" /><span>All systems operational</span></div><div className="profile"><div className="avatar">K</div><span><b>Kenzie</b><small>Admin</small></span><Settings size={15} /></div></div></aside>
    <main><header className="topbar"><button className="mobile-menu" onClick={() => setCollapsed(!collapsed)}><Menu size={18} /></button><div className="crumb"><span>NEXUS AI</span><ArrowRight size={13} /><b>{title}</b></div><div className="top-right"><span className="model">MODEL: NEXUS-4-OMNI</span><span className="backend-status"><i className={backendStatus === 'online' ? 'live-dot' : ''} /> {backendStatus === 'checking' ? 'Connecting MCP...' : backendStatus === 'online' ? 'Backend online' : 'Backend offline'}</span><button className="icon-button"><Bell size={17} /><i /></button><div className="avatar">K</div></div></header><div className="content">{route !== 'copilot' && <PageHeader title={title} route={route} onAction={() => route === 'command-center' ? setModal('report') : ask()} />}{route === 'copilot' && <ConversationWorkspace context={pageContext} messages={messages} loading={agentLoading} onSubmit={submitQuestion} query={query} setQuery={setQuery} responseLength={responseLength} setResponseLength={setResponseLength} composerAttachments={composerAttachments} composerImages={composerImages} onRemoveAttachment={(id) => setComposerAttachments(current => current.filter(a => a.id !== id))} onRemoveImage={(id) => setComposerImages(current => current.filter(img => img.id !== id))} setComposerAttachments={setComposerAttachments} setComposerImages={setComposerImages} />} {route === 'command-center' && <CommandCenter onAsk={ask} />} {route === 'ai-insights' && <Insights onAsk={ask} />} {route === 'forecast' && <Forecast onAsk={ask} />} {route === 'news-intelligence' && <News onAsk={ask} onOpen={() => setModal('news')} />} {route === 'documents' && <Documents onAsk={ask} uploadedDocuments={uploadedDocuments} />} {route === 'data-explorer' && <DataExplorer onAsk={ask} />} {route === 'pipelines' && <Pipelines onOpen={name => setModal(name)} />} {route === 'mcp-tools' && <Mcp onOpen={name => setModal(name)} onActivity={() => setRoute('agent-activity')} />} {route === 'agent-activity' && <AgentActivity onOpen={name => setModal(name)} />}</div></main>{modal && <Modal type={modal} onClose={() => setModal(null)} onAsk={ask} />} {toast && <div className="toast"><Check size={14} />{toast}</div>}</div>
}

function PageHeader({ title, route, onAction }: { title: string; route: Route; onAction: () => void }) { const descriptions: Record<Route, string> = { copilot: 'Your AI operating layer for asking, analyzing, and deciding.', 'command-center': 'Real-time overview of your business intelligence.', 'ai-insights': 'Autonomous intelligence and anomalies discovered across your operations.', forecast: 'Autonomous predictive modeling for core business metrics.', 'news-intelligence': 'Real-time external threat and opportunity scanning by web agents.', documents: 'Extract structured insights from invoices, agreements, and business documents.', 'data-explorer': 'Query, filter, and inspect structured core pipeline data.', pipelines: 'Manage transactional workflows from raw inputs into Gold metrics.', 'mcp-tools': 'Manage and test Model Context Protocol integrations for agents.', 'agent-activity': 'Real-time execution traces from active autonomous agents.' }; return <div className="page-header"><div><span className="eyebrow">{route.replaceAll('-', ' ').toUpperCase()}</span><h1>{title}</h1><p>{descriptions[route]}</p></div>{route === 'command-center' ? <button className="primary" onClick={onAction}><FileText size={15} /> Generate Executive Report</button> : route !== 'copilot' && <button className="secondary" onClick={onAction}><Sparkles size={15} /> Ask Nexus</button>}</div> }
function Panel({ title, eyebrow, children, className = '' }: { title: string; eyebrow?: string; children: ReactNode; className?: string }) { return <section className={`panel ${className}`}><div className="panel-head"><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h2>{title}</h2></div></div>{children}</section> }
function ConversationWorkspace({ context, messages, loading, onSubmit, query, setQuery, responseLength, setResponseLength, composerAttachments, composerImages, onRemoveAttachment, onRemoveImage, setComposerAttachments, setComposerImages }: { context: string; messages: ChatMessage[]; loading: boolean; onSubmit: (question: string) => void; query: string; setQuery: (value: string) => void; responseLength: 'short' | 'medium' | 'long'; setResponseLength: (value: 'short' | 'medium' | 'long') => void; composerAttachments: DocumentAttachment[]; composerImages: ImageAttachment[]; onRemoveAttachment: (id: string) => void; onRemoveImage: (id: string) => void; setComposerAttachments?: (value: DocumentAttachment[]) => void; setComposerImages?: (value: ImageAttachment[]) => void }) {
  const endRef = useRef<HTMLDivElement>(null)
  const send = (value = query) => { if (value.trim() && !loading) onSubmit(value.trim()) }
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [messages, loading])
  const suggestions = [['Autonomous Analysis', 'Give me a complete business health report.'], ['Forecast', 'Forecast revenue for the next 14 days.'], ['Market Analysis', "What's happening in the market today?"], ['Risk Analysis', 'What are the biggest risks to our business?'], ['Document Analysis', 'Analyze the latest uploaded document.']]
  return <section className="conversation-workspace">
    <header className="chat-header"><div><span className="eyebrow">NEXUS AI</span><h1>Nexus AI</h1><p>AI operating layer for asking, analyzing, and deciding.</p></div><div className="chat-status"><i className="live-dot" /> Online <span>MODEL: NEXUS-4-OMNI</span></div></header>
    {context && context !== 'Nexus' && <div className="conversation-context"><span>Working context</span><b>{context}</b></div>}
    <div className="conversation-scroll">
      {!messages.length && !loading && <div className="empty-chat"><Sparkles size={25} /><h2>What would you like to understand today?</h2><p>Ask naturally. Nexus will choose the right connected tools and show the evidence behind its answer.</p><div className="chat-suggestions">{suggestions.map(([label, prompt]) => <button key={label} onClick={() => send(prompt)}><span>{label}</span><b>{prompt}</b><ArrowRight size={15} /></button>)}</div></div>}
      {messages.map((message, index) => <article className={`conversation-message ${message.role}`} key={message.id}>
        <div className="message-author">{message.role === 'ai' ? <><Sparkles size={14} /> Nexus AI</> : 'You'}</div>
        {message.role === 'user' && message.attachments && message.attachments.length > 0 && (
          <div style={{ display: 'grid', gap: '8px', marginBottom: '12px' }}>
            {message.attachments.map(att => (
              <div key={att.id} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 12px', border: '1px solid var(--line)', borderRadius: '6px', background: 'rgba(82, 216, 232, 0.05)', fontSize: '12px' }}>
                <FileText size={14} style={{ color: 'var(--cyan)', flexShrink: 0 }} />
                <span style={{ flex: 1 }}>{att.filename}</span>
                <span style={{ color: 'var(--green)', fontSize: '10px' }}>✓</span>
              </div>
            ))}
          </div>
        )}
        {message.role === 'user' && message.imageAttachments && message.imageAttachments.length > 0 && (
          <div style={{ display: 'grid', gap: '8px', marginBottom: '12px' }}>
            {message.imageAttachments.map(att => (
              <div key={att.id} style={{ display: 'grid', gap: '8px', padding: '10px 12px', border: '1px solid var(--line)', borderRadius: '8px', background: 'rgba(255,255,255,0.02)' }}>
                {att.content_base64 && <img src={`data:${att.type};base64,${att.content_base64}`} alt={att.filename} style={{ width: 120, height: 90, objectFit: 'cover', borderRadius: 6, border: '1px solid var(--line)' }} />}
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px' }}>
                  <ImageIcon size={14} style={{ color: 'var(--cyan)', flexShrink: 0 }} />
                  <span style={{ flex: 1 }}>{att.filename}</span>
                  <span style={{ color: 'var(--green)', fontSize: '10px' }}>✓</span>
                </div>
              </div>
            ))}
          </div>
        )}
        {message.role === 'ai' ? message.text ? <ReactMarkdown rehypePlugins={[rehypeSanitize]}>{message.text}</ReactMarkdown> : <p>Processing your request...</p> : <p>{message.text}</p>}
        {message.role === 'ai' && <ActivityTrace activity={message.activity || []} steps={message.result?.steps || []} sources={message.result?.sources || []} status={message.status} onRetry={() => {
          const prevUserMessage = messages[index - 1];
          if (prevUserMessage && prevUserMessage.role === 'user') {
            if (setComposerAttachments && prevUserMessage.attachments) setComposerAttachments(prevUserMessage.attachments);
            if (setComposerImages && prevUserMessage.imageAttachments) setComposerImages(prevUserMessage.imageAttachments);
          }
          send(messages[index - 1]?.text || '');
        }} />}
        {message.role === 'ai' && index === messages.length - 1 && message.status === 'completed' && <div className="follow-ups"><button onClick={() => send('Why did this happen?')}>Why did this happen?</button><button onClick={() => send('Show sources')}>Show sources</button><button onClick={() => send('Forecast next 14 days')}>Forecast next 14 days</button></div>}
      </article>)}
      <div ref={endRef} />
    </div>
    <div className="conversation-composer">
      {(composerAttachments.length > 0 || composerImages.length > 0) && (
        <div className="composer-attachments">
          {composerAttachments.map(att => (
            <div key={att.id} className={`attachment-card ${att.status}`}>
              <div className="attachment-header">
                <div className="attachment-icon"><FileText size={14} /></div>
                <div className="attachment-info">
                  <div className="attachment-name">{att.filename}</div>
                  <div className="attachment-meta">{att.type} · {(att.size / 1024).toFixed(1)} KB</div>
                </div>
                {att.status !== 'failed' && (
                  <button className="attachment-remove" onClick={() => onRemoveAttachment(att.id)} title="Remove attachment"><X size={14} /></button>
                )}
              </div>
              {att.status === 'uploading' && (
                <div className="attachment-progress">
                  <div className="progress-bar"><div className="progress-fill" style={{ width: `${att.progress || 25}%` }} /></div>
                  <span className="progress-text">Uploading...</span>
                </div>
              )}
              {att.status === 'ingesting' && (
                <div className="attachment-progress">
                  <div className="progress-bar"><div className="progress-fill" style={{ width: `${att.progress || 50}%` }} /></div>
                  <span className="progress-text">Indexing document...</span>
                </div>
              )}
              {att.status === 'ready' && (
                <div className="attachment-status ready"><Check size={14} /> Ready to use</div>
              )}
              {att.status === 'failed' && (
                <div className="attachment-status failed"><AlertTriangle size={14} /> {att.error || 'Upload failed'}</div>
              )}
            </div>
          ))}
          {composerImages.map(att => (
            <div key={att.id} className={`attachment-card ${att.status}`}>
              <div className="attachment-header">
                <div className="attachment-icon"><ImageIcon size={14} /></div>
                <div className="attachment-info">
                  <div className="attachment-name">{att.filename}</div>
                  <div className="attachment-meta">{att.type} · {(att.size / 1024).toFixed(1)} KB</div>
                </div>
                {att.status !== 'failed' && (
                  <button className="attachment-remove" onClick={() => onRemoveImage(att.id)} title="Remove image"><X size={14} /></button>
                )}
              </div>
              {att.content_base64 && (
                <img src={`data:${att.type};base64,${att.content_base64}`} alt={att.filename} style={{ width: 120, height: 90, objectFit: 'cover', borderRadius: 6, border: '1px solid var(--line)' }} />
              )}
              {att.status === 'ready' && (
                <div className="attachment-status ready"><Check size={14} /> Image ready</div>
              )}
              {att.status === 'failed' && (
                <div className="attachment-status failed"><AlertTriangle size={14} /> {att.error || 'Image failed'}</div>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="composer-box">
        <textarea value={query} rows={1} onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send() } }} placeholder="Ask Nexus anything..." />
        <button className="primary composer-send" onClick={() => send()} disabled={loading || !query.trim()} aria-label="Send message"><Send size={16} /></button>
        <div className="composer-tools">
          <button type="button" onClick={() => document.getElementById('document-upload')?.click()}><FileText size={14} /> Documents</button>
          <button type="button" onClick={() => document.getElementById('image-upload')?.click()}><ImageIcon size={14} /> Image</button>
          <label className="response-length">Response length <select value={responseLength} onChange={event => setResponseLength(event.target.value as 'short' | 'medium' | 'long')}><option value="short">Short</option><option value="medium">Medium</option><option value="long">Long</option></select></label>
          <span>Enter to send</span>
        </div>
      </div>
    </div>
  </section>
}
function Copilot({ onAsk, query, setQuery, onNavigate }: { onAsk: (c?: string) => void; query: string; setQuery: (s: string) => void; onNavigate: (r: Route) => void }) { return <><div className="welcome"><div><span className="eyebrow">NEXUS AI WORKSPACE</span><h2>What should we investigate?</h2><p>Ask a question and the agent will show only evidence it actually collected.</p></div><div className="online"><i className="live-dot" /> NEXUS-4-OMNI <span>online</span></div></div><div className="copilot-input"><div className="input-row"><Sparkles size={20} /><input value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && query.trim()) onAsk(query) }} placeholder="Kenapa Bitcoin turun?" /><button className="primary" onClick={() => query.trim() && onAsk(query)}><Send size={15} /> Ask AI</button></div><div className="input-tools"><button type="button" onClick={() => document.getElementById('document-upload')?.click()}><FileText size={14} /> Documents</button></div></div><Panel title="Suggested Actions" eyebrow="START WITH A QUESTION" className="suggestions"><div className="suggestion-grid">{[['Autonomous Analysis', 'Give me a complete business health report.'], ['Forecast', 'Forecast revenue for the next 14 days.'], ['Risk Analysis', 'What are the biggest risks to our business?'], ['External Intelligence', 'Berita terbaru Bitcoin hari ini?']].map(([a, b]) => <button onClick={() => setQuery(b)}><span>{a}</span><b>{b}</b><ArrowRight size={15} /></button>)}</div></Panel><div className="recommendation"><div><span className="eyebrow">EVIDENCE-FIRST WORKSPACE</span><h3>Results, activity, and sources appear here after a real request.</h3><div className="evidence"><span>Computer Use</span><span>MinIO</span><span>RAG</span></div></div><div className="confidence"><button className="secondary" onClick={() => onNavigate('ai-insights')}>View Insights</button></div></div></> }
function CommandCenter({ onAsk }: { onAsk: (c?: string) => void }) {
  const stages = [
    { label: 'Bronze', status: 'success', text: 'Original CSV ingested', meta: 'Raw rows retained' },
    { label: 'Silver', status: 'success', text: 'Quality checks complete', meta: 'Missing values and duplicates handled' },
    { label: 'Gold', status: 'pending', text: 'Feature engineering ready', meta: 'Analytics dataset prepared' },
  ]

  return (
    <div className="command-shell">
      <div className="command-grid">
        <Panel title="Forecasting" eyebrow="CSV upload → backend processing → forecast results" className="forecasting-module">
          <div className="module-card compact">
            <div className="module-header">
              <span className="eyebrow">AUTONOMOUS PIPELINE</span>
              <span className="processing-pill neutral">Ready to upload</span>
            </div>
            <div className="module-body">
              <div className="module-stat-row">
                <span>Upload</span>
                <b>CSV file</b>
              </div>
              <div className="module-stat-row">
                <span>Processing</span>
                <b>Backend only</b>
              </div>
              <div className="module-stat-row">
                <span>Output</span>
                <b>Forecast + metrics</b>
              </div>
            </div>
          </div>
          <button className="primary" onClick={() => onAsk('Forecasting')}>
            <Play size={14} /> Open Forecasting
          </button>
        </Panel>

        <Panel title="Medallion Architecture" eyebrow="🥉 Bronze → 🥈 Silver → 🥇 Gold" className="medallion-module">
          <div className="medallion-flow">
            {stages.map((stage) => (
              <div key={stage.label} className={`medallion-step ${stage.status}`}>
                <span className="medallion-badge">{stage.label}</span>
                <strong>{stage.text}</strong>
                <small>{stage.meta}</small>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  )
}

function Insights({ onAsk }: { onAsk: (c?: string) => void }) { return <Panel title="Discovered signals" eyebrow="5 ACTIVE INSIGHTS" className="insights">{[['Revenue Anomaly', 'HIGH SEVERITY', 'Revenue dropped 8.4% compared with expected performance.', 'red'], ['Product Opportunity', 'MEDIUM SEVERITY', 'Product C is showing unusually strong growth (+18% above seasonal forecast).', 'amber'], ['Market Signal', 'MEDIUM SEVERITY', 'New external news may affect demand in the next 2 weeks.', 'amber'], ['Customer Retention Alert', 'LOW SEVERITY', 'Churn metrics increased 0.8% MoM.', 'green'], ['Supply Chain Signal', 'LOW SEVERITY', 'Expected shipping volumes decreased.', 'green']].map(x => <div className="insight"><div className={`signal ${x[3]}`}><AlertTriangle size={17} /></div><section><span className={`badge ${x[3]}`}>{x[1]}</span><h3>{x[0]}</h3><p>{x[2]}</p><button className="text-button" onClick={() => onAsk(x[0])}>Why?</button><button className="text-button">Evidence</button><button className="text-button" onClick={() => onAsk(x[0])}>Ask AI</button></section><ArrowRight size={16} /></div>)}</Panel> }

function Forecast({ onAsk }: { onAsk: (c?: string) => void }) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [fileInfo, setFileInfo] = useState<{ name: string; size: string; rows?: number; columns: string[] } | null>(null)
  const [columns, setColumns] = useState<string[]>([])
  const [dateColumn, setDateColumn] = useState('')
  const [targetColumn, setTargetColumn] = useState('')
  const [horizon, setHorizon] = useState(14)
  const [jobId, setJobId] = useState('')
  const [stage, setStage] = useState<ForecastStage>('idle')
  const [processing, setProcessing] = useState(false)
  const [backendAvailable, setBackendAvailable] = useState<boolean | null>(null)
  const [error, setError] = useState('')
  const [result, setResult] = useState<ForecastResult | null>(null)
  const [downloadUrl, setDownloadUrl] = useState('')
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    let active = true
    const checkBackend = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/health`, { cache: 'no-store', signal: AbortSignal.timeout(5000) })
        const payload = await response.json().catch(() => ({}))
        if (!active) return
        if (response.ok && payload?.status === 'ok') {
          setBackendAvailable(true)
          setError('')
          return
        }
        const message = `Backend unavailable: ${response.status || 'unknown status'}`
        console.error('[FORECAST_BACKEND_CHECK]', {
          url: `${API_BASE_URL}/health`,
          status: response.status,
          responseBody: payload,
          error: message,
        })
        setBackendAvailable(false)
        setError('Backend unavailable')
      } catch (cause) {
        if (!active) return
        const message = cause instanceof Error ? cause.message : String(cause)
        console.error('[FORECAST_BACKEND_CHECK]', {
          url: `${API_BASE_URL}/health`,
          status: 'network_error',
          responseBody: '',
          error: message,
        })
        setBackendAvailable(false)
        setError('Backend unavailable')
      }
    }

    checkBackend()
    return () => { active = false }
  }, [])

  const pipelineStages: { key: ForecastStage; label: string }[] = [
    { key: 'idle', label: 'Waiting' },
    { key: 'uploading', label: 'Uploading' },
    { key: 'bronze', label: 'Bronze Processing' },
    { key: 'silver', label: 'Silver Processing' },
    { key: 'gold', label: 'Gold Processing' },
    { key: 'training', label: 'Training Model' },
    { key: 'forecasting', label: 'Forecasting' },
    { key: 'completed', label: 'Completed' },
    { key: 'failed', label: 'Failed' },
  ]

  const stageIndex = pipelineStages.findIndex((item) => item.key === stage)
  const stageLabel = stageIndex >= 0 ? pipelineStages[stageIndex].label : 'Idle'

  const formatBytes = (value: number) => {
    if (value < 1024) return `${value} B`
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
    return `${(value / (1024 * 1024)).toFixed(2)} MB`
  }

  const fetchForecastApi = async (path: string, options: RequestInit = {}) => {
    const url = path.startsWith('http') ? path : `${API_BASE_URL}${path}`
    const method = options.method ?? 'GET'

    try {
      const response = await fetch(url, { ...options, cache: 'no-store' })
      const responseBody = await response.clone().text().catch(() => '')
      if (!response.ok) {
        console.error('[FORECAST_API_ERROR]', {
          url,
          method,
          status: response.status,
          statusText: response.statusText,
          responseBody,
          error: `HTTP ${response.status}`,
        })
      }
      return response
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      console.error('[FORECAST_API_ERROR]', {
        url,
        method,
        status: 'network_error',
        responseBody: '',
        error: message,
      })
      throw error
    }
  }

  const handleUpload = async (file: File | null) => {
    if (!file) return
    if (backendAvailable === false) {
      const message = 'Backend unavailable. Start the forecasting backend on port 8000 before uploading a CSV.'
      console.error('[FORECAST_UPLOAD]', { url: `${API_BASE_URL}/api/forecast/upload`, status: 'offline', error: message })
      setError(message)
      setStage('failed')
      return
    }

    setSelectedFile(file)
    setProcessing(true)
    setStage('uploading')
    setError('')
    setResult(null)

    try {
      const formData = new FormData()
      formData.append('file', file)

      const response = await fetchForecastApi('/api/forecast/upload', {
        method: 'POST',
        body: formData,
      })

      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        const detail = payload?.error || payload?.detail || 'Upload failed'
        console.error('[FORECAST_UPLOAD_ERROR]', {
          url: `${API_BASE_URL}/api/forecast/upload`,
          status: response.status,
          responseBody: payload,
          error: detail,
        })
        throw new Error(detail)
      }

      const normalizedColumns = Array.isArray(payload.column_names) && payload.column_names.length
        ? payload.column_names
        : Array.isArray(payload.columns)
          ? payload.columns.map((column: unknown) => {
              if (typeof column === 'string') return column
              if (column && typeof column === 'object' && 'name' in column && typeof (column as { name?: unknown }).name === 'string') {
                return (column as { name: string }).name
              }
              return ''
            }).filter(Boolean)
          : []

      const fallbackDate = normalizedColumns.find((column: string) => /date|time|timestamp/i.test(column)) || ''
      const fallbackTarget = normalizedColumns.find((column: string) => !/date|time|timestamp/i.test(column)) || ''

      setColumns(normalizedColumns)
      // Use backend-recommended columns if available, otherwise fallback
      setDateColumn(payload.recommended_datetime_column || payload.date_column || fallbackDate)
      setTargetColumn(payload.recommended_target_column || payload.target_column || fallbackTarget)
      setJobId(payload.job_id || payload.id || '')
      setFileInfo({
        name: file.name,
        size: formatBytes(file.size),
        rows: payload.row_count ?? payload.raw_row_count ?? undefined,
        columns: normalizedColumns,
      })
      setDownloadUrl(payload.download_url || '')
      setStage(payload.stage || 'bronze')
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : 'Upload failed')
      setStage('failed')
    } finally {
      setProcessing(false)
    }
  }

  const startForecast = async () => {
    if (backendAvailable === false) {
      const message = 'Backend unavailable. Start the forecasting backend on port 8000 to run a forecast.'
      console.error('[FORECAST_START]', { url: `${API_BASE_URL}/api/forecast/process`, status: 'offline', error: message })
      setError(message)
      setStage('failed')
      return
    }

    if (!jobId) {
      setError('Please upload a CSV first.')
      return
    }

    setProcessing(true)
    setError('')
    setStage('bronze')
    setResult(null)

    try {
      const response = await fetchForecastApi('/api/forecast/process', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          job_id: jobId,
          date_column: dateColumn,
          target_column: targetColumn,
          horizon,
        }),
      })

      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(payload?.error || payload?.detail || 'Forecast failed')
      }

      const currentJob = payload.job_id || jobId
      setJobId(currentJob)

      let finished = false
      while (!finished) {
        const statusResponse = await fetchForecastApi(`/api/forecast/${currentJob}/status`)
        const statusPayload = await statusResponse.json().catch(() => ({}))

        if (!statusResponse.ok) {
          throw new Error(statusPayload?.error || statusPayload?.detail || 'Status request failed')
        }

        const nextStage = (statusPayload.stage || statusPayload.status || 'uploading').toLowerCase()
        setStage(nextStage === 'completed' ? 'completed' : nextStage === 'failed' ? 'failed' : nextStage === 'training' ? 'training' : nextStage === 'forecasting' ? 'forecasting' : nextStage === 'bronze' ? 'bronze' : nextStage === 'silver' ? 'silver' : nextStage === 'gold' ? 'gold' : 'uploading')

        if (nextStage === 'completed') {
          const resultResponse = await fetchForecastApi(`/api/forecast/${currentJob}/result`)
          const resultPayload = await resultResponse.json().catch(() => ({}))
          if (!resultResponse.ok) {
            throw new Error(resultPayload?.error || resultPayload?.detail || 'Result request failed')
          }

          setResult({
            series: Array.isArray(resultPayload.series) ? resultPayload.series : [],
            metrics: resultPayload.metrics || {},
            rows: Array.isArray(resultPayload.rows) ? resultPayload.rows : [],
          })
          setDownloadUrl(`${API_BASE_URL}/api/forecast/${currentJob}/download`)
          finished = true
        } else if (nextStage === 'failed') {
          setError(statusPayload.error || statusPayload.message || 'Forecast pipeline failed')
          finished = true
        } else {
          await new Promise((resolve) => window.setTimeout(resolve, 1500))
        }
      }
    } catch (processError) {
      setError(processError instanceof Error ? processError.message : 'Forecast failed')
      setStage('failed')
    } finally {
      setProcessing(false)
    }
  }

  const chartData = result?.series?.map((point) => ({
    date: point.date,
    actual: point.actual ?? null,
    forecast: point.forecast ?? null,
  })) || []

  const metrics = result?.metrics && typeof result.metrics === 'object' ? result.metrics : {}

  return (
    <>
      <div className="control-bar forecast-bar">
        <span>Metric: <b>{targetColumn || 'Target column'}</b></span>
        <span>Horizon: <b>{horizon} days</b></span>
        <span>Status: <b>{stageLabel}</b></span>
        <button className="secondary" onClick={() => onAsk('Forecasting')}><Workflow size={14} /> Open Pipeline</button>
      </div>

      <div className="split forecast-layout">
        <Panel title="Forecasting" eyebrow="CSV upload → backend validation → forecasting" className="forecast-panel">
          <div className="forecast-upload-zone">
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,.xlsx,.xls"
              onChange={(event) => {
                const file = event.target.files?.[0] || null
                handleUpload(file)
                event.target.value = ''
              }}
              hidden
            />
            <button className="primary upload-button" onClick={() => fileInputRef.current?.click()} disabled={processing || backendAvailable === false}>
              <Upload size={15} /> {selectedFile ? 'Replace CSV' : 'Upload CSV'}
            </button>

            {backendAvailable === false && (
              <div className="alert-box error"><AlertTriangle size={15} /> Backend unavailable. Start the forecasting backend on port 8000.</div>
            )}

            {fileInfo ? (
              <div className="file-summary">
                <div>
                  <span className="eyebrow">FILE</span>
                  <strong>{fileInfo.name}</strong>
                </div>
                <div className="file-meta">
                  <span>{fileInfo.size}</span>
                  <span>{fileInfo.rows != null ? `${fileInfo.rows} rows` : 'Pending row count'}</span>
                </div>
              </div>
            ) : (
              <div className="empty-forecast-state">
                <p>Upload a CSV to begin backend Bronze → Silver → Gold processing.</p>
              </div>
            )}
          </div>

          <div className="forecast-form">
            <label>
              <span>Date/time column {dateColumn && <small style={{color: '#52d8e8', fontWeight: 500}}>✓</small>}</span>
              <select value={dateColumn} onChange={(event) => setDateColumn(event.target.value)} disabled={!columns.length || processing}>
                <option value="">Select column</option>
                {columns.map((column) => <option key={column} value={column}>{column}</option>)}
              </select>
            </label>

            <label>
              <span>Target column {targetColumn && <small style={{color: '#52d8e8', fontWeight: 500}}>✓</small>}</span>
              <select value={targetColumn} onChange={(event) => setTargetColumn(event.target.value)} disabled={!columns.length || processing}>
                <option value="">Select column</option>
                {columns.map((column) => <option key={column} value={column}>{column}</option>)}
              </select>
            </label>

            <label>
              <span>Forecast horizon</span>
              <select value={horizon} onChange={(event) => setHorizon(Number(event.target.value))} disabled={processing}>
                {[7, 14, 30, 60, 90].map((value) => <option key={value} value={value}>{value} days</option>)}
              </select>
            </label>

            <button className="primary" onClick={startForecast} disabled={!jobId || !dateColumn || !targetColumn || processing || backendAvailable === false}>
              <Play size={14} /> Start Forecast
            </button>
          </div>

          {error && <div className="alert-box error"><AlertTriangle size={15} /> {error}</div>}

          {processing && (
            <div className="processing-box">
              <div className="processing-row">
                <span className="processing-pill active">{stageLabel}</span>
                <small>Uploading → Bronze → Silver → Gold → Training → Forecasting → Completed</small>
              </div>
            </div>
          )}
        </Panel>

        <Panel title="Forecast result" eyebrow="Actual vs forecast" className="summary-panel">
          {chartData.length ? (
            <div className="forecast-chart-wrap">
              <ResponsiveContainer width="100%" height={260}>
                <AreaChart data={chartData}>
                  <defs>
                    <linearGradient id="actualFill" x1="0" x2="0" y1="0" y2="1">
                      <stop offset="0%" stopColor="#52d8e8" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="#52d8e8" stopOpacity={0.02} />
                    </linearGradient>
                    <linearGradient id="forecastFill" x1="0" x2="0" y1="0" y2="1">
                      <stop offset="0%" stopColor="#8fa5ff" stopOpacity={0.25} />
                      <stop offset="100%" stopColor="#8fa5ff" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="date" stroke="#64707a" tickLine={false} axisLine={false} minTickGap={20} />
                  <YAxis stroke="#64707a" tickLine={false} axisLine={false} />
                  <Tooltip
                    contentStyle={{ background: '#111216', border: '1px solid #2a3038', borderRadius: 8 }}
                    formatter={(value) => {
                      const raw = Array.isArray(value) ? value[0] : value
                      if (typeof raw === 'number') return raw.toLocaleString()
                      if (typeof raw === 'string') return raw
                      return '—'
                    }}
                  />
                  <Area type="monotone" dataKey="actual" stroke="#52d8e8" fill="url(#actualFill)" strokeWidth={2} connectNulls />
                  <Area type="monotone" dataKey="forecast" stroke="#8fa5ff" strokeDasharray="5 5" fill="url(#forecastFill)" strokeWidth={2} connectNulls />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="empty-chart-state">
              <p>No forecast yet. Upload a dataset and run the backend job.</p>
            </div>
          )}

          <div className="metrics-grid">
            {Object.entries(metrics).length ? (
              Object.entries(metrics).map(([key, value]) => (
                <div key={key} className="metric-card">
                  <span>{key.toUpperCase()}</span>
                  <strong>{value ?? '—'}</strong>
                </div>
              ))
            ) : (
              <div className="metric-card empty-metric">
                <span>METRICS</span>
                <strong>Pending</strong>
              </div>
            )}
          </div>
        </Panel>
      </div>

      <div className="callout forecast-callout">
        <Sparkles size={18} />
        <div>
          <span className="eyebrow">PIPELINE STATUS</span>
          <p>{stageLabel === 'Completed' ? 'Forecasting finished successfully on the backend.' : stageLabel === 'Failed' ? 'The backend failed at the current pipeline stage. Check the exact error above.' : 'Upload → Bronze → Silver → Gold → Training → Forecasting → Completed'}</p>
        </div>
        {downloadUrl && (
          <a className="secondary action-link" href={downloadUrl} target="_blank" rel="noreferrer">
            <Download size={14} /> Download
          </a>
        )}
      </div>

      {result?.rows?.length ? (
        <Panel title="Forecast table" eyebrow="Backend result export" className="result-panel">
          <div className="forecast-table-wrap">
            <table className="forecast-table">
              <thead>
                <tr>
                  {Object.keys(result.rows[0]).map((header) => <th key={header}>{header}</th>)}
                </tr>
              </thead>
              <tbody>
                {result.rows.slice(0, 10).map((row, index) => (
                  <tr key={`${index}-${Object.values(row).join('-')}`}>
                    {Object.values(row).map((value, cellIndex) => (
                      <td key={`${index}-${cellIndex}`}>{String(value ?? '—')}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}
    </>
  )
}
function News({ onAsk, onOpen: _onOpen }: { onAsk: (c?: string) => void; onOpen: () => void }) {
  const [topics, setTopics] = useState<NewsTopic[]>([])
  const [selectedTopic, setSelectedTopic] = useState<NewsTopic | null>(null)
  const [articles, setArticles] = useState<NewsArticle[]>([])
  const [selectedArticle, setSelectedArticle] = useState<NewsArticle | null>(null)
  const [search, setSearch] = useState('')
  const [source, setSource] = useState('')
  const [impact, setImpact] = useState('')
  const [range, setRange] = useState('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadTopics = async () => {
    setLoading(true)
    setError('')
    try {
      const payload = unwrapMcp<{ topics?: NewsTopic[] }>(await callMcp('list_news_intelligence_topics'))
      setTopics(Array.isArray(payload.topics) ? payload.topics : [])
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load News Intelligence.')
    } finally { setLoading(false) }
  }

  useEffect(() => { loadTopics() }, [])
  useEffect(() => {
    if (!selectedTopic) { setArticles([]); return }
    const loadArticles = async () => {
      setLoading(true)
      try {
        const payload = unwrapMcp<{ articles?: NewsArticle[] }>(await callMcp('get_news_intelligence_topic', {
          topic: selectedTopic.topic, search: search || undefined, source: source || undefined, impact: impact || undefined,
        }))
        let next = Array.isArray(payload.articles) ? payload.articles : []
        if (range !== 'all') {
          const days = range === 'today' ? 1 : range === 'yesterday' ? 2 : range === '7d' ? 7 : 30
          const cutoff = Date.now() - days * 86400000
          next = next.filter(article => article.collected_at && Date.parse(article.collected_at) >= cutoff)
        }
        setArticles(next)
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'Unable to load stored articles.')
      } finally { setLoading(false) }
    }
    loadArticles()
  }, [selectedTopic, search, source, impact, range])

  const formatDate = (value?: string) => value ? new Date(value).toLocaleString() : 'Unknown'
  const sources = Array.from(new Set(articles.map(article => article.source).filter(Boolean)))
  return <>
    <div className="outlets"><div><Globe2 size={17} /><b>Persistent News Repository</b><span className="green">MinIO connected</span><small>{topics.length} topics indexed</small></div><button className="secondary" onClick={loadTopics}><Activity size={14} /> Refresh</button></div>
    {error && <div className="alert-box error"><AlertTriangle size={15} /> {error}</div>}
    <div className="control-bar"><span>Research topics</span><b>{topics.reduce((total, topic) => total + topic.article_count, 0)} stored articles</b><div className="tabs"><button className={range === 'all' ? 'active' : ''} onClick={() => setRange('all')}>All</button><button className={range === 'today' ? 'active' : ''} onClick={() => setRange('today')}>Today</button><button className={range === '7d' ? 'active' : ''} onClick={() => setRange('7d')}>Last 7 days</button><button className={range === '30d' ? 'active' : ''} onClick={() => setRange('30d')}>Last 30 days</button></div></div>
    <Panel title="Research topics" eyebrow="PERSISTED COMPUTER USE INTELLIGENCE">
      {loading && !topics.length ? <div className="empty-forecast-state"><p>Loading stored intelligence...</p></div> : topics.length ? <div className="news-list">{topics.map(topic => <article key={topic.topic} onClick={() => { setSelectedTopic(topic); setSelectedArticle(null) }}><span className="news-number">{String(topic.article_count).padStart(2, '0')}</span><div><span className="eyebrow">TOPIC · UPDATED {formatDate(topic.last_updated)}</span><h3>{topic.topic}</h3><p>{topic.article_count} articles · Positive {topic.positive} · Negative {topic.negative} · Neutral {topic.neutral}</p><button className="text-button" onClick={event => { event.stopPropagation(); setSelectedTopic(topic) }}>Open topic <ArrowRight size={14} /></button></div><ArrowRight size={17} /></article>)}</div> : <div className="empty-forecast-state"><p>No persisted news intelligence yet. Ask Nexus to research a current topic.</p><button className="primary" onClick={() => onAsk('nvidia hari ini beritanya')}><Sparkles size={14} /> Ask Nexus</button></div>}
    </Panel>
    {selectedTopic && <Panel title={selectedTopic.topic} eyebrow="TOPIC DETAIL"><div className="table-tools"><label><Search size={15} /><input value={search} onChange={event => setSearch(event.target.value)} placeholder="Search intelligence..." /></label><select value={source} onChange={event => setSource(event.target.value)}><option value="">All sources</option>{sources.map(value => <option key={value} value={value}>{value}</option>)}</select><select value={impact} onChange={event => setImpact(event.target.value)}><option value="">All impact</option><option value="positive">Positive</option><option value="negative">Negative</option><option value="neutral">Neutral</option></select><button className="secondary" onClick={() => setSelectedTopic(null)}>Close</button></div><div className="news-list">{articles.map((article, index) => <article key={article.artifact_id || article.url || index} onClick={() => setSelectedArticle(article)}><span className="news-number">{String(index + 1).padStart(2, '0')}</span><div><span className="eyebrow">{(article.impact || 'NEUTRAL').toUpperCase()} · {article.source || 'Unknown source'} · PUBLISHED {formatDate(article.published_at)}</span><h3>{article.title || 'Untitled article'}</h3><p>{article.summary || article.content?.slice(0, 220) || 'No summary available.'}</p>{article.url && <a className="text-button" href={article.url} target="_blank" rel="noreferrer" onClick={event => event.stopPropagation()}>View Source ↗</a>}</div></article>)}</div></Panel>}
    {selectedArticle && <Panel title={selectedArticle.title || 'Article detail'} eyebrow="STORED INTELLIGENCE"><div className="metadata-display"><div className="metadata-row"><span>Source</span><b>{selectedArticle.source || 'Unknown'}</b></div><div className="metadata-row"><span>Published</span><b>{formatDate(selectedArticle.published_at)}</b></div><div className="metadata-row"><span>Collected</span><b>{formatDate(selectedArticle.collected_at)}</b></div><div className="metadata-row"><span>Impact</span><b>{selectedArticle.impact || 'Neutral'}</b></div><div className="metadata-row"><span>Category</span><b>{selectedArticle.category || 'News'}</b></div></div><p>{selectedArticle.summary || selectedArticle.content || 'No article content available.'}</p>{selectedArticle.url && <a className="primary" href={selectedArticle.url} target="_blank" rel="noreferrer"><Globe2 size={14} /> View Original Source</a>}</Panel>}
  </>
}
function Documents({ onAsk, uploadedDocuments }: { onAsk: (c?: string) => void; uploadedDocuments: Array<{ name: string; object_key?: string; uploadedAt: string }> }) { return <><div className="upload-zone"><Upload size={25} /><h2>Drop documents here or click to browse</h2><p>Invoice, Receipt, Agreement, Letter, Image · Max 24MB</p><button className="primary" onClick={() => document.getElementById('document-upload')?.click()}>Analyze Document</button></div><div className="split document-result"><Panel title="Uploaded documents" eyebrow="INDEXED IN RAG"><div className="paper-list">{uploadedDocuments.length ? uploadedDocuments.map((doc) => <div key={`${doc.name}-${doc.uploadedAt}`} className="document-item"><FileText size={18} /><div><b>{doc.name}</b><small>{new Date(doc.uploadedAt).toLocaleString()}</small>{doc.object_key ? <span>{doc.object_key}</span> : null}</div></div>) : <div className="document-item empty"><FileText size={18} /><div><b>No documents uploaded yet</b><small>Use the Documents button in the chat composer to add a file.</small></div></div>}</div></Panel><Panel title="AI extracted schemas" eyebrow="VALIDATED JSON"><pre>{uploadedDocuments[0] ? `{
  "document_type": "uploaded_document",
  "filename": "${uploadedDocuments[0].name}",
  "object_key": "${uploadedDocuments[0].object_key ?? 'unknown'}",
  "status": "indexed"
}` : `{
  "document_type": "uploaded_document",
  "status": "waiting"
}`}</pre><span className="badge green">{uploadedDocuments.length ? 'Indexed and ready' : 'Waiting for upload'}</span><button className="secondary" onClick={() => onAsk(uploadedDocuments[0] ? `Document: ${uploadedDocuments[0].name}` : 'Document upload')}>{uploadedDocuments.length ? 'Ask AI About This Document' : 'Upload a document first'}</button></Panel></div></> }
function DataExplorer({ onAsk }: { onAsk: (c?: string) => void }) {
  const [jobs, setJobs] = useState<Array<{ job_id: string; source_file?: string; created_at?: string; status?: string; stages?: string[] }>>([])
  const [selectedJob, setSelectedJob] = useState('')
  const [stage, setStage] = useState<'bronze' | 'silver' | 'gold' | 'forecast'>('bronze')
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(20)
  const [search, setSearch] = useState('')
  const [dataset, setDataset] = useState<{ columns: string[]; rows: Record<string, unknown>[]; total: number; page: number; per_page: number } | null>(null)
  const [metadata, setMetadata] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const loadJobs = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/datasets/jobs`, { cache: 'no-store', signal: AbortSignal.timeout(10000) })
        const payload = await response.json().catch(() => ({ jobs: [] }))
        if (!response.ok) {
          throw new Error(payload?.detail || 'Unable to read dataset jobs.')
        }
        const nextJobs = Array.isArray(payload.jobs) ? payload.jobs : []
        setJobs(nextJobs)
        if (nextJobs.length && !selectedJob) setSelectedJob(nextJobs[0].job_id)
      } catch (cause) {
        console.error('[DATA_EXPLORER_JOBS]', cause)
        setError(cause instanceof Error ? cause.message : 'Unable to list MinIO datasets.')
      }
    }
    loadJobs()
  }, [])

  useEffect(() => {
    if (!selectedJob) {
      setDataset(null)
      setMetadata(null)
      return
    }
    const loadDataset = async () => {
      setLoading(true)
      setError('')
      try {
        // Load metadata
        const metaResponse = await fetch(`${API_BASE_URL}/api/datasets/${selectedJob}/metadata?stage=${stage}`, { cache: 'no-store', signal: AbortSignal.timeout(10000) })
        if (metaResponse.ok) {
          const metaData = await metaResponse.json()
          setMetadata(metaData)
        }

        // Load preview data
        const params = new URLSearchParams({ page: String(page), per_page: String(perPage) })
        if (search.trim()) params.set('search', search.trim())
        const response = await fetch(`${API_BASE_URL}/api/datasets/${selectedJob}/preview?stage=${stage}&${params.toString()}`, { cache: 'no-store', signal: AbortSignal.timeout(10000) })
        const payload = await response.json().catch(() => ({}))
        if (!response.ok) {
          throw new Error(payload?.detail || 'Dataset preview failed.')
        }
        setDataset({
          columns: Array.isArray(payload.columns) ? payload.columns : [],
          rows: Array.isArray(payload.rows) ? payload.rows : [],
          total: typeof payload.total === 'number' ? payload.total : 0,
          page: typeof payload.page === 'number' ? payload.page : 1,
          per_page: typeof payload.per_page === 'number' ? payload.per_page : perPage,
        })
      } catch (cause) {
        console.error('[DATA_EXPLORER_PREVIEW]', cause)
        setDataset(null)
        setError(cause instanceof Error ? cause.message : 'Unable to load dataset preview.')
      } finally {
        setLoading(false)
      }
    }

    loadDataset()
  }, [selectedJob, stage, page, perPage, search])

  const selectedJobMeta = jobs.find((job) => job.job_id === selectedJob)
  const totalPages = dataset ? Math.max(1, Math.ceil(dataset.total / dataset.per_page)) : 1
  
  // Get artifact name based on stage
  const getArtifactName = () => {
    const persistedName = metadata?.artifact_name
    if (typeof persistedName === 'string' && persistedName) return persistedName
    if (stage === 'bronze') return 'raw_sales.csv'
    if (stage === 'silver') return 'data.parquet'
    if (stage === 'gold') return 'data.parquet'
    if (stage === 'forecast') return 'forecast.parquet'
    return 'data.parquet'
  }

  return <>
    <div className="tabs">
      {(['bronze', 'silver', 'gold', 'forecast'] as const).map((tier) => (
        <button key={tier} className={stage === tier ? 'active' : ''} onClick={() => { setStage(tier); setPage(1) }}>{tier === 'forecast' ? 'Forecast' : tier.charAt(0).toUpperCase() + tier.slice(1)}</button>
      ))}
      <span>MinIO medallion artifacts</span>
      <b>{getArtifactName()} · {selectedJobMeta?.status || 'completed'}</b>
    </div>

    <Panel title={`${stage.toUpperCase()} - Medallion Layer`} eyebrow="LAYER-SPECIFIC MINIO ARTIFACT">
      {metadata && (
        <div className="metadata-display">
          <div className="metadata-row">
            <span>Artifact</span>
              <b>{metadata?.object_key ? <a href={`${API_BASE_URL}/api/forecast/${selectedJob}/download`} target="_blank" rel="noreferrer">{getArtifactName()}</a> : getArtifactName()}</b>
          </div>
          {metadata.row_count !== undefined && (
            <div className="metadata-row">
              <span>Rows</span>
              <b>{String(metadata.row_count)}</b>
            </div>
          )}
          {metadata.column_count !== undefined && (
            <div className="metadata-row">
              <span>Columns</span>
              <b>{String(metadata.column_count)}</b>
            </div>
          )}
          {metadata.quality_score !== undefined && (
            <div className="metadata-row">
              <span>Quality Score</span>
              <b>{String(metadata.quality_score)}%</b>
            </div>
          )}
          {metadata.missing_percentage !== undefined && (
            <div className="metadata-row">
              <span>Missing Values</span>
              <b>{String(metadata.missing_percentage)}%</b>
            </div>
          )}
          {metadata.duplicates_removed !== undefined && (
            <div className="metadata-row">
              <span>Duplicates Removed</span>
              <b>{String(metadata.duplicates_removed)}</b>
            </div>
          )}
          {!!metadata.source_bronze_object && (
            <div className="metadata-row">
              <span>Source</span>
              <b>{String(metadata.source_bronze_object)}</b>
            </div>
          )}
          {!!metadata.source_silver_object && (
            <div className="metadata-row">
              <span>Source</span>
              <b>{String(metadata.source_silver_object)}</b>
            </div>
          )}
          {!!metadata.source_gold_object && (
            <div className="metadata-row">
              <span>Source</span>
              <b>{String(metadata.source_gold_object)}</b>
            </div>
          )}
          {!!metadata.forecast_ready && (
            <div className="metadata-row">
              <span>Forecast Ready</span>
              <b>YES</b>
            </div>
          )}
        </div>
      )}

      <div className="table-tools">
        <label>
          <Search size={15} />
          <input value={search} onChange={(event) => { setSearch(event.target.value); setPage(1) }} placeholder="Search records..." />
        </label>
        <select value={selectedJob} onChange={(event) => setSelectedJob(event.target.value)}>
          <option value="">Select job</option>
          {jobs.map((job) => <option key={job.job_id} value={job.job_id}>{job.source_file || job.job_id}</option>)}
        </select>
        <button className="secondary" onClick={() => onAsk('Dataset explorer')}><Sparkles size={14} /> Ask Nexus</button>
      </div>

      {error ? <div className="alert-box error"><AlertTriangle size={15} /> {error}</div> : null}

      {loading ? <div className="empty-forecast-state"><p>Loading {stage} layer data from MinIO…</p></div> : dataset && dataset.columns.length ? (
        <>
          <table>
            <thead>
              <tr>{dataset.columns.map((column) => <th key={column}>{column}</th>)}</tr>
            </thead>
            <tbody>
              {dataset.rows.map((row, rowIndex) => (
                <tr key={`${selectedJob}-${stage}-${rowIndex}`}>
                  {dataset.columns.map((column) => <td key={`${column}-${rowIndex}`}>{String(row[column] ?? '—')}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="pagination">
            Showing {dataset.rows.length ? (dataset.page - 1) * dataset.per_page + 1 : 0}–{Math.min(dataset.page * dataset.per_page, dataset.total)} of {dataset.total} records
            <span>
              <button onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={dataset.page <= 1}>Previous</button>
              <span>Page {dataset.page} of {totalPages}</span>
              <button onClick={() => setPage((current) => Math.min(totalPages, current + 1))} disabled={dataset.page >= totalPages}>Next</button>
            </span>
          </div>
        </>
      ) : (
        <div className="empty-forecast-state">
          <p>No dataset rows are available yet for this job and stage. Upload a CSV and run the backend pipeline first.</p>
        </div>
      )}
    </Panel>
  </>
}
function Pipelines({ onOpen }: { onOpen: (s: string) => void }) { return <><div className="kpi-row four">{[['Total Records Processed', '1,248,921'], ['Pipeline Health', '100%'], ['Last Full Run', '3 min ago'], ['Next Scheduled Run', 'In 12 min']].map(x => <div><span>{x[0]}</span><b>{x[1]}</b></div>)}</div><Panel title="Medallion execution graph" eyebrow="MOBILE_SALES_MEDALLION_ETL"><div className="pipeline-graph">{[['DATA SOURCE', '142,816', 'success'], ['BRONZE TIER', '142,816', 'success'], ['TRANSFORM NODE', '128,450', 'success'], ['SILVER TIER', '128,450', 'success'], ['AGGREGATE NODE', '32,604', 'running'], ['GOLD TIER', '32,604', 'pending']].map((x, i) => <div className="pipeline-node-wrap"><button className={`pipeline-node ${x[2]}`} onClick={() => onOpen(x[0])}><span>{x[2]}</span><b>{x[0]}</b><strong>{x[1]} records</strong><small>Last run 3 min ago</small></button>{i < 5 && <ArrowRight />}</div>)}</div></Panel></> }
function Mcp({ onOpen, onActivity }: { onOpen: (s: string) => void; onActivity: () => void }) { return <><div className="system-banner"><Zap size={21} /><div><b>Overall System Status: Excellent</b><span>All 5 tools online · Average cluster latency: 240ms</span></div><button className="secondary">Refresh Cluster</button></div><div className="tool-grid">{[['Data Tool', 'Query business data', '1,247', '120ms'], ['RAG Tool', 'Retrieve business knowledge', '892', '340ms'], ['RAG Index', 'Index internal files and Detik Finance', '-', 'on demand'], ['Vision Tool', 'Extract structured information', '156', '890ms'], ['Forecast Tool', 'Generate predictions', '423', '1.2s'], ['News Tool', 'Collect external intelligence', '312', '2.1s']].map(x => <div className="tool-card"><div className="tool-icon"><Zap size={17} /></div><span className="badge green">ACTIVE</span><h3>{x[0]}</h3><p>{x[1]}</p><div><span>{x[2]} executions</span><span>{x[3]} avg</span></div><button className="secondary" onClick={() => x[0] === 'RAG Index' ? onOpen('Index RAG') : onOpen(`Execute ${x[0]}`)}><Play size={14} /> {x[0] === 'RAG Index' ? 'Index RAG' : 'Test Tool'}</button></div>)}</div><button className="text-button" onClick={onActivity}>View Agent Activity <ArrowRight size={14} /></button></> }
function AgentActivity({ onOpen: _onOpen }: { onOpen: (s: string) => void }) {
  const [executions, setExecutions] = useState<any[]>([])
  const [stats, setStats] = useState<Record<string, number>>({})
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const selectedIdRef = useRef<string | null>(null)
  selectedIdRef.current = selectedId

  useEffect(() => {
    let active = true
    const refresh = async () => {
      try {
        const response = unwrapMcp<{ executions?: any[]; stats?: Record<string, number> }>(await callMcp('get_execution_history', { limit: 50 }))
        if (!active) return
        setExecutions(response.executions || [])
        setStats(response.stats || {})
        if (!selectedIdRef.current && response.executions?.length) {
          setSelectedId(response.executions[response.executions.length - 1].execution_id)
        }
      } catch (err) { console.warn('[AGENT ACTIVITY] history unavailable', err) }
    }
    refresh()
    const timer = window.setInterval(refresh, 2000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])

  const selected = executions.find(item => item.execution_id === (selectedId || '')) || executions.at(-1)
  const displayTime = (value?: string) => value ? new Date(value).toLocaleTimeString([], { hour12: false }) : '--:--:--'
  const displayDuration = (value?: number) => value == null ? '…' : `${(value / 1000).toFixed(1)}s`
  const statusIcon = (s: string) => s === 'completed' ? '✓' : s === 'failed' ? '✕' : s === 'running' ? '⌕' : '○'
  const statusCls = (s: string) => s === 'completed' ? 'completed' : s === 'failed' ? 'failed' : s === 'running' ? 'running' : 'pending'

  const renderActivityError = (error: any) => {
    if (!error) return null
    const code = typeof error === 'string' ? 'ERROR' : (error.code || 'ERROR')
    const http = error.http_status ? ` · HTTP ${error.http_status}` : ''
    const msg = typeof error === 'string' ? error : (error.message || '')
    return (
      <div className="act-error">
        <span className="act-error-code">{code}{http}</span>
        {msg && <span className="act-error-msg">{msg}</span>}
      </div>
    )
  }

  return <div className="split agent-layout">
    <Panel title="Real-time agent trace window" eyebrow="● LIVE FEED">
      <div className="timeline">
        {executions.length ? [...executions].reverse().map(execution => {
          const s = execution.status || 'running'
          return (
            <button
              key={execution.execution_id}
              className={`timeline-btn${(selectedId || selected?.execution_id) === execution.execution_id ? ' active' : ''}`}
              onClick={() => setSelectedId(execution.execution_id)}
            >
              <time>{displayTime(execution.started_at)}</time>
              <i className={s === 'completed' ? 'done' : s === 'failed' ? 'system' : 'ai'} />
              <div>
                <b>{execution.execution_id}</b>
                <p>{execution.query}</p>
                <small>
                  <span className={statusCls(s)}>{s.toUpperCase()}</span>
                  {' · '}{execution.activities?.length || 0} steps
                  {execution.duration_ms != null ? ` · ${displayDuration(execution.duration_ms)}` : ' · running…'}
                </small>
              </div>
            </button>
          )
        }) : <div className="empty-forecast-state"><p>No real executions yet. Ask Nexus from Nexus to create a live trace.</p></div>}
      </div>
    </Panel>

    <div className="agent-right-col">
      {selected && (
        <Panel title="EXECUTION DETAIL" eyebrow={selected.execution_id}>
          <div className="exec-meta-grid">
            <div><span>Query</span><b>"{selected.query}"</b></div>
            <div><span>Status</span><b className={statusCls(selected.status)}>{selected.status?.toUpperCase()}</b></div>
            <div><span>Started</span><b>{displayTime(selected.started_at)}</b></div>
            {selected.completed_at && <div><span>Ended</span><b>{displayTime(selected.completed_at)}</b></div>}
            <div><span>Duration</span><b>{displayDuration(selected.duration_ms)}</b></div>
            <div><span>Steps</span><b>{selected.activities?.length || 0}</b></div>
          </div>
          <div className="act-list">
            {(selected.activities || []).map((act: any, idx: number) => {
              const s = act.status || 'pending'
              return (
                <div key={act.activity_id || act.id || idx} className={`act-row ${statusCls(s)}`}>
                  <span className="act-icon">{statusIcon(s)}</span>
                  <div className="act-body">
                    <div className="act-top">
                      <span className="act-label">{act.label || act.stage}</span>
                      {act.tool && <code className="act-tool">{act.tool}</code>}
                      <span className="act-dur">
                        {act.duration_ms != null && <span>{displayDuration(act.duration_ms)}</span>}
                        {act.result_count != null && <span> · {act.result_count} results</span>}
                      </span>
                    </div>
                    {act.error && renderActivityError(act.error)}
                  </div>
                </div>
              )
            })}
            {!(selected.activities?.length) && <p className="muted-copy">No activity events recorded for this execution.</p>}
          </div>
        </Panel>
      )}

      <Panel title="Agent diagnostic stats" eyebrow="LIVE DATA">
        <div className="diagnostics">
          {[
            ['Total Sessions Today', stats.total_sessions_today ?? 0],
            ['Successful Sessions', stats.successful_sessions ?? 0],
            ['Failed Sessions', stats.failed_sessions ?? 0],
            ['Tools Used', stats.tools_used ?? 0],
            ['Average Response Time', `${((stats.average_response_time_ms ?? 0) / 1000).toFixed(1)}s`],
            ['Success Rate', `${stats.success_rate ?? 0}%`],
            ['Error Count', stats.error_count ?? 0],
            ['Active Executions', stats.active_executions ?? 0],
          ].map(([label, value]) => <div key={String(label)}><span>{label}</span><b>{value}</b></div>)}
        </div>
        {!executions.length && <p className="muted-copy" style={{marginTop:'0.75rem'}}>Executions will appear here after you send a message from Nexus.</p>}
      </Panel>
    </div>
  </div>
}
function AutonomousDrawer({ route, messages, loading, onSubmit, onClose }: { route: string; messages: { role: 'user' | 'ai'; text: string; result?: AgentResult }[]; loading: boolean; onSubmit: (question: string) => void; onClose: () => void }) {
  const [input, setInput] = useState('')
  const chatEndRef = useRef<HTMLDivElement>(null)
  const send = () => { if (input.trim()) { onSubmit(input); setInput('') } }
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [messages, loading])
  return <div className="overlay"><aside className="drawer"><header><div><h2>Nexus AI</h2><span className="green">● Online</span></div><button onClick={onClose}><X size={18} /></button></header><div className="context">Context: {route}</div><div className="drawer-chat">{messages.length === 0 && <div className="chat ai"><b><Sparkles size={14} /> Nexus AI</b><p>Tanyakan apa saja tentang bisnis Anda. Nexus akan memilih tool MCP yang relevan secara otomatis.</p></div>}{messages.map((message, index) => <div className={`chat ${message.role}`} key={`${message.role}-${index}`}><b>{message.role === 'ai' ? <><Sparkles size={14} /> Nexus AI</> : 'You'}</b>{message.role === 'ai' ? <ReactMarkdown rehypePlugins={[rehypeSanitize]}>{message.text}</ReactMarkdown> : <p>{message.text}</p>}{message.result && <ActivityTrace steps={message.result.steps} sources={message.result.sources} />}</div>)}{loading && <div className="chat ai"><b><Sparkles size={14} /> Nexus AI</b><p>Nexus sedang menganalisis pertanyaan Anda...</p><div className="loading-dots">● ● ●</div></div>}<div ref={chatEndRef} /></div><div className="drawer-input"><input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') send() }} placeholder="Ask Nexus anything..." /><button className="primary" onClick={send} disabled={loading}><Send size={15} /></button></div></aside></div>
}

function ActivityTrace({ activity = [], steps, sources, status, onRetry }: { activity?: ActivityEvent[]; steps: ToolStep[]; sources: ChatSource[]; status?: ChatMessage['status']; onRetry?: () => void }) { 
  const [open, setOpen] = useState(status === 'processing'); 
  useEffect(() => { setOpen(status === 'processing') }, [status]); 
  const viewSource = async (source: ChatSource) => { if (!source.object_key) return; try { const result = unwrapMcp<{ url?: string }>(await callMcp('get_rag_source_url', { object_key: source.object_key, bucket_name: source.bucket })); if (result.url) window.open(result.url, '_blank', 'noopener,noreferrer') } catch { } }; 
  const labels: Record<string, string> = { understanding: 'Understanding the question', ask_rag: 'Finding relevant evidence', search_rag: 'Searching documents', collect_detik_finance_news: 'Searching current information', web_research: 'Searching current information', read_sources: 'Reading relevant documents', verify: 'Verifying information', prepare_answer: 'Preparing answer', completed: 'Completed' }; 
  const visibleActivity: ActivityEvent[] = activity.length ? activity : steps.map(step => ({ id: step.tool, stage: step.tool, status: step.state, message: labels[step.tool] || step.name, durationMs: step.durationMs, error: step.detail })); 
  return <div className="activity-trace">
    <button className="text-button" onClick={() => setOpen(!open)}><Activity size={13} /> {open ? 'Hide activity' : 'View activity'}</button>
    {open && <>
      <div className="tool-checks">
        {visibleActivity.map((item, idx) => 
          <div key={item.id || `${item.stage}-${idx}`} className={`activity-step ${item.status}`}>
            <span className="activity-icon"><b>{item.status === 'completed' ? '✓' : item.status === 'failed' ? '✕' : item.status === 'running' ? '⌕' : '○'}</b></span>
            <div className="activity-details">
              <span>{labels[item.stage] || item.message}{item.status === 'running' ? '...' : ''}</span>
              {(item.durationMs != null || item.resultCount != null || item.error) && <div className="activity-meta">
                {item.error ? <span className="error">{typeof item.error === 'string' ? item.error : `${item.error.code || 'ERROR'}${item.error.message ? ` - ${item.error.message}` : ''}`}</span> : <>
                  {item.durationMs != null && <span>{(item.durationMs / 1000).toFixed(1)}s</span>}
                  {item.durationMs != null && item.resultCount != null && <span> · </span>}
                  {item.resultCount != null && <span>{item.resultCount} results</span>}
                </>}
              </div>}
            </div>
          </div>
        )}
      </div>
      {sources.length > 0 && <div className="source-list">
        <strong>Sources · {sources.length}</strong>
        {sources.map((source, index) => 
          <div className="source-item" key={source.id || `${source.object_key || source.title}-${index}`}>
            <div className="source-title-row">
              <b>{source.title}</b>
              <small>{source.domain || (source.source_type === 'external_news' || source.source_type === 'external_web' ? 'External Web' : 'Internal Document')}{source.page ? ` · Page ${source.page}` : ''}</small>
            </div>
            {source.url ? (
              <a href={source.url} target="_blank" rel="noopener noreferrer" className="source-url">
                {source.url.length > 60 ? source.url.substring(0, 60) + '...' : source.url} <span className="open-link">[Open source ↗]</span>
              </a>
            ) : source.object_key && (
              <button className="text-button" onClick={() => viewSource(source)}>View source</button>
            )}
          </div>
        )}
      </div>}
      {status === 'failed' && <div className="failure-actions">
        <button className="secondary" onClick={onRetry}>Retry</button>
        {sources.length > 0 && <span>I found {sources.length} relevant sources, but couldn't prepare the answer.</span>}
      </div>}
    </>}
  </div> 
}
function Modal({ type, onClose, onAsk }: { type: string; onClose: () => void; onAsk: (c?: string) => void }) { const isIndex = type === 'Index RAG'; return <div className="overlay"><div className="modal"><button className="modal-close" onClick={onClose}><X size={17} /></button><span className="eyebrow">NEXUS AI WORKSPACE</span><h2>{type === 'report' ? 'Executive Intelligence Report' : type.startsWith('Execute') ? type : type}</h2>{type === 'report' ? <div className="report"><h3>Executive Summary</h3><p>Revenue is below plan by 8.4%, with recovery expected across the next 14 days.</p><h3>Revenue Performance</h3><p>Product A and the South region are the primary contributors.</p><h3>Recommended Actions</h3><p>Increase Product A promotion and investigate local logistics bottlenecks.</p></div> : <><p>{isIndex ? 'Index file internal RAG dan artikel Detik Finance yang tersimpan di MinIO ke Chroma.' : 'Tool execution completed successfully. This result is ready for grounded analysis.'}</p><div className="result-box">status: <b>{isIndex ? 'ready' : 'completed'}</b><br />{isIndex ? 'sources: internal files + Detik Finance' : 'latency: 1.2s'}</div></>}<div className="modal-actions"><button className="secondary" onClick={onClose}>Close</button><button className="primary" onClick={() => { onClose(); onAsk(type) }}><Sparkles size={14} /> {isIndex ? 'Run Index RAG' : 'Ask Nexus'}</button></div></div></div> }

export default App

createRoot(document.getElementById('root')!).render(<App />)
