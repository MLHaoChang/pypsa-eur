// Issue 19 slice 2 — the super-admin MCP server section. Security-load-
// bearing like AssistantModelSettings: the API returns key presence and a
// hint, never a value, and no input is ever seeded with one.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import {
  deleteMcpServer,
  fetchMcpServersOrNull,
  postMcpTest,
  putMcpKey,
  putMcpServer,
  type McpServersPayload,
} from '../api/mcpSettings'
import McpServerSettings from './McpServerSettings'

vi.mock('../api/mcpSettings', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/mcpSettings')>()
  return {
    ...actual,
    fetchMcpServersOrNull: vi.fn(),
    putMcpServer: vi.fn(),
    deleteMcpServer: vi.fn(),
    postMcpTest: vi.fn(),
    putMcpKey: vi.fn(),
    deleteMcpKey: vi.fn(),
  }
})
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

function payload(over: Partial<McpServersPayload> = {}): McpServersPayload {
  return {
    file_error: null,
    servers: [
      {
        id: 'tariffs', enabled: false, transport: 'stdio', command: '/usr/bin/tariff-mcp',
        args: ['--ro'], url: null, tiers: { upload: 'destructive' },
        env: [{ name: 'TARIFF_TOKEN', present: true, hint: '…wxyz', redactable: true }],
        key: null, problem: null,
      },
      {
        id: 'feed', enabled: true, transport: 'http', command: null, args: [],
        url: 'https://example.com/mcp', tiers: {}, env: [],
        key: { present: false, hint: null, redactable: null }, problem: null,
      },
    ],
    ...over,
  }
}

function renderSection() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, retryDelay: 0 } } })
  return render(<QueryClientProvider client={qc}><McpServerSettings /></QueryClientProvider>)
}

beforeEach(() => { vi.clearAllMocks() })
afterEach(cleanup)

it('renders nothing when the routes are not for this user', async () => {
  vi.mocked(fetchMcpServersOrNull).mockResolvedValue(null)
  const { container } = renderSection()
  await waitFor(() => expect(fetchMcpServersOrNull).toHaveBeenCalled())
  expect(container.innerHTML).toBe('')
})

it('shows an outage distinctly from "not for you"', async () => {
  vi.mocked(fetchMcpServersOrNull).mockRejectedValue(new Error('500'))
  renderSection()
  expect(await screen.findByTestId('mcp-settings-error')).toBeTruthy()
})

it('lists servers with the warning and never seeds a key input', async () => {
  vi.mocked(fetchMcpServersOrNull).mockResolvedValue(payload())
  renderSection()
  expect(await screen.findByTestId('mcp-warning')).toBeTruthy()
  const input = screen.getByTestId('mcp-key-tariffs-TARIFF_TOKEN-input') as HTMLInputElement
  expect(input.value).toBe('')
  expect(input.type).toBe('password')
  expect(input.placeholder).toContain('…wxyz')
  expect(screen.getByTestId('mcp-key-feed-bearer')).toBeTruthy()
})

it('says when there are no servers', async () => {
  vi.mocked(fetchMcpServersOrNull).mockResolvedValue({ servers: [], file_error: null })
  renderSection()
  expect(await screen.findByTestId('mcp-empty')).toBeTruthy()
})

it('toggling enable resends the whole entry', async () => {
  vi.mocked(fetchMcpServersOrNull).mockResolvedValue(payload())
  vi.mocked(putMcpServer).mockResolvedValue(payload().servers[0])
  renderSection()
  await userEvent.click(await screen.findByTestId('mcp-enable-tariffs'))
  expect(putMcpServer).toHaveBeenCalledWith('tariffs', {
    enabled: true, transport: 'stdio', command: '/usr/bin/tariff-mcp', args: ['--ro'], url: null,
    env: ['TARIFF_TOKEN'], tiers: { upload: 'destructive' },
  })
})

it('saves a key and clears the input', async () => {
  vi.mocked(fetchMcpServersOrNull).mockResolvedValue(payload())
  vi.mocked(putMcpKey).mockResolvedValue({ present: true, hint: '…1234', redactable: true })
  renderSection()
  const input = await screen.findByTestId('mcp-key-feed-bearer-input') as HTMLInputElement
  await userEvent.type(input, 'secret-1234')
  await userEvent.click(screen.getByTestId('mcp-key-feed-bearer-save'))
  expect(putMcpKey).toHaveBeenCalledWith('feed', 'bearer', 'secret-1234')
  expect(input.value).toBe('')
})

