import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import {
  COMPLETENESS_ORDER,
  completenessRows,
  dtcPlanningCsvRows,
  dtcStressCsvRows,
  EhReferenceDesignPanel,
  hasMultiEnergyBlock,
  leverCsvRows,
  multiEnergyCarrierEntries,
  multiEnergyLoadEntries,
  redundancyCsvRows,
  scrTone,
  statusTone,
} from './EhReferenceDesignPanel'
import { downloadCSV } from './shared'

vi.mock('./shared', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./shared')>()
  return { ...actual, downloadCSV: vi.fn() }
})

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
      getEhRedundancy: vi.fn(),
      getEhLevers: vi.fn(),
      getEhDtc: vi.fn(),
      getEhDtcPlanning: vi.fn(),
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
    multi_energy: 'skipped' as const,
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
  vi.mocked(resultsApi.getEhRedundancy).mockReset().mockResolvedValue(null)
  vi.mocked(resultsApi.getEhLevers).mockReset().mockResolvedValue(null)
  vi.mocked(resultsApi.getEhDtc).mockReset().mockResolvedValue(null)
  vi.mocked(resultsApi.getEhDtcPlanning).mockReset().mockResolvedValue(null)
  vi.mocked(downloadCSV).mockReset()
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

describe('SCR / EMT dynamics gate (P9)', () => {
  it('tones pass / warn / fail differently', () => {
    expect(scrTone('pass')).toContain('accent')
    expect(scrTone('warn')).toContain('warn')
    expect(scrTone('fail')).toContain('danger')
  })

  it('renders SCR warn + EMT recommended + min SCR from sections payload', async () => {
    const report = {
      ...REPORT,
      archetype: 'weak_flexible' as const,
      completeness: { ...REPORT.completeness, gates: 'ok' as const },
      gates: { scr: 'warn' as const, emt_recommended: true },
      sections: {
        gates: {
          status: 'ok' as const,
          payload: {
            method: 'eh_sk_mva_proxy',
            pass_scr: 3,
            min_scr: 2.5,
            min_bus: 'poc',
            warn_only: true,
          },
          note: 'SCR warn at poc (min SCR=2.5, threshold=3; warn-only thin slice)',
        },
      },
    }
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'weak_flexible', report,
    } as never)
    await openPanel()
    const block = await screen.findByTestId('eh-gates')
    expect(block).toBeTruthy()
    expect(screen.getByTestId('eh-gates-scr').textContent).toMatch(/warn/i)
    expect(screen.getByTestId('eh-gates-scr').getAttribute('data-scr')).toBe('warn')
    expect(screen.getByTestId('eh-gates-emt').textContent).toMatch(/yes|recommended/i)
    expect(screen.getByTestId('eh-gates-min-scr').textContent).toMatch(/2\.5/)
    expect(screen.getByTestId('eh-gates-note').textContent).toMatch(/warn-only/i)
  })

  it('renders SCR pass with EMT not recommended', async () => {
    const report = {
      ...REPORT,
      completeness: { ...REPORT.completeness, gates: 'ok' as const },
      gates: { scr: 'pass' as const, emt_recommended: false },
      sections: {
        gates: {
          status: 'ok' as const,
          payload: { method: 'eh_sk_mva_proxy', min_scr: 4.2, pass_scr: 3 },
          note: null,
        },
      },
    }
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'strong_grid', report,
    } as never)
    await openPanel()
    expect((await screen.findByTestId('eh-gates-scr')).getAttribute('data-scr'))
      .toBe('pass')
    expect(screen.getByTestId('eh-gates-emt').textContent).toMatch(/no/i)
    expect(screen.queryByTestId('eh-gates-note')).toBeNull()
  })

  it('does not invent SCR values when gates section is skipped', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'strong_grid', report: REPORT,
    } as never)
    await openPanel()
    await screen.findByTestId('eh-report')
    expect(screen.queryByTestId('eh-gates')).toBeNull()
    expect(screen.queryByTestId('eh-gates-scr')).toBeNull()
  })

  it('shows section note alone when gates are not_established without values', async () => {
    const report = {
      ...REPORT,
      completeness: { ...REPORT.completeness, gates: 'not_established' as const },
      gates: null,
      sections: {
        gates: {
          status: 'not_established' as const,
          payload: null,
          note: 'no eh_poc buses tagged for SCR gate',
        },
      },
    }
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'weak_flexible', report,
    } as never)
    await openPanel()
    expect(await screen.findByTestId('eh-gates')).toBeTruthy()
    expect(screen.queryByTestId('eh-gates-scr')).toBeNull()
    expect(screen.getByTestId('eh-gates-note').textContent)
      .toMatch(/no eh_poc/i)
  })
})


