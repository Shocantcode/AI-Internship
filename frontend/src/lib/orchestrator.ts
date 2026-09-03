import { callMcp, unwrapMcp, type StreamEvent } from './mcp'
import { normalizeChatResponse, type ChatSource } from './responseNormalizer'

export type ToolState = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
export type ToolStep = {
  name: string
  tool: string
  state: ToolState
  durationMs?: number
  detail?: string
}
export type AgentResult = {
  answer: string
  steps: ToolStep[]
  sources: ChatSource[]
  evidence: unknown[]
  confidence: number
  status: 'completed' | 'partial' | 'failed'
  error?: { stage?: string; code?: string; status?: number; message?: string; retryable?: boolean; provider?: string }
}
export type ActivityEvent = {
  id?: string
  eventId?: string
  stage: string
  status: ToolState
  message: string
  resultCount?: number
  durationMs?: number
  error?: string | { code?: string; stage?: string; message?: string; provider?: string; http_status?: number; retryable?: boolean }
}

function forwardStreamEvent(event: StreamEvent, requestId: string, emit?: (event: ActivityEvent) => void) {
  let payload: Record<string, any>
  if (event.event === 'activity' && typeof event.data === 'object' && event.data) {
    payload = event.data as Record<string, any>
  } else if (event.data && typeof event.data === 'object' && (event.data as any).method === 'notifications/activity') {
    payload = (event.data as Record<string, any>).params
  } else if (event.data && typeof event.data === 'object' && (event.data as any).method === 'activity') {
    payload = (event.data as Record<string, any>).params
  } else if (event.data && typeof event.data === 'object' && (event.data as any).method === 'notifications/message') {
    const message = (event.data as Record<string, any>).params
    if ((message as any)?.logger !== 'activity' || typeof (message as any)?.data !== 'object') return
    payload = (message as Record<string, any>).data
  } else {
    return
  }

  if (payload.request_id && payload.request_id !== requestId) return
  const action = String(payload.action || payload.stage || '')
  const stage = action === 'generate_answer' ? 'prepare_answer' : action === 'search_web' ? 'web_research' : action
  if (!stage) return
  const id = String(payload.activity_id || payload.id || stage)
  
  emit?.({
    id,
    eventId: payload.event_id ? String(payload.event_id) : undefined,
    stage,
    status: payload.status === 'running' || payload.status === 'completed' || payload.status === 'failed' || payload.status === 'skipped' ? payload.status : 'pending',
    message: payload.label || payload.message || stage,
    resultCount: payload.result_count ?? payload.metadata?.result_count ?? payload.metadata?.sources_found,
    durationMs: payload.duration_ms,
    error: payload.error || payload.metadata?.error,
  })
}

type ToolPayload = Record<string, any>
type Intent = { rag: boolean; forecast: boolean; news: boolean; document: boolean }

const registry = {
  rag: { name: 'Internal Knowledge', tool: 'ask_rag' },
  search: { name: 'RAG Search', tool: 'search_rag' },
  forecast: { name: 'Forecast', tool: 'get_forecasting_preview' },
  news: { name: 'News Intelligence', tool: 'collect_detik_finance_news' },
} as const

const NEWS_TERMS = /news|berita|market|pasar|ekonomi|economic|regulation|regulasi|competitor|kompetitor|current|terbaru|terkini|today|hari ini|recent|terbaru|industry|industri|external|eksternal/i
const MARKET_NEWS_QUERY = /(?=.*\b(?:bitcoin|crypto|cryptocurrency|btc|silver|perak|gold|emas|ihsg|saham|rupiah|minyak|oil|coal|batu bara)\b)(?=.*\b(?:price|prize|harga|drop|fall|fell|declin|turun|naik|rise|rally|volatil|volatile)\b)/i
const FORECAST_TERMS = /forecast|prediction|prediksi|future|masa depan|next\s+(?:7|14|30)\s+days?|next month|bulan depan|expected|projected|proyeksi|demand/i
const BUSINESS_TERMS = /revenue|pendapatan|sales|penjualan|order|pesanan|product|produk|customer|pelanggan|region|wilayah|business|bisnis|declin|turun|naik|performance|kinerja|inventory|inventor/i
const BUSINESS_METRIC_TERMS = /revenue|pendapatan|sales|penjualan|order|pesanan|product|produk|customer|pelanggan|region|wilayah|business|bisnis|performance|kinerja|inventory|inventor/i
const DOCUMENT_TERMS = /invoice|faktur|receipt|kuitansi|agreement|kontrak|letter|surat|document|dokumen|report|laporan|internal/i
const MARKET_TERMS = /bitcoin|crypto|cryptocurrency|btc|silver|perak|gold|emas|ihsg|saham|rupiah|minyak|oil|coal|batu bara|bi rate|inflasi|usd\s*\/?\s*idr|dolar/i
const MARKET_MOVEMENT_TERMS = /kenapa|mengapa|why|turun|naik|drop|fall|fell|declin|rise|rally|harga|price|market|pasar/i
const MARKET_TICKER = /\b(?:BBRI|BBCA|BMRI|BBNI|ASII|TLKM|GOTO|ANTM|MDKA|UNTR)\b/i

