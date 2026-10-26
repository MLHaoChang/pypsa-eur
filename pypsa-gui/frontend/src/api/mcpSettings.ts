/**
 * Chat harness issue 19, slice 2 — the super-admin MCP server routes
 * (`/api/chat/settings/mcp/servers`, backend `routers/chat.py`).
 *
 * SECURITY. Like api/llmSettings.ts, nothing here ever receives a key value:
 * `McpKeyStatus` is presence, a last-four hint and redactability. The body
 * of `putMcpServer` is an http server's URL, toggle and tier overrides; the
 * backend derives its bearer slot, so a client cannot point a server at
 * another server's credential. A stdio server is written by hand in
 * `mcp-servers.json`: these routes list, test and key it, never change it.
 */
import axios from 'axios'

/** An external server's own tool name: the model sees `mcp__<server>__<tool>`. */
export const shortMcpToolName = (name: string): string => name.replace(/^mcp__[a-z0-9]+__/, '')
import { client } from './client'

export type McpTier = 'read' | 'write' | 'destructive' | 'execution' | 'execution_long_running'

export interface McpKeyStatus {
  present: boolean
  hint: string | null
  redactable: boolean | null
}

export interface McpServerOut {
  /** null for a hand-written entry whose id is invalid; `problem` says why. */
  id: string | null
  enabled: boolean
  transport: 'stdio' | 'http' | null
  command: string | null
  args: string[]
  url: string | null
  tiers: Record<string, string>
  env: Array<{ name: string } & McpKeyStatus>
  /** The bearer token's status; null for a stdio server. */
  key: McpKeyStatus | null
  problem: string | null
}

/** An http server only; the backend refuses any other transport. */
export interface McpServerIn {
  enabled: boolean
  url: string
  tiers?: Record<string, McpTier>
}

export interface McpServersPayload {
  servers: McpServerOut[]
  file_error: string | null
}

export type McpTestResult =
  | { ok: true; skipped: number; tools: Array<{ name: string; tier: McpTier; description: string }> }
  | { ok: false; error_kind: string; message: string }

const BASE = '/chat/settings/mcp/servers'

/** null when the routes are not for this user (403) or not mounted (404). */
export async function fetchMcpServersOrNull(): Promise<McpServersPayload | null> {
  try {
    const r = await client.get<McpServersPayload>(BASE, { skipErrorToast: true })
    return r.data
  } catch (error) {
    if (axios.isAxiosError(error) && (error.response?.status === 403 || error.response?.status === 404)) {
      return null
    }
    throw error
  }
}

export async function putMcpServer(id: string, body: McpServerIn): Promise<McpServerOut> {
  const r = await client.put(`${BASE}/${encodeURIComponent(id)}`, body)
  return r.data
}

export async function deleteMcpServer(id: string): Promise<void> {
  await client.delete(`${BASE}/${encodeURIComponent(id)}`)
}

export async function postMcpTest(id: string): Promise<McpTestResult> {
  const r = await client.post(`${BASE}/${encodeURIComponent(id)}/test`)
  return r.data
}

/** `name` is `bearer` or one of the server's variables. */
export async function putMcpKey(id: string, name: string, value: string): Promise<McpKeyStatus> {
  const r = await client.put(`${BASE}/${encodeURIComponent(id)}/keys/${encodeURIComponent(name)}`, { value })
  return r.data
}

export async function deleteMcpKey(id: string, name: string): Promise<McpKeyStatus> {
  const r = await client.delete(`${BASE}/${encodeURIComponent(id)}/keys/${encodeURIComponent(name)}`)
  return r.data
}
