// The Project info page's "Derive lengths from geometry" toggle (map plan 2,
// M2): reads the per-project setting from the shared projects list, writes it
// through `projectsApi.updateSettings` as a partial PATCH, and carries the one
// line of help text that tells the user what the switch does.
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { useUIStore } from '../store/uiStore'
import { projectsApi } from '../api/projects'
import type { ProjectInfo } from '../api/types'
import OverviewPanel, { DERIVE_LENGTHS_HELP } from './OverviewPanel'

vi.mock('../api/projects', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/projects')>()
  return {
    ...actual,
    projectsApi: { ...actual.projectsApi, list: vi.fn(), updateSettings: vi.fn(), downloadBundle: vi.fn() },
  }
})

const info = (over: Partial<ProjectInfo> = {}): ProjectInfo => ({
  name: 'Demo', created_at: '2026-10-06T00:00:00Z', has_solver_config: false,
  bus_count: 3, snapshot_count: 24, objective: null, ...over,
})

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <OverviewPanel />
    </QueryClientProvider>,
  )
}

const toggle = () => screen.getByRole('switch', { name: /derive lengths from geometry/i })

beforeEach(() => {
  vi.mocked(projectsApi.list).mockReset()
  vi.mocked(projectsApi.updateSettings).mockReset()
  useUIStore.setState({ currentProject: 'Demo', readOnly: false, readOnlyReason: 'writable' })
  vi.spyOn(toast, 'error').mockImplementation(() => '')
  vi.spyOn(toast, 'success').mockImplementation(() => '')
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null })
})

it('shows the setting off by default, with its help text', async () => {
  vi.mocked(projectsApi.list).mockResolvedValue([info()])
  renderPanel()
  await waitFor(() => expect(toggle().getAttribute('aria-checked')).toBe('false'))
  expect(screen.getByText(DERIVE_LENGTHS_HELP)).toBeTruthy()
  expect(DERIVE_LENGTHS_HELP).toMatch(/bus drag, a route edit or an import/)
  expect(DERIVE_LENGTHS_HELP).toMatch(/impedance rescale/)
})

it('reflects a project whose setting is on', async () => {
  vi.mocked(projectsApi.list).mockResolvedValue([
    info({ name: 'Other' }),
    info({ settings: { derive_lengths_from_geometry: true } }),
  ])
  renderPanel()
  await waitFor(() => expect(toggle().getAttribute('aria-checked')).toBe('true'))
})

it('a click PATCHes only this flag for the current project and settles on the answer', async () => {
  vi.mocked(projectsApi.list).mockResolvedValue([info()])
  const on = info({ settings: { derive_lengths_from_geometry: true } })
  vi.mocked(projectsApi.updateSettings).mockImplementation(async () => {
    // From here the server lists the project with the flag on, as it would.
    vi.mocked(projectsApi.list).mockResolvedValue([on])
    return on
  })
  renderPanel()
  await waitFor(() => expect(toggle().getAttribute('aria-checked')).toBe('false'))

  await userEvent.click(toggle())

  await waitFor(() => expect(projectsApi.updateSettings).toHaveBeenCalledTimes(1))
  expect(projectsApi.updateSettings).toHaveBeenCalledWith('Demo', { derive_lengths_from_geometry: true })
  await waitFor(() => expect(toggle().getAttribute('aria-checked')).toBe('true'))
  expect(toast.success).toHaveBeenCalledWith(expect.stringMatching(/on$/))
})

it('a refused PATCH leaves the switch where it was and names the reason', async () => {
  vi.mocked(projectsApi.list).mockResolvedValue([info()])
  vi.mocked(projectsApi.updateSettings).mockRejectedValue(
    { isAxiosError: true, response: { status: 409, data: { detail: "Save 'Demo' before changing its settings." } } },
  )
  renderPanel()
  await waitFor(() => expect(toggle().getAttribute('aria-checked')).toBe('false'))

  await userEvent.click(toggle())

  // Named, not counted: this page's other queries fail in jsdom and the
  // axios interceptor toasts each one (see OverviewPanel.download.test.tsx).
  const ours = () => vi.mocked(toast.error).mock.calls.map(c => String(c[0])).filter(m => /Save 'Demo'/.test(m))
  await waitFor(() => expect(ours()).toHaveLength(1))
  expect(ours()[0]).toMatch(/Could not change the setting/)
  expect(toggle().getAttribute('aria-checked')).toBe('false')
})