export function detectIntent(question: string): Intent {
  const hasBusiness = BUSINESS_TERMS.test(question)
  const hasBusinessMetric = BUSINESS_METRIC_TERMS.test(question)
  const isCurrentMarket = MARKET_TICKER.test(question) || ((MARKET_TERMS.test(question)) && MARKET_MOVEMENT_TERMS.test(question))
  const isExplicitMarketPrice = (MARKET_TERMS.test(question) || MARKET_TICKER.test(question)) && /harga|price|sekarang|current|today|hari ini|terbaru|terkini/i.test(question)
  const hasNews = isCurrentMarket || NEWS_TERMS.test(question) || MARKET_NEWS_QUERY.test(question)
  const hasForecast = FORECAST_TERMS.test(question)
  const hasDocument = DOCUMENT_TERMS.test(question)
  return {
    rag: !isCurrentMarket && !isExplicitMarketPrice && (hasBusiness || hasDocument || !hasNews && !hasForecast),
    forecast: hasForecast,
    news: hasNews,
    document: hasDocument,
  }
}

async function runStep(step: ToolStep, action: () => Promise<ToolPayload>, emit?: (event: ActivityEvent) => void): Promise<ToolPayload> {
  const started = performance.now()
  step.state = 'running'
  emit?.({ stage: step.tool, status: 'running', message: step.name })
  try {
    const payload = await action()
    step.state = payload?.status === 'error' || payload?.status === 'failed' ? 'failed' : 'completed'
    step.detail = payload?.message
    emit?.({ stage: step.tool, status: step.state, message: step.name, resultCount: Array.isArray(payload?.results) ? payload.results.length : undefined, error: step.detail })
    return payload
  } catch (error) {
    step.state = 'failed'
    step.detail = error instanceof Error ? error.message : 'Tool gagal dijalankan'
    emit?.({ stage: step.tool, status: 'failed', message: step.name, error: step.detail })
    return { status: 'error', message: step.detail }
  } finally {
    step.durationMs = Math.round(performance.now() - started)
  }
}

function text(value: unknown): string {
  if (typeof value === 'string') return value
  try { return JSON.stringify(value, null, 2) } catch { return String(value) }
}

