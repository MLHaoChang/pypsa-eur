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
  expect(screen.getByTestId('mcp-row-tariffs').textContent).toBe('tariffs · stdio · /usr/bin/tariff-mcp --ro')
  expect(screen.getByTestId('mcp-row-feed').getAttribute('title')).toBe('feed · http · https://example.com/mcp')
  expect(screen.getByTestId('mcp-key-tariffs-TARIFF_TOKEN-status').textContent).toBe('Key set (…wxyz)')
  expect(screen.queryByTestId('mcp-key-tariffs-TARIFF_TOKEN-input')).toBeNull()
  await userEvent.click(screen.getByTestId('mcp-key-tariffs-TARIFF_TOKEN-replace'))
  const input = screen.getByTestId('mcp-key-tariffs-TARIFF_TOKEN-input') as HTMLInputElement
  expect(input.value).toBe('')
  expect(input.type).toBe('password')
  expect(input.placeholder).not.toContain('wxyz')
  expect(screen.getByTestId('mcp-key-feed-bearer-status').textContent).toBe('No key')
})

it('keeps a stdio server read-only and says why', async () => {
  vi.mocked(fetchMcpServersOrNull).mockResolvedValue(payload())
  renderSection()
  const toggle = await screen.findByTestId('mcp-enable-tariffs') as HTMLInputElement
  expect(toggle.disabled).toBe(true)
  expect(screen.getByTestId('mcp-locked-tariffs').textContent).toBe('Command servers are edited in mcp-servers.json')
  expect(screen.queryByTestId('mcp-edit-tariffs')).toBeNull()
  expect(screen.queryByTestId('mcp-delete-tariffs')).toBeNull()
  expect(screen.getByTestId('mcp-test-tariffs')).toBeTruthy()
  expect(screen.queryByTestId('mcp-locked-feed')).toBeNull()
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
  await userEvent.click(await screen.findByTestId('mcp-enable-feed'))
  expect(putMcpServer).toHaveBeenCalledWith('feed', {
    enabled: false, url: 'https://example.com/mcp', tiers: {},
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
    { name: 'mcp__tariffs__upload', tier: 'destructive', description: 'Uploads.' },
  ] })
  renderSection()
  await userEvent.click(await screen.findByTestId('mcp-test-tariffs'))
  const tools = await screen.findByTestId('mcp-test-tools')
  expect(within(tools).getByText('mcp__tariffs__lookup')).toBeTruthy()
  expect(within(tools).getByText('<b>External tool</b> Safety: read')).toBeTruthy()
  expect(tools.querySelector('b')).toBeNull()
  expect(tools.textContent).toContain('1 skipped')
  expect(within(tools).getByText('Read only')).toBeTruthy()
  expect(within(tools).getByText('Asks first').getAttribute('title')).toBe('destructive')
})

it('shows a failed test with the fixed message', async () => {
  vi.mocked(fetchMcpServersOrNull).mockResolvedValue(payload())
  vi.mocked(postMcpTest).mockResolvedValue({ ok: false, error_kind: 'mcp_timeout',
    message: 'The server did not answer in time.' })
  renderSection()
  await userEvent.click(await screen.findByTestId('mcp-test-feed'))
  expect((await screen.findByTestId('mcp-test-failed')).textContent).toBe('The server did not answer in time.')
})

it('adds an http server, disabled, with tiers; no command form', async () => {
  vi.mocked(fetchMcpServersOrNull).mockResolvedValue(payload())
  vi.mocked(putMcpServer).mockResolvedValue(payload().servers[1])
  renderSection()
  await userEvent.click(await screen.findByTestId('mcp-add-open'))
  expect(screen.queryByTestId('mcp-form-command')).toBeNull()
  expect(screen.queryByTestId('mcp-form-transport')).toBeNull()
  const submit = screen.getByTestId('mcp-form-submit') as HTMLButtonElement
  await userEvent.type(screen.getByTestId('mcp-form-id'), 'tariffs')
  expect(screen.getByText('That id is taken.')).toBeTruthy()
  await userEvent.clear(screen.getByTestId('mcp-form-id'))
  await userEvent.type(screen.getByTestId('mcp-form-id'), 'weather')
  expect(submit.disabled).toBe(true)
  await userEvent.type(screen.getByTestId('mcp-form-url'), 'https://weather.example/mcp')
  await userEvent.type(screen.getByTestId('mcp-form-tiers'), 'forecast = harmless')
  expect(submit.disabled).toBe(true)
  await userEvent.clear(screen.getByTestId('mcp-form-tiers'))
  await userEvent.type(screen.getByTestId('mcp-form-tiers'), 'forecast = destructive')
  await userEvent.click(submit)
  expect(putMcpServer).toHaveBeenCalledWith('weather', {
    enabled: false, url: 'https://weather.example/mcp', tiers: { forecast: 'destructive' },
  })
})

it('deletes through the confirm dialog', async () => {
  vi.mocked(fetchMcpServersOrNull).mockResolvedValue(payload())
  vi.mocked(deleteMcpServer).mockResolvedValue()
  renderSection()
  await userEvent.click(await screen.findByTestId('mcp-delete-feed'))
  await userEvent.click(screen.getByRole('button', { name: 'Delete server' }))
  expect(deleteMcpServer).toHaveBeenCalledWith('feed')
})

it('blocks adding while the file is unreadable', async () => {
  vi.mocked(fetchMcpServersOrNull).mockResolvedValue({ servers: [], file_error: 'mcp-servers.json is not valid JSON' })
  renderSection()
  expect((await screen.findByTestId('mcp-file-error')).textContent).toContain('not valid JSON')
  expect((screen.getByTestId('mcp-add-open') as HTMLButtonElement).disabled).toBe(true)
})