it('is inert while the project is read-only', async () => {
  vi.mocked(projectsApi.list).mockResolvedValue([info()])
  useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
  renderPanel()
  await waitFor(() => expect(toggle().getAttribute('aria-checked')).toBe('false'))

  await userEvent.click(toggle())

  expect(projectsApi.updateSettings).not.toHaveBeenCalled()
})

// ── AS-17c: the project's PoC X/R ──────────────────────────────────────────
const xr = () => screen.getByRole('textbox', { name: /poc x\/r/i }) as HTMLInputElement

it('PoC X/R: empty shows the default as placeholder and the range hint', async () => {
  vi.mocked(projectsApi.list).mockResolvedValue([info()])
  renderPanel()
  await waitFor(() => expect(xr().value).toBe(''))
  expect(xr().placeholder).toBe('4 (default)')
  expect(screen.getByText(/0 < X\/R ≤ 100\. Empty: the default 4 applies\./)).toBeTruthy()
})

it('PoC X/R: a value saves on blur, PATCHes only poc_xr, and shows the stored value', async () => {
  vi.mocked(projectsApi.list).mockResolvedValue([info()])
  const saved = info({ settings: { poc_xr: 7.5 } })
  vi.mocked(projectsApi.updateSettings).mockImplementation(async () => {
    vi.mocked(projectsApi.list).mockResolvedValue([saved])
    return saved
  })
  renderPanel()
  await waitFor(() => expect(xr().value).toBe(''))
  await userEvent.type(xr(), '7.5')
  await userEvent.tab()
  await waitFor(() => expect(projectsApi.updateSettings).toHaveBeenCalledWith('Demo', { poc_xr: 7.5 }))
  await waitFor(() => expect(xr().value).toBe('7.5'))
})

it('PoC X/R: clearing a stored value sends null', async () => {
  vi.mocked(projectsApi.list).mockResolvedValue([info({ settings: { poc_xr: 7.5 } })])
  vi.mocked(projectsApi.updateSettings).mockResolvedValue(info())
  renderPanel()
  await waitFor(() => expect(xr().value).toBe('7.5'))
  await userEvent.clear(xr())
  await userEvent.tab()
  await waitFor(() => expect(projectsApi.updateSettings).toHaveBeenCalledWith('Demo', { poc_xr: null }))
})

it('PoC X/R: an out-of-range value is refused inline without a request', async () => {
  vi.mocked(projectsApi.list).mockResolvedValue([info()])
  renderPanel()
  await waitFor(() => expect(xr().value).toBe(''))
  await userEvent.type(xr(), '150')
  await userEvent.tab()
  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(xr().getAttribute('aria-invalid')).toBe('true')
  expect(xr().value).toBe('150')
  expect(projectsApi.updateSettings).not.toHaveBeenCalled()
})

it('PoC X/R: the API 422 reason shows inline under the field and the typed value stays', async () => {
  vi.mocked(projectsApi.list).mockResolvedValue([info()])
  vi.mocked(projectsApi.updateSettings).mockRejectedValue(
    { isAxiosError: true, response: { status: 422, data: { detail: 'PoC X/R must be above 0 and at most 100.' } } },
  )
  renderPanel()
  await waitFor(() => expect(xr().value).toBe(''))
  await userEvent.type(xr(), '5')
  await userEvent.tab()
  const alert = await screen.findByRole('alert')
  expect(alert.textContent).toMatch(/must be above 0 and at most 100/)
  expect(xr().getAttribute('aria-invalid')).toBe('true')
  expect(xr().value).toBe('5')
})

it('PoC X/R: disabled while read-only, with the reason as visible text', async () => {
  vi.mocked(projectsApi.list).mockResolvedValue([info()])
  useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
  renderPanel()
  await waitFor(() => expect(xr().disabled).toBe(true))
  expect(screen.getByText(/read-only/i)).toBeTruthy()
})