async function collectNews(question: string, step: ToolStep, requestId: string, emit?: (event: ActivityEvent) => void): Promise<ToolPayload> {
  const newsQuery = question
    .replace(/\s*application context\s*:.*/i, '')
    .replace(/^\s*(why did|why has|why is|kenapa|mengapa)\s+/i, '')
    .replace(/\bprize\b/gi, 'price')
    .replace(/[?!.]+\s*$/, '')
    .replace(/\s+/g, ' ')
    .trim()
  const args = { query: newsQuery, limit: 5, async_mode: true, request_id: requestId }
  console.info('[CURRENT_RESEARCH]', { query: newsQuery, tool: registry.news.tool, args, event: 'start' })
  emit?.({ stage: 'web_research', status: 'running', message: 'Searching current information' })
  let collected: ToolPayload
  try {
    collected = unwrapMcp<ToolPayload>(await callMcp(registry.news.tool, args, event => forwardStreamEvent(event, requestId, emit)))
    console.info('[CURRENT_RESEARCH]', { query: newsQuery, tool: registry.news.tool, response: collected, event: 'response' })
  } catch (error) {
    const message = error instanceof Error ? error.message : 'News collection failed'
    console.error('[CURRENT_RESEARCH FAILED]', { query: newsQuery, tool: registry.news.tool, args, error })
    step.state = 'failed'
    step.detail = message
    emit?.({ stage: 'web_research', status: 'failed', message: 'Searching current information', error: message })
    return { status: 'failed', message }
  }
  if (collected.status !== 'processing' || !collected.job_id) {
    const state: ToolState = collected.status === 'failed' || collected.status === 'error' ? 'failed' : 'completed'
    step.state = state
    emit?.({ stage: 'web_research', status: state, message: 'Searching current information', resultCount: collected.articles?.length, error: collected.message })
    return collected
  }

  const deadline = Date.now() + 300_000
  for (let attempt = 0; Date.now() < deadline; attempt += 1) {
    const statusArgs = { job_id: collected.job_id }
    const status = unwrapMcp<ToolPayload>(await callMcp('get_news_job_status', statusArgs))
    step.detail = status.message || `Progress ${status.progress ?? 0}%`
    if (status.status === 'completed') {
      const result = status.result || status
      
      if (Array.isArray(status.activities)) {
        status.activities.forEach((act: any) => forwardStreamEvent({ event: 'activity', data: act }, requestId, emit))
      }
      
      step.state = result.status === 'failed' || result.status === 'error' ? 'failed' : 'completed'
      console.info('[CURRENT_RESEARCH]', { query: newsQuery, tool: registry.news.tool, job_id: collected.job_id, response: result, event: 'completed' })
      return result
    }
    if (status.status === 'failed' || status.status === 'error') {
      if (Array.isArray(status.activities)) {
        status.activities.forEach((act: any) => forwardStreamEvent({ event: 'activity', data: act }, requestId, emit))
      }
      step.state = 'failed'
      console.error('[CURRENT_RESEARCH FAILED]', { query: newsQuery, tool: registry.news.tool, args: statusArgs, response: status, error: status.message })
      return status
    }
    await new Promise(resolve => window.setTimeout(resolve, 1000))
  }
  step.state = 'failed'
  emit?.({ stage: 'web_research', status: 'failed', message: 'Searching current information', error: 'News collection timeout' })
  const message = 'News collection timeout after 300 seconds'
  console.error('[CURRENT_RESEARCH FAILED]', { query: newsQuery, tool: registry.news.tool, args, error: message })
  return { status: 'error', message }
}