describe('EH sibling table CSV helpers', () => {
  it('maps redundancy options including selection', () => {
    const rows = redundancyCsvRows({
      options: [
        { scenario_id: 'base', status: 'ok', cost_at_target_eur: 100,
          achieved_ens_mwh: 1, meets_target: true },
        { scenario_id: 'n1_generation', status: 'ok', cost_at_target_eur: 120,
          achieved_ens_mwh: 0.5, meets_target: true },
      ],
      selection: { selected_id: 'base' },
    })
    expect(rows[0]).toEqual(['base', 'ok', 100, 1, 'yes', 'selected', ''])
    expect(rows[1][5]).toBe('')
  })

  it('maps lever and DtC rows', () => {
    expect(leverCsvRows({
      options: [{
        kind: 'storage_duration', value: 4, unit: 'h', status: 'ok',
        cost_at_target_eur: 50, achieved_ens_mwh: 0, meets_target: true,
      }],
    })[0][0]).toBe('storage_duration')
    expect(dtcStressCsvRows({
      contingencies: [{
        contingency: 'import', status: 'ok',
        critical_unserved_mwh: 2, noncritical_unserved_mwh: 9,
      }],
    })[0][0]).toBe('import')
    expect(dtcPlanningCsvRows({
      contingencies: [{
        contingency: 'import', status: 'optimal',
        cost_at_target_eur: 200, built_p_nom_mw: 15,
      }],
    })[0][3]).toBe(15)
  })
})

describe('EhReferenceDesignPanel — sibling tables', () => {
  const RED = {
    options: [
      { scenario_id: 'base', status: 'ok', cost_at_target_eur: 1e6,
        achieved_ens_mwh: 1, meets_target: true },
      { scenario_id: 'parallel_storage', status: 'ok', cost_at_target_eur: 1.2e6,
        achieved_ens_mwh: 0.5, meets_target: true },
    ],
    selection: { selected_id: 'base' },
  }
  const LEV = {
    kind: 'storage_duration',
    options: [
      { kind: 'storage_duration', value: 2, unit: 'h', status: 'ok',
        cost_at_target_eur: 900000, meets_target: true },
      { kind: 'storage_duration', value: 8, unit: 'h', status: 'ok',
        cost_at_target_eur: 1100000, meets_target: true },
    ],
    skipped_kinds: ['import_cap:no import Links'],
  }
  const DTC = {
    attribution: 'bus_aggregate_not_per_load',
    contingencies: [{
      contingency: 'grid_import', status: 'ok',
      critical_unserved_mwh: 3, noncritical_unserved_mwh: 12,
    }],
  }
  const DTC_PLAN = {
    mode: 'planning',
    contingencies: [{
      contingency: 'grid_import', status: 'optimal',
      cost_at_target_eur: 2e6, built_p_nom_mw: 40,
    }],
  }

  it('renders redundancy / levers / DtC tables when GETs return data', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'weak_flexible', report: REPORT,
    } as never)
    vi.mocked(resultsApi.getEhRedundancy).mockResolvedValue(RED as never)
    vi.mocked(resultsApi.getEhLevers).mockResolvedValue(LEV as never)
    vi.mocked(resultsApi.getEhDtc).mockResolvedValue(DTC as never)
    vi.mocked(resultsApi.getEhDtcPlanning).mockResolvedValue(DTC_PLAN as never)
    await openPanel()
    expect(await screen.findByTestId('eh-redundancy')).toBeTruthy()
    expect(screen.getByTestId('eh-redundancy-selected').textContent).toMatch(/base/)
    expect(screen.getByTestId('eh-redundancy-row-base').getAttribute('data-selected'))
      .toBe('true')
    expect(screen.getByTestId('eh-levers')).toBeTruthy()
    expect(screen.getByTestId('eh-levers-skipped').textContent).toMatch(/import_cap/)
    expect(screen.getByTestId('eh-dtc-stress')).toBeTruthy()
    expect(screen.getByTestId('eh-dtc-attribution').textContent)
      .toMatch(/bus_aggregate/)
    expect(screen.getByTestId('eh-dtc-planning')).toBeTruthy()
  })

  it('exports CSV for each sibling table', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'weak_flexible', report: REPORT,
    } as never)
    vi.mocked(resultsApi.getEhRedundancy).mockResolvedValue(RED as never)
    vi.mocked(resultsApi.getEhLevers).mockResolvedValue(LEV as never)
    vi.mocked(resultsApi.getEhDtc).mockResolvedValue(DTC as never)
    vi.mocked(resultsApi.getEhDtcPlanning).mockResolvedValue(DTC_PLAN as never)
    const user = await openPanel()
    await user.click(await screen.findByTestId('eh-redundancy-csv'))
    await user.click(screen.getByTestId('eh-levers-csv'))
    await user.click(screen.getByTestId('eh-dtc-stress-csv'))
    await user.click(screen.getByTestId('eh-dtc-planning-csv'))
    expect(downloadCSV).toHaveBeenCalledTimes(4)
    expect(vi.mocked(downloadCSV).mock.calls.map(c => c[0])).toEqual([
      'eh-redundancy.csv', 'eh-levers.csv', 'eh-dtc-stress.csv', 'eh-dtc-planning.csv',
    ])
  })
})


