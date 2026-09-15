import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import {
  COMPLETENESS_ORDER,
  completenessRows,
  EhReferenceDesignPanel,
  statusTone,
} from './EhReferenceDesignPanel'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getEhStudy: vi.fn(),
      startEhStudy: vi.fn(),
      abortEhStudy: vi.fn(),
      getEhReferenceDesign: vi.fn(),
    },
  }
})

const REPORT = {
  archetype: 'strong_grid' as const,
  pack_hash: 'abc',
  assumptions_hash: 'def',
  ens_cap_permyriad: 10,
  achieved_ens_permyriad: 8.5,
  cost_at_target_eur: 1_250_000,
  excludes_shed_cost: true,
  completeness: {
    target: 'ok' as const,
    cost: 'ok' as const,
    frontier: 'skipped' as const,
    sizing: 'ok' as const,
    redundancy: 'skipped' as const,
    levers: 'skipped' as const,
    dtc: 'skipped' as const,
    fmea_top: 'skipped' as const,
    tea: 'ok' as const,
    gates: 'not_established' as const,
  },
  tea: { lcoe_eur_per_mwh: 42, lcoh_eur_per_kg: null, notes: null },
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <EhReferenceDesignPanel />
    </QueryClientProvider>,
  )
}

async function openPanel() {
  const user = userEvent.setup()
  renderPanel()
  await user.click(screen.getByTestId('eh-reference-design-toggle'))
  return user
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(resultsApi.getEhStudy).mockReset().mockResolvedValue(null)
  vi.mocked(resultsApi.startEhStudy).mockReset()
    .mockResolvedValue({ status: 'running', study: 'eh_study', archetype: 'strong_grid' })
  vi.mocked(resultsApi.abortEhStudy).mockReset()
    .mockResolvedValue({ status: 'running', aborting: true })
  vi.mocked(resultsApi.getEhReferenceDesign).mockReset().mockResolvedValue(null)
})

afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('completenessRows', () => {
  it('keeps REPORT_SECTIONS order and appends unknowns', () => {
    const rows = completenessRows({
      gates: 'not_established',
      target: 'ok',
      extra: 'skipped',
    })
    expect(rows.map(r => r.name)).toEqual(['target', 'gates', 'extra'])
    expect(COMPLETENESS_ORDER[0]).toBe('target')
  })

  it('tones ok / skipped / not_established differently', () => {
    expect(statusTone('ok')).toContain('accent')
    expect(statusTone('skipped')).toContain('muted')
    expect(statusTone('not_established')).toContain('warn')
  })
})

describe('EhReferenceDesignPanel', () => {
  it('mounts collapsed and shows empty copy when opened with no study', async () => {
    const user = await openPanel()
    expect(screen.getByTestId('eh-reference-design-panel')).toBeTruthy()
    expect(await screen.findByTestId('eh-not-run')).toBeTruthy()
    expect(screen.queryByTestId('eh-abort')).toBeNull()
    expect((screen.getByTestId('eh-archetype') as HTMLSelectElement).value)
      .toBe('strong_grid')
    await user.selectOptions(screen.getByTestId('eh-archetype'), 'off_grid')
    expect((screen.getByTestId('eh-archetype') as HTMLSelectElement).value)
      .toBe('off_grid')
  })

  it('starts a study with the selected archetype', async () => {
    const user = await openPanel()
    await user.selectOptions(screen.getByTestId('eh-archetype'), 'weak_flexible')
    await user.click(screen.getByTestId('eh-run'))
    await waitFor(() =>
      expect(resultsApi.startEhStudy).toHaveBeenCalledWith({
        archetype: 'weak_flexible',
      }))
  })

  it('offers Abort only while running and calls the abort route', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'strong_grid', report: REPORT,
    } as never)
    await openPanel()
    await waitFor(() => expect(resultsApi.getEhStudy).toHaveBeenCalled())
    expect(screen.queryByTestId('eh-abort')).toBeNull()
    cleanup()

    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'running', study: 'eh_study', archetype: 'strong_grid', report: null,
    } as never)
    const user = await openPanel()
    await user.click(await screen.findByTestId('eh-abort'))
    await waitFor(() => expect(resultsApi.abortEhStudy).toHaveBeenCalledTimes(1))
  })

  it('renders report headlines and completeness when done', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'strong_grid', report: REPORT,
    } as never)
    await openPanel()
    expect(await screen.findByTestId('eh-report')).toBeTruthy()
    expect(screen.getByTestId('eh-report-archetype').textContent).toMatch(/strong_grid/)
    expect(screen.getByTestId('eh-report-ens-cap').textContent).toMatch(/10/)
    expect(screen.getByTestId('eh-report-cost').textContent).toMatch(/€/)
    expect(screen.getByTestId('eh-report-lcoe').textContent).toMatch(/€.*\/MWh/)
    expect(screen.getByTestId('eh-section-target').getAttribute('data-status'))
      .toBe('ok')
    expect(screen.getByTestId('eh-section-frontier').getAttribute('data-status'))
      .toBe('skipped')
    expect(screen.getByTestId('eh-section-gates').getAttribute('data-status'))
      .toBe('not_established')
  })

  it('says a stopped study is stopped', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'aborted', study: 'eh_study', archetype: 'strong_grid', report: REPORT,
    } as never)
    await openPanel()
    const note = (await screen.findByTestId('eh-aborted')).textContent ?? ''
    expect(note).toMatch(/stopped/i)
  })

  it('surfaces a mesh blocker from start errors', async () => {
    const e = new Error('Request failed with status code 409') as Error & {
      response?: { status: number; data: { detail: string } }
    }
    e.response = {
      status: 409,
      data: { detail: 'a frontier study is running — wait for it to finish' },
    }
    vi.mocked(resultsApi.startEhStudy).mockRejectedValue(e)
    const user = await openPanel()
    await user.click(screen.getByTestId('eh-run'))
    const blocked = await screen.findByTestId('eh-blocked')
    expect(blocked.textContent).toMatch(/frontier/i)
  })
})