export async function runAgent(
  question: string,
  pageContext = '',
  emit?: (event: ActivityEvent) => void,
  responseLength: 'short' | 'medium' | 'long' = 'medium',
  images: Array<{ id?: string; filename: string; type: string; content_base64?: string; size?: number }> = [],
  documents: Array<{ id?: string; filename: string; type: string; size?: number }> = [],
): Promise<AgentResult> {
  const requestId = `exec_${new Date().toISOString().slice(0, 10).replaceAll('-', '')}_${crypto.randomUUID().slice(0, 8)}`
  console.info('[CHAT] submit', { question, requestId })
  const prompt = pageContext ? `${question}\n\nApplication context: ${pageContext}` : question
  const activity: ActivityEvent[] = []
  const recordActivity = (event: ActivityEvent) => {
    const existing = activity.findIndex(item => (item.id || item.stage) === (event.id || event.stage))
    if (existing >= 0) {
      const previous = activity[existing]
      if ((previous.status === 'completed' || previous.status === 'failed') && event.status !== previous.status) return
      activity[existing] = event
    } else activity.push(event)
    emit?.(event)
  }
  console.info('[CHAT] request started', { requestId })
  const payload = unwrapMcp<ToolPayload>(await callMcp('ask_nexus', {
    message: prompt,
    response_length: responseLength,
    request_id: requestId,
    images: images.length ? images.map(image => ({
      id: image.id,
      filename: image.filename,
      type: image.type,
      content_base64: image.content_base64,
      size: image.size,
    })) : [],
    documents: documents.length ? documents.map(document => ({
      id: document.id,
      filename: document.filename,
      type: document.type,
      size: document.size,
    })) : [],
  }, event => forwardStreamEvent(event, requestId, recordActivity)))
  const terminalStatus = payload?.status === 'completed' || payload?.status === 'partial' || payload?.status === 'failed' || payload?.status === 'error'
  if (!terminalStatus && Array.isArray(payload?.activity) && payload.activity.length) {
    const lastState = payload.activity.at(-1)?.status
    if (lastState === 'completed' || lastState === 'failed' || lastState === 'skipped') {
      payload.status = lastState === 'completed' ? 'completed' : 'failed'
    }
  }
  console.info('[CHAT] request completed', { requestId, response: payload })
  const gatewayNormalized = normalizeChatResponse(payload)
  const gatewaySteps: ToolStep[] = activity.map(event => ({
    name: event.message,
    tool: event.stage,
    state: event.status,
    durationMs: event.durationMs,
    detail: typeof event.error === 'string' ? event.error : event.error?.message,
  }))
  const failed = payload?.success === false || payload?.status === 'failed' || payload?.status === 'error'
  const error = payload?.error
  console.info('[CHAT] answer', { requestId, answer: gatewayNormalized.answer, activities: activity })
  return {
    answer: gatewayNormalized.answer,
    steps: gatewaySteps,
    sources: gatewayNormalized.sources,
    evidence: payload?.evidence || [],
    confidence: typeof payload?.confidence === 'number' ? payload.confidence : 0,
    status: failed ? 'failed' : payload?.status === 'partial' ? 'partial' : 'completed',
    error,
  }

  /* Kept below as a local fallback while older deployments are upgraded. */
  const intent = detectIntent(prompt)
  console.info('[NEXUS RESEARCH]', {
    query: question,
    intent,
    selected_tools: [intent.rag ? 'ask_rag' : null, intent.rag ? 'search_rag' : null, intent.news ? 'collect_detik_finance_news' : null, intent.forecast ? 'get_forecasting_preview' : null].filter(Boolean),
    request_id: requestId,
  })
  const steps: ToolStep[] = []
  const jobs: Promise<ToolPayload>[] = []
  let rag: ToolPayload = {}
  let search: ToolPayload = {}
  let forecast: ToolPayload = {}
  let news: ToolPayload = {}

  if (intent.rag) {
    const askStep = { name: 'Internal Knowledge', tool: registry.rag.tool, state: 'pending' as ToolState }
    const searchStep = { name: 'RAG Evidence', tool: registry.search.tool, state: 'pending' as ToolState }
    steps.push(askStep, searchStep)
    jobs.push(runStep(askStep, async () => unwrapMcp<ToolPayload>(await callMcp(registry.rag.tool, { question: prompt, request_id: requestId }, event => forwardStreamEvent(event, requestId, emit))), emit).then(result => { rag = result; return result }))
    jobs.push(runStep(searchStep, async () => unwrapMcp<ToolPayload>(await callMcp(registry.search.tool, { query: prompt, top_k: 5, request_id: requestId }, event => forwardStreamEvent(event, requestId, emit))), emit).then(result => { search = result; return result }))
  }
  if (intent.forecast) {
    const step = { name: 'Forecast', tool: registry.forecast.tool, state: 'pending' as ToolState }
    steps.push(step)
    jobs.push(runStep(step, async () => unwrapMcp<ToolPayload>(await callMcp(registry.forecast.tool, { limit: 14 })), emit).then(result => { forecast = result; return result }))
  }
  if (intent.news) {
    const step = { name: 'News Intelligence', tool: registry.news.tool, state: 'pending' as ToolState }
    steps.push(step)
    jobs.push(collectNews(prompt, step, requestId, emit).then(result => { news = result; return result }))
  }

  await Promise.all(jobs)
  console.info('[NEXUS RESEARCH]', {
    query: question,
    request_id: requestId,
    rag_status: rag.status || 'not_requested',
    rag_candidate_count: Array.isArray(search.results) ? search.results.length : 0,
    news_status: news.status || 'not_requested',
    articles_read: Array.isArray(news.articles) ? news.articles.length : 0,
  })
  const currentResearchFailed = intent.news && (news.status === 'failed' || news.status === 'error' || !Array.isArray(news.articles) || news.articles.length === 0)
  const validFallbackEvidence = Boolean(rag.answer && Array.isArray(rag.sources) && rag.sources.length > 0)
  if (currentResearchFailed && !validFallbackEvidence) {
    emit?.({ stage: 'web_research', status: 'failed', message: 'Searching current information', error: news.message || 'No current evidence was returned' })
    return {
      answer: 'Nexus tidak dapat memperoleh informasi terkini yang dapat dipercaya untuk pertanyaan ini. Coba gunakan topik atau aset yang lebih spesifik.',
      steps,
      sources: [],
      evidence: [],
      confidence: 0,
      status: 'failed',
    }
  }
  if (news.articles?.length) {
    const synthesisStep = { name: 'Evidence Analysis', tool: 'ask_rag', state: 'pending' as ToolState }
    steps.push(synthesisStep)
    const freshEvidence = news.articles.map((article: ToolPayload, index: number) => ({
      text: article.content || '',
      source: article.title || article.source || 'External research',
      metadata: {
        evidence_id: article.evidence_id,
        research_session_id: article.research_session_id,
        request_id: article.request_id,
        title: article.title,
        source: article.source || 'Detik Finance',
        source_type: 'external_news',
        storage: 'minio',
        bucket: news.storage?.bucket || 'news',
        object_key: news.storage?.objects?.[index]?.object_key,
        published_at: article.published_at,
        url: article.url,
      },
      relevance_score: article.relevance_score || 0.8,
    }))
    const synthesized = await runStep(synthesisStep, async () => unwrapMcp<ToolPayload>(await callMcp(registry.rag.tool, { question: prompt, evidence: freshEvidence, include_internal: intent.rag, request_id: requestId }, event => forwardStreamEvent(event, requestId, emit))))
    if (synthesized.answer) rag = synthesized
    const synthesisSucceeded = synthesized.success === true && Boolean(String(synthesized.answer || '').trim())
    emit?.({ stage: 'prepare_answer', status: synthesisSucceeded ? 'completed' : 'failed', message: 'Preparing answer', error: synthesized.error?.message || synthesized.message })
    if (!synthesisSucceeded) {
      const sourceCount = freshEvidence.length
      const error = synthesized.error || { code: 'ANSWER_GENERATION_FAILED', message: synthesized.message || 'Answer generation failed' }
      const answer = error.code === 'RESOURCE_EXHAUSTED'
        ? `${sourceCount} sumber relevan berhasil ditemukan, tetapi Nexus belum dapat menyusun jawaban karena layanan AI sedang mencapai batas penggunaan.`
        : `${sourceCount} sumber relevan berhasil ditemukan, tetapi Nexus belum dapat menyusun jawabannya.`
      return {
        answer,
        steps,
        sources: newsSourcesFromArticles(news),
        evidence: news.articles,
        confidence: 0,
        status: 'failed',
        error,
      }
    }
  }
  const synthesisFailed = intent.rag && rag.status === 'error' && !rag.answer
  if (synthesisFailed) {
    emit?.({ stage: 'prepare_answer', status: 'failed', message: 'Preparing answer', error: rag.error?.message || rag.message })
    const error = rag.error || { code: 'ANSWER_GENERATION_FAILED', message: rag.message || 'Internal answer generation failed' }
    return {
      answer: error.code === 'RESOURCE_EXHAUSTED'
        ? 'Nexus menemukan evidence internal, tetapi layanan AI sedang mencapai batas penggunaan sehingga jawaban belum dapat disusun.'
        : 'Nexus menemukan evidence internal, tetapi belum berhasil menyusun jawabannya.',
      steps,
      sources: searchSources(search),
      evidence: Array.isArray(search.results) ? search.results : [],
      confidence: 0,
      status: 'failed',
      error,
    }
  }
  const successful = steps.filter(step => step.state === 'completed')
  const failures = steps.filter(step => step.state === 'failed')
  const parts: string[] = []
  if (rag.answer) parts.push(rag.answer)
  if (!rag.answer && search.results) parts.push(`Saya menemukan ${search.results.length} sumber internal yang relevan, tetapi belum ada jawaban sintesis.`)
  if (forecast.forecast) parts.push(`Forecast berhasil dimuat untuk ${forecast.forecast.total_records ?? 14} titik data ke depan.`)
  if (news.articles?.length) parts.push(`Saya menemukan ${news.articles.length} artikel eksternal yang relevan dari Detik Finance.`)
  if (!parts.length) parts.push('Nexus belum menghasilkan jawaban dari proses yang dijalankan.')
  if (failures.length) parts.push(`Catatan: ${failures.map(step => `${step.name} tidak tersedia`).join(', ')}. Analisis tetap dibuat dari evidence yang berhasil diambil.`)

  const newsSources = newsSourcesFromArticles(news)
  const rawSources = [...(Array.isArray(rag.sources) ? rag.sources : []), ...newsSources]
  const relevanceThreshold = 0.2
  const sources = rawSources.filter((source: ToolPayload, index: number, all: ToolPayload[]) => {
    const metadata = source.metadata || source
    const identity = metadata.object_key || metadata.url || metadata.filename || metadata.title
    const relevance = Number(metadata.relevance_score ?? metadata.relevance ?? source.relevance_score ?? source.relevance ?? 0)
    return identity && relevance >= relevanceThreshold && all.findIndex(candidate => { const candidateMetadata = candidate.metadata || candidate; return (candidateMetadata.object_key || candidateMetadata.url || candidateMetadata.filename || candidateMetadata.title) === identity }) === index
  })
  const normalized = normalizeChatResponse({ answer: parts.join('\n\n'), status: failures.length ? 'partial' : undefined, sources })
  if (!news.articles?.length) {
  }
  if (news.status === 'failed' && !validFallbackEvidence) {
    normalized.answer = 'Riset eksternal gagal dijalankan. Saya tidak akan menampilkan sumber atau jawaban yang tidak terverifikasi.'
    normalized.status = 'error'
  } else if (news.status === 'failed' && validFallbackEvidence) {
    normalized.answer = `${normalized.answer}\n\nSaya menggunakan evidence internal karena riset terkini tidak tersedia.`
    normalized.status = 'partial'
  } else if (news.articles?.length && !rag.answer) {
    normalized.answer = 'Riset eksternal berhasil dibaca, tetapi tahap penyusunan jawaban belum berhasil. Sumber terverifikasi tersedia di bawah.'
    normalized.status = 'partial'
  }
  return {
    answer: normalized.answer,
    steps,
    sources: normalized.sources,
    evidence: [forecast, ...(news.articles || [])].filter(item => Object.keys(item || {}).length > 0),
    confidence: Math.max(35, Math.min(98, 45 + successful.length * 16 - failures.length * 10)),
    status: failures.length ? 'partial' : 'completed',
  }
}