it('shows the tools a test lists, as text', async () => {
  vi.mocked(fetchMcpServersOrNull).mockResolvedValue(payload())
  vi.mocked(postMcpTest).mockResolvedValue({ ok: true, skipped: 1, tools: [
    { name: 'mcp__tariffs__lookup', tier: 'read', description: '<b>External tool</b> Safety: read' },
  ] })
  renderSection()
  await userEvent.click(await screen.findByTestId('mcp-test-tariffs'))
  const tools = await screen.findByTestId('mcp-test-tools')
  expect(within(tools).getByText('mcp__tariffs__lookup')).toBeTruthy()
  expect(within(tools).getByText('<b>External tool</b> Safety: read')).toBeTruthy()
  expect(tools.querySelector('b')).toBeNull()
  expect(tools.textContent).toContain('1 skipped')
})

it('shows a failed test with the fixed message', async () => {
  vi.mocked(fetchMcpServersOrNull).mockResolvedValue(payload())
  vi.mocked(postMcpTest).mockResolvedValue({ ok: false, error_kind: 'mcp_timeout',
    message: 'The server did not answer in time.' })
  renderSection()
  await userEvent.click(await screen.findByTestId('mcp-test-feed'))
  expect((await screen.findByTestId('mcp-test-failed')).textContent).toBe('The server did not answer in time.')
})

it('adds a stdio server, disabled, with variables and tiers', async () => {
  vi.mocked(fetchMcpServersOrNull).mockResolvedValue(payload())
  vi.mocked(putMcpServer).mockResolvedValue(payload().servers[0])
  renderSection()
  await userEvent.click(await screen.findByTestId('mcp-add-open'))
  const submit = screen.getByTestId('mcp-form-submit') as HTMLButtonElement
  await userEvent.type(screen.getByTestId('mcp-form-id'), 'tariffs')
  expect(screen.getByText('That id is taken.')).toBeTruthy()
  await userEvent.clear(screen.getByTestId('mcp-form-id'))
  await userEvent.type(screen.getByTestId('mcp-form-id'), 'weather')
  expect(submit.disabled).toBe(true)
  await userEvent.type(screen.getByTestId('mcp-form-command'), '/opt/weather-mcp')
  await userEvent.type(screen.getByTestId('mcp-form-args'), '--a{enter}--b')
  await userEvent.type(screen.getByTestId('mcp-form-env'), 'W_TOKEN, W_USER')
  await userEvent.type(screen.getByTestId('mcp-form-tiers'), 'forecast = harmless')
  expect(submit.disabled).toBe(true)
  await userEvent.clear(screen.getByTestId('mcp-form-tiers'))
  await userEvent.type(screen.getByTestId('mcp-form-tiers'), 'forecast = destructive')
  await userEvent.click(submit)
  expect(putMcpServer).toHaveBeenCalledWith('weather', {
    enabled: false, transport: 'stdio', command: '/opt/weather-mcp', args: ['--a', '--b'],
    url: null, env: ['W_TOKEN', 'W_USER'], tiers: { forecast: 'destructive' },
  })
})

it('deletes through the confirm dialog', async () => {
  vi.mocked(fetchMcpServersOrNull).mockResolvedValue(payload())
  vi.mocked(deleteMcpServer).mockResolvedValue()
  renderSection()
  await userEvent.click(await screen.findByTestId('mcp-delete-feed'))
  await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
  expect(deleteMcpServer).toHaveBeenCalledWith('feed')
})

it('blocks adding while the file is unreadable', async () => {
  vi.mocked(fetchMcpServersOrNull).mockResolvedValue({ servers: [], file_error: 'mcp-servers.json is not valid JSON' })
  renderSection()
  expect((await screen.findByTestId('mcp-file-error')).textContent).toContain('not valid JSON')
  expect((screen.getByTestId('mcp-add-open') as HTMLButtonElement).disabled).toBe(true)
})
