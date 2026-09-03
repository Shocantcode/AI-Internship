export type ChatSource = {
  id?: string
  title: string
  domain?: string
  source_type?: string
  storage?: string
  bucket?: string
  object_key?: string
  page?: number
  relevance?: number
  relevance_score?: number
  published_at?: string
  url?: string
}

export type NormalizedChatResponse = {
  answer: string
  status: 'success' | 'partial' | 'no_relevant_information' | 'error'
  sources: ChatSource[]
  confidence: number | null
}

function removeInternalMarkup(value: string): string {
  return value
    .replace(/\\r\\n/g, '\n').replace(/\\n/g, '\n').replace(/\r\n/g, '\n')
    .replace(/>{2,}\s*(?:sources?|source)\s*>{2,}.*?(?:<{2,}|$)/gis, '')
    .replace(/^\s*>{2,}\s*(?:sources?|source)\s*>{2,}\s*$/gim, '')
    .replace(/^\s*<{2,}\s*$/gim, '')
    .replace(/^\s*\[?sources?\]?\s*:?\s*$/gim, '')
    .replace(/^\s*\*{3,}\s*$/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

export function normalizeChatResponse(payload: any): NormalizedChatResponse {
  let answer = payload?.answer ?? payload?.message ?? ''
  if (typeof answer === 'string') {
    try {
      const decoded = JSON.parse(answer)
      if (decoded && typeof decoded === 'object') answer = decoded.answer ?? answer
    } catch { /* plain text */ }
  }
  answer = removeInternalMarkup(typeof answer === 'string' ? answer : '')
  const sources = Array.isArray(payload?.sources) ? payload.sources.map((source: any) => ({
    title: source.title || source.filename || source.metadata?.filename || source.source || 'Source',
    source_type: source.source_type || source.type || source.metadata?.source_type,
    storage: source.storage || (source.bucket || source.metadata?.bucket ? 'minio' : undefined),
    bucket: source.bucket || source.metadata?.bucket,
    object_key: source.object_key || source.metadata?.object_key,
    page: source.page ?? source.metadata?.page,
    relevance: source.relevance ?? source.relevance_score ?? source.gaussian_score,
    published_at: source.published_at || source.metadata?.published_at,
    url: source.url || source.metadata?.url,
  })) : []
  return {
    answer: answer || (payload?.success === false || payload?.ok === false ? '' : sources.length ? 'Saya menemukan informasi yang relevan.' : ''),
    status: payload?.status === 'error' ? 'error' : sources.length || answer ? payload?.status || 'success' : 'no_relevant_information',
    sources,
    confidence: typeof payload?.confidence === 'number' ? payload.confidence : null,
  }
}