function newsSourcesFromArticles(news: ToolPayload): ChatSource[] {
  return (news.articles || []).map((article: ToolPayload, index: number) => ({
    id: article.evidence_id || `source-${index}`,
    title: article.title,
    domain: article.domain || (article.url ? new URL(article.url).hostname : 'Detik Finance'),
    source_type: 'external_web',
    source: article.source || 'Detik Finance',
    storage: 'minio',
    bucket: news.storage?.objects?.[index]?.bucket || news.storage?.bucket || 'news',
    object_key: news.storage?.objects?.[index]?.object_key,
    url: article.url,
    published_at: article.published_at,
    snippet: article.content ? article.content.slice(0, 150) + '...' : undefined,
    relevance: article.relevance_score,
  }))
}

function searchSources(search: ToolPayload): ChatSource[] {
  return (search.results || []).map((result: ToolPayload) => {
    const metadata = result.metadata || {}
    return {
      id: metadata.evidence_id || `internal-${Math.random().toString(36).substring(7)}`,
      title: metadata.title || metadata.filename || result.source || 'Internal evidence',
      source_type: metadata.source_type || 'internal_document',
      domain: metadata.domain || 'internal_document',
      storage: metadata.bucket ? 'minio' : undefined,
      bucket: metadata.bucket,
      object_key: metadata.object_key,
      page: metadata.page ?? result.page,
      relevance: result.relevance_score ?? result.gaussian_score,
      url: metadata.url,
      snippet: result.content ? String(result.content).slice(0, 150) + '...' : undefined,
    }
  }).filter((source: ChatSource) => source.title)
}

export function describePayload(payload: unknown): string {
  return text(payload)
}
