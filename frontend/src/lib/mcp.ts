const MCP_URL = import.meta.env.VITE_MCP_URL || '/mcp'

export type McpResponse = { result?: { content?: { type?: string; text?: string }[] }; error?: { message?: string } }
export type StreamEvent = { event?: string; data: unknown }

function isFinalMcpPayload(payload: unknown): payload is McpResponse {
  if (!payload || typeof payload !== 'object') return false
  const record = payload as Record<string, unknown>
  return Object.prototype.hasOwnProperty.call(record, 'result') || Object.prototype.hasOwnProperty.call(record, 'error')
}

function parseMcpResponse(responseText: string, onEvent?: (event: StreamEvent) => void): McpResponse {
  const blocks = responseText.split(/\r?\n\r?\n/).map(block => block.trim()).filter(Boolean)
  let finalResponse: McpResponse | undefined

  for (const block of blocks) {
    const data = block.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n')
    if (!data) continue
    try {
      const parsed = JSON.parse(data)
      const eventName = block.match(/^event:\s*(.+)$/m)?.[1]
      onEvent?.({ event: eventName, data: parsed })
      if (isFinalMcpPayload(parsed)) finalResponse = parsed as McpResponse
    } catch { /* Ignore keep-alive and incomplete application frames. */ }
  }

  if (finalResponse) return finalResponse
  if (responseText.trim().startsWith('{')) {
    try { return JSON.parse(responseText.trim()) as McpResponse } catch { }
  }
  return { error: { message: 'MCP request did not return a final result.' } } as McpResponse
}

async function readStream(response: Response, onEvent?: (event: StreamEvent) => void): Promise<McpResponse> {
  if (!response.body) return parseMcpResponse(await response.text(), onEvent)
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let finalResponse: McpResponse | undefined

  const consume = (block: string) => {
    const data = block.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n')
    if (!data) return
    try {
      const parsed = JSON.parse(data)
      onEvent?.({ event: block.match(/^event:\s*(.+)$/m)?.[1], data: parsed })
      if (isFinalMcpPayload(parsed)) finalResponse = parsed as McpResponse
    } catch { /* Ignore keep-alive and incomplete application frames. */ }
  }

  while (!finalResponse) {
    const chunk = await reader.read()
    buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done })
    const blocks = buffer.split(/\r?\n\r?\n/)
    buffer = blocks.pop() || ''
    blocks.forEach(consume)
    if (chunk.done) break
  }

  await reader.cancel()
  return finalResponse || parseMcpResponse(buffer, onEvent)
}

export async function callMcp(tool: string, args: Record<string, unknown> = {}, onEvent?: (event: StreamEvent) => void) {
  const response = await fetch(MCP_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
    body: JSON.stringify({ jsonrpc: '2.0', id: crypto.randomUUID(), method: 'tools/call', params: { name: tool, arguments: args } }),
  })
  if (!response.ok) throw new Error(`MCP ${response.status}`)
  const result = await readStream(response, onEvent)
  if (result.error) throw new Error(result.error.message || 'MCP tool failed')
  return result
}

export async function enableMcpLogging() {
  await fetch(MCP_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
    body: JSON.stringify({ jsonrpc: '2.0', id: crypto.randomUUID(), method: 'logging/setLevel', params: { level: 'info' } }),
  })
}

export function unwrapMcp<T = unknown>(result: McpResponse): T {
  const text = result.result?.content?.find(item => item.type === 'text')?.text
  if (!text) return result as T
  try { return JSON.parse(text) as T } catch { return text as T }
}

export async function getMcpSnapshot() {
  return unwrapMcp(await callMcp('get_status')) as { status?: string; services?: Record<string, boolean> }
}
