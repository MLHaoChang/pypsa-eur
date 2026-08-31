// Fix round 1, item (3) — Task 15's Settings nav row gates on
// `useLocalSettingsAvailable() || useLLMSettingsAvailable()` (Sidebar.tsx),
// but no test ever exercised the llm-reachable-only branch: every existing
// mock left `fetchLLMSettingsOrNull` unmocked (→ a real, failing network
// call → unreachable), so the OR always happened to resolve via
// local-settings alone. That leaves the entire point of the OR — a web
// deployment where local-settings 404s but a super-admin can still reach
// AssistantModelSettings — uncovered. This file pins that branch, plus the
// existing local-only and both-unavailable branches for a complete matrix.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { fetchLocalSettings, type LocalSettingsState } from '../api/localSettings'
import { fetchLLMSettingsOrNull, type LLMSettingsPayload } from '../api/llmSettings'
import Sidebar from './Sidebar'

vi.mock('../api/network', () => ({
  networkApi: { getMeta: vi.fn().mockResolvedValue({ bus_count: 3 }) },
}))
vi.mock('../api/io', () => ({ ioApi: {} }))
vi.mock('../api/projects', () => ({
  projectsApi: { list: vi.fn().mockResolvedValue([]), save: vi.fn() },
}))
vi.mock('../api/simulation', () => ({
  simulationApi: { preflight: vi.fn().mockResolvedValue({ ok: true, errors: 0, warnings: 0, issues: [] }) },
}))
vi.mock('../api/solveQueue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/solveQueue')>()
  return { ...actual, solveQueueApi: { list: vi.fn().mockResolvedValue({ jobs: [] }) } }
})
vi.mock('../api/localSettings', async (orig) => ({
  ...(await orig<typeof import('../api/localSettings')>()),
  fetchLocalSettings: vi.fn(),
}))
vi.mock('../api/llmSettings', async (orig) => ({
  ...(await orig<typeof import('../api/llmSettings')>()),
  fetchLLMSettingsOrNull: vi.fn(),
}))
vi.mock('../pages/ImportExport', () => ({ ImportZone: () => <div /> }))
vi.mock('./NewProjectWizard', () => ({ default: () => <div /> }))
vi.mock('../components/ProjectPicker', () => ({ default: () => <div /> }))
vi.mock('../utils/projectActions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../utils/projectActions')>()
  return {
    ...actual,
    invalidateNetworkQueries: vi.fn(),
    saveProjectQuietly: vi.fn(),
    resetBackendNetwork: vi.fn(),
    downloadProjectBundle: vi.fn(),
    abortRunningSim: vi.fn(),
    switchToProject: vi.fn(),
  }
})

const LOCAL_STATE: LocalSettingsState = { key_set: false, key_hint: null, log_path: '/tmp/app.log' }
const LLM_PAYLOAD: LLMSettingsPayload = {
  active_profile_id: 'anthropic-sonnet',
  profiles: [],
  presets: [],
}

function renderSidebar() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  cleanup()
  vi.clearAllMocks()
  useUIStore.setState({
    sidebarMode: 'expanded',
    currentProject: 'Demo',
    projectName: 'Demo',
    activeSlidePanel: null,
    assistantDockOpen: false,
  })
})

describe('Sidebar Settings row — gates on EITHER surface (Task 15)', () => {
  it('is absent when neither local-settings nor llm-settings is reachable', async () => {
    vi.mocked(fetchLocalSettings).mockResolvedValue(null)
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(null)
    renderSidebar()

    // Confirms the sidebar actually rendered before asserting absence.
    await screen.findByText('Solver Settings')
    expect(screen.queryByRole('button', { name: 'Settings' })).toBeNull()
  })

  it('is present when ONLY local-settings is reachable (desktop app, pre-existing branch)', async () => {
    vi.mocked(fetchLocalSettings).mockResolvedValue(LOCAL_STATE)
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(null)
    renderSidebar()

    expect(await screen.findByRole('button', { name: 'Settings' })).toBeTruthy()
  })

  it('is present when ONLY llm-settings is reachable — a web super-admin, local-settings 404s', async () => {
    // The branch nothing previously covered: every other test in this suite
    // left fetchLLMSettingsOrNull unmocked, so it always resolved false and
    // this leg of the OR was never actually exercised.
    vi.mocked(fetchLocalSettings).mockResolvedValue(null)
    vi.mocked(fetchLLMSettingsOrNull).mockResolvedValue(LLM_PAYLOAD)
    renderSidebar()

    expect(await screen.findByRole('button', { name: 'Settings' })).toBeTruthy()
  })
})