describe('hasMultiEnergyBlock / multiEnergyCarrierEntries', () => {
  it('hides skipped multi_energy', () => {
    expect(hasMultiEnergyBlock({
      ...REPORT,
      completeness: { ...REPORT.completeness, multi_energy: 'skipped' },
      sections: { multi_energy: { status: 'skipped', payload: null, note: null } },
    })).toBe(false)
  })

  it('lists carrier MWh from payload', () => {
    const report = {
      ...REPORT,
      completeness: { ...REPORT.completeness, multi_energy: 'ok' as const },
      sections: {
        multi_energy: {
          status: 'ok' as const,
          payload: {
            attribution: 'dedicated_bus_by_carrier',
            ens_by_carrier_mwh: { hydrogen: 80, electrical: 0 },
          },
          note: 'unmet by carrier',
        },
      },
    }
    expect(hasMultiEnergyBlock(report)).toBe(true)
    expect(multiEnergyCarrierEntries(report)).toEqual([
      { carrier: 'hydrogen', mwh: 80 },
      { carrier: 'electrical', mwh: 0 },
    ])
  })
})

describe('multi-energy ENS panel', () => {
  it('renders carrier ENS when multi_energy is ok', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done',
      report: {
        ...REPORT,
        completeness: { ...REPORT.completeness, multi_energy: 'ok' as const },
        sections: {
          multi_energy: {
            status: 'ok' as const,
            payload: {
              attribution: 'dedicated_bus_by_carrier',
              honesty: ['dedicated_bus_by_carrier'],
              ens_by_carrier_mwh: { hydrogen: 80.5 },
              violations: [],
            },
            note: 'unmet by carrier (dedicated-bus roll-up): hydrogen=80.5 MWh',
          },
        },
      },
    } as never)
    vi.mocked(resultsApi.getEhReferenceDesign).mockResolvedValue(null as never)
    await openPanel()
    const block = await screen.findByTestId('eh-multi-energy')
    expect(block).toBeTruthy()
    expect(screen.getByTestId('eh-multi-energy-hydrogen').textContent).toMatch(/80\.50/)
    expect(screen.getByTestId('eh-multi-energy-attribution').textContent)
      .toMatch(/dedicated_bus_by_carrier/)
    expect(screen.getByTestId('eh-multi-energy-note').textContent).toMatch(/hydrogen/)
  })

  it('does not render multi_energy when skipped', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done',
      report: REPORT,
    } as never)
    vi.mocked(resultsApi.getEhReferenceDesign).mockResolvedValue(null as never)
    await openPanel()
    await screen.findByTestId('eh-report')
    expect(screen.queryByTestId('eh-multi-energy')).toBeNull()
  })

  it('shows fail-closed note without inventing by_carrier totals', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done',
      report: {
        ...REPORT,
        completeness: { ...REPORT.completeness, multi_energy: 'not_established' as const },
        sections: {
          multi_energy: {
            status: 'not_established' as const,
            payload: {
              attribution: 'dedicated_bus_by_carrier',
              ens_by_carrier_mwh: null,
              violations: ['bus b: mixed load carriers'],
            },
            note: 'shared or mismatched bus/load carriers — multi-energy ENS not established',
          },
        },
      },
    } as never)
    vi.mocked(resultsApi.getEhReferenceDesign).mockResolvedValue(null as never)
    await openPanel()
    expect(await screen.findByTestId('eh-multi-energy')).toBeTruthy()
    expect(screen.queryByTestId('eh-multi-energy-by-carrier')).toBeNull()
    expect(screen.getByTestId('eh-multi-energy-note').textContent).toMatch(/not established/i)
  })
})


describe('P6b per-Load multi-energy disclosure', () => {
  it('lists ens_by_load_mwh when attribution is per_load_slack', async () => {
    const report = {
      ...REPORT,
      completeness: { ...REPORT.completeness, multi_energy: 'ok' as const },
      sections: {
        multi_energy: {
          status: 'ok' as const,
          payload: {
            attribution: 'per_load_slack',
            honesty: ['per_load_slack', 'shared_bus_supported'],
            ens_by_carrier_mwh: { hydrogen: 40 },
            ens_by_load_mwh: { l_h2: 40, industrial: 12.5 },
            violations: [],
          },
          note: 'unmet by carrier (per-Load slack): hydrogen=40 MWh',
        },
      },
    }
    expect(multiEnergyLoadEntries(report)).toEqual([
      { load: 'l_h2', mwh: 40 },
      { load: 'industrial', mwh: 12.5 },
    ])
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done',
      report,
    } as never)
    await openPanel()
    expect(await screen.findByTestId('eh-multi-energy-by-load')).toBeTruthy()
    expect(screen.getByTestId('eh-multi-energy-load-l_h2').textContent).toMatch(/40/)
    expect(screen.getByTestId('eh-multi-energy-attribution').textContent)
      .toMatch(/per_load_slack/)
  })
})
