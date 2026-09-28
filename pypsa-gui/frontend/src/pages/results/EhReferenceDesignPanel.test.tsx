import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import {
  COMPLETENESS_ORDER,
  certificationPayload,
  completenessRows,
  dtcPlanningCsvRows,
  dtcStressCsvRows,
  EhReferenceDesignPanel,
  fmeaImportRankingNote,
  fmeaTopCsvRows,
  fmeaTopModes,
  frontierCsvRows,
  frontierPoints,
  hasCertificationBlock,
  hasMultiEnergyBlock,
  gridAreasSummary,
  commonModeLines,
  coptImportSummary,
  fmeaCoptNotes,
  importModelLabel,
  lcohChip,
  leverCsvRows,
  multiEnergyCarrierEntries,
  multiEnergyLoadEntries,
  redundancyCsvRows,
  scrTone,
  statusTone,
  verdictTone,
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
    certification: 'skipped' as const,
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


describe('wired stages — certification / frontier / fmea_top / LCOH (2026-09-26)', () => {
  const CERT = {
    metric: 'mc_lole', target_lole_h: 3, mc_lole_h: 1.25, lole_ci: [0.9, 1.6],
    eue_mwh: 12.5, n_samples: 200, draws_requested: 200, converged: true,
    ens_met: true, verdict: 'certified' as const,
    warning: 'Sequential MC results rest on ONE weather realisation',
  }
  const FRONTIER = {
    targets_permyriad: [40, 20, 10, 5, 2.5],
    points: [
      { target_permyriad: 40, status: 'ok', excludes_shed_cost: true, period_basis: 'single_period',
        binding: 'system_cap', point: { total_system_cost_eur: 900000, achieved_ens_mwh: 40, achieved_shed_hours: 3 } },
      { target_permyriad: 20, status: 'ok', excludes_shed_cost: true, period_basis: 'single_period',
        binding: 'system_cap', point: { total_system_cost_eur: 950000, achieved_ens_mwh: 20, achieved_shed_hours: 2 } },
      { target_permyriad: 10, status: 'ok', excludes_shed_cost: true, period_basis: 'single_period',
        binding: 'system_cap', point: { total_system_cost_eur: 1250000, achieved_ens_mwh: 10, achieved_shed_hours: 1 } },
      { target_permyriad: 5, status: 'infeasible', excludes_shed_cost: true, point: null },
    ],
    knee_index: 1,
    period_basis: 'single_period',
    excludes_shed_cost: true,
    warning: null,
    base_restored: true,
  }
  const FMEA = {
    top: [
      { rank: 1, mode_id: 'link:import_poc:forced_outage', component_class: 'Link',
        name: 'import_poc', failure_class: 'B', criticality_eur_per_year: 52000,
        delta_eue_mwh: 120, occurrence_per_year: 5.5, severity_eur: 9454, engine: 'lp_proxy' },
      { rank: 2, mode_id: 'generator:base:forced_outage', component_class: 'Generator',
        name: 'base', failure_class: 'A', criticality_eur_per_year: 31000,
        delta_eue_mwh: 80, occurrence_per_year: 8.7, severity_eur: 3563, engine: 'copt' },
    ],
    top_n: 10, n_total_modes: 3, classes_included: ['A', 'B'],
    class_b: { status: 'run', rows: 1, base_restored: true },
    note: 'Link-primary residual risk (Class-B Link sweep); AC Line/Transformer N-1 remains on SCLOPF and is omitted from FMEA ranking',
  }
  const FULL = {
    ...REPORT,
    archetype: 'weak_flexible' as const,
    mc_lole_h: 1.25,
    completeness: {
      ...REPORT.completeness, certification: 'ok' as const, frontier: 'ok' as const,
      fmea_top: 'ok' as const,
    },
    sections: {
      certification: { status: 'ok' as const, payload: CERT,
        note: 'MC LOLE 1.25 h ≤ target 3 h — certified' },
      frontier: { status: 'ok' as const, payload: FRONTIER, note: '3 points around 10‱' },
      fmea_top: { status: 'ok' as const, payload: FMEA,
        note: 'Link-primary residual risk (Class-B Link sweep); AC Line/Transformer N-1 remains on SCLOPF and is omitted from FMEA ranking; top 2 of 3 modes, classes A+B' },
    },
    tea: { lcoe_eur_per_mwh: 42, lcoh_eur_per_kg: 4.87, notes: null,
      lcoh_status: 'ok' as const, lcoh_note: 'LCOH = fleet cost / H₂ produced' },
  }

  it('orders certification right after target in the completeness chips', () => {
    expect(COMPLETENESS_ORDER[1]).toBe('certification')
    const rows = completenessRows({ cost: 'ok', certification: 'ok', target: 'ok' })
    expect(rows.map(r => r.name)).toEqual(['target', 'certification', 'cost'])
  })

  it('tones verdicts: certified accent, failed danger, no_target muted, else warn', () => {
    expect(verdictTone('certified')).toContain('accent')
    expect(verdictTone('failed')).toContain('danger')
    expect(verdictTone('no_target')).toContain('muted')
    expect(verdictTone('not_established')).toContain('warn')
    expect(verdictTone(undefined)).toContain('warn')
  })

  it('extracts payloads and maps CSV rows', () => {
    expect(certificationPayload(FULL)?.verdict).toBe('certified')
    expect(certificationPayload(REPORT)).toBeNull()
    expect(frontierPoints(FULL)).toHaveLength(4)
    expect(frontierPoints(REPORT)).toEqual([])
    expect(frontierCsvRows(frontierPoints(FULL))[0]).toEqual(
      [40, 'ok', 900000, 40, 3, 'system_cap', 'single_period', 'yes'])
    expect(frontierCsvRows(frontierPoints(FULL))[3][2]).toBe('')
    expect(fmeaTopModes(FULL).map(m => m.rank)).toEqual([1, 2])
    expect(fmeaTopCsvRows(fmeaTopModes(FULL))[0].slice(0, 4))
      .toEqual([1, 'B', 'Link', 'import_poc'])
    expect(lcohChip(FULL)).toEqual({ value: 4.87, status: 'ok', note: 'LCOH = fleet cost / H₂ produced' })
    expect(lcohChip({ ...REPORT, tea: { lcoe_eur_per_mwh: 1, lcoh_eur_per_kg: null,
      lcoh_status: 'skipped', lcoh_note: 'no electrolyser Links' } }))
      .toEqual({ value: null, status: 'skipped', note: 'no electrolyser Links' })
    expect(lcohChip(REPORT)).toBeNull()  // pre-flag report: nothing invented
  })

  it('hides the certification block when the section is skipped', () => {
    expect(hasCertificationBlock({
      ...REPORT,
      sections: { certification: { status: 'skipped', payload: null, note: 'mc_certify not requested' } },
    })).toBe(false)
    expect(hasCertificationBlock(FULL)).toBe(true)
  })

  it('renders MC LOLE, the verdict, LCOH, the frontier and the FMEA top-N', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'weak_flexible', report: FULL,
    } as never)
    const user = await openPanel()
    expect(await screen.findByTestId('eh-report')).toBeTruthy()
    expect(screen.getByTestId('eh-report-mc-lole').textContent).toMatch(/1\.25 h/)
    expect(screen.getByTestId('eh-report-mc-lole').textContent).toMatch(/target 3 h/)
    const verdict = screen.getByTestId('eh-report-verdict')
    expect(verdict.getAttribute('data-verdict')).toBe('certified')
    expect(verdict.className).toContain('accent')
    expect(screen.getByTestId('eh-report-lcoh').textContent).toMatch(/€4\.87\/kg/)
    expect(screen.queryByTestId('eh-report-lcoh-flag')).toBeNull()
    expect(screen.getByTestId('eh-section-certification').getAttribute('data-status')).toBe('ok')

    expect(screen.getByTestId('eh-certification')).toBeTruthy()
    expect(screen.getByTestId('eh-certification-lole').textContent).toMatch(/\[0\.90, 1\.60\]/)
    expect(screen.getByTestId('eh-certification-ens').textContent).toMatch(/met/)
    expect(screen.getByTestId('eh-certification-warning').textContent).toMatch(/ONE weather/)

    expect(screen.getByTestId('eh-frontier')).toBeTruthy()
    expect(screen.getByTestId('eh-frontier-basis').textContent).toMatch(/excl\. shed/)
    expect(screen.getByTestId('eh-frontier-basis').textContent).toMatch(/single_period/)
    expect(screen.getByTestId('eh-frontier-row-1').getAttribute('data-knee')).toBe('true')
    expect(screen.getByTestId('eh-frontier-row-0').getAttribute('data-knee')).toBe('false')
    expect(screen.getByTestId('eh-frontier-row-3').textContent).toMatch(/infeasible/)
    expect(screen.getByTestId('eh-frontier-row-3').textContent).toMatch(/—/)

    expect(screen.getByTestId('eh-fmea-top')).toBeTruthy()
    expect(screen.getByTestId('eh-fmea-top-row-1').getAttribute('data-class')).toBe('B')
    expect(screen.getByTestId('eh-fmea-top-row-1').textContent).toMatch(/import_poc/)
    expect(screen.getByTestId('eh-fmea-top-row-2').getAttribute('data-class')).toBe('A')
    expect(screen.getByTestId('eh-fmea-top-note').textContent).toMatch(/Link-primary/)
    expect(screen.getByTestId('eh-fmea-top-note').textContent).toMatch(/SCLOPF/)

    await user.click(screen.getByTestId('eh-frontier-csv'))
    await user.click(screen.getByTestId('eh-fmea-top-csv'))
    expect(vi.mocked(downloadCSV).mock.calls.map(c => c[0]))
      .toEqual(['eh-frontier.csv', 'eh-fmea-top.csv'])
  })

  it('shows a failed verdict in danger tone with the decision-2 note', async () => {
    const report = {
      ...FULL,
      mc_lole_h: 412.5,
      sections: {
        ...FULL.sections,
        certification: {
          status: 'ok' as const,
          payload: { ...CERT, mc_lole_h: 412.5, verdict: 'failed' as const },
          note: 'MC LOLE 412 h > target 3 h — certification FAILED although the ENS target is met (spec decision 2)',
        },
      },
    }
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'off_grid', report,
    } as never)
    await openPanel()
    const verdict = await screen.findByTestId('eh-report-verdict')
    expect(verdict.getAttribute('data-verdict')).toBe('failed')
    expect(verdict.className).toContain('danger')
    expect(screen.getByTestId('eh-certification-note').textContent).toMatch(/decision 2/)
  })

  it('shows the LCOH flag, not a zero, when there is no electrolyser', async () => {
    const report = {
      ...REPORT,
      tea: { lcoe_eur_per_mwh: 42, lcoh_eur_per_kg: null, notes: null,
        lcoh_status: 'skipped' as const, lcoh_note: 'LCOH skipped: the network has no electrolyser Links' },
    }
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'strong_grid', report,
    } as never)
    await openPanel()
    await screen.findByTestId('eh-report')
    expect(screen.queryByTestId('eh-report-lcoh')).toBeNull()
    const flag = screen.getByTestId('eh-report-lcoh-flag')
    expect(flag.getAttribute('data-status')).toBe('skipped')
    expect(flag.textContent).toMatch(/no electrolyser/)
    expect(flag.textContent).not.toMatch(/0\.00/)
  })

  it('shows the not_established certification reason without inventing a LOLE', async () => {
    const report = {
      ...REPORT,
      completeness: { ...REPORT.completeness, certification: 'not_established' as const },
      sections: {
        certification: {
          status: 'not_established' as const, payload: null,
          note: 'nothing to sample: no electrical generator carries resolvable occurrence data',
        },
      },
    }
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'weak_flexible', report,
    } as never)
    await openPanel()
    expect(await screen.findByTestId('eh-certification')).toBeTruthy()
    expect(screen.queryByTestId('eh-report-mc-lole')).toBeNull()
    expect(screen.queryByTestId('eh-certification-lole')).toBeNull()
    expect(screen.getByTestId('eh-certification-note').textContent).toMatch(/occurrence/)
  })

  it('renders none of the new blocks on a pre-wiring report', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'strong_grid', report: REPORT,
    } as never)
    await openPanel()
    await screen.findByTestId('eh-report')
    expect(screen.queryByTestId('eh-certification')).toBeNull()
    expect(screen.queryByTestId('eh-frontier')).toBeNull()
    expect(screen.queryByTestId('eh-fmea-top')).toBeNull()
    expect(screen.queryByTestId('eh-report-mc-lole')).toBeNull()
    expect(screen.queryByTestId('eh-report-lcoh')).toBeNull()
    expect(screen.queryByTestId('eh-report-lcoh-flag')).toBeNull()
  })
})

describe('import model in the certification (2026-09-27)', () => {
  const scope = (over: Record<string, unknown> = {}) => ({
    mode: 'hub_side' as const, import_links: ['import_poc'],
    import_cap_mw_max: 50, import_firm_mw_max: null,
    note: 'MC / COPT fleet restricted to the hub side of import Link(s) import_poc',
    ...over,
  })
  const cert = (over: Record<string, unknown> = {}) => ({
    metric: 'mc_lole', target_lole_h: 3, mc_lole_h: 585.28, verdict: 'failed' as const,
    n_samples: 200, ens_met: true, ...over,
  })

  it('labels each import model and never invents one', () => {
    expect(importModelLabel(cert({ import_model: 'zonal', fleet_scope: scope() })))
      .toMatch(/grid-side surplus sampled.*cap 50 MW/)
    expect(importModelLabel(cert({ import_model: 'sampled_unit', fleet_scope: scope() })))
      .toMatch(/Link outages sampled/)
    expect(importModelLabel(cert({ import_model: 'islanded',
      fleet_scope: scope({ import_cap_mw_max: 0, import_firm_mw_max: 0 }) })))
      .toBe('islanded (0 MW)')
    expect(importModelLabel(cert({ import_model: 'firm_block',
      fleet_scope: scope({ import_firm_mw_max: 50 }) }))).toMatch(/firm at the planning limit/)
    expect(importModelLabel(cert({ import_model: 'mixed', fleet_scope: scope() })))
      .toMatch(/partly sampled/)
    // A pre-2026-09-27 report: hub-side scope, no import_model → it WAS firm.
    expect(importModelLabel(cert({ fleet_scope: { mode: 'hub_side',
      import_firm_mw_max: 50, import_firmness: 'planning_limit_only' } })))
      .toMatch(/firm at the planning limit.*50 MW/)
    // Whole-network scope / no scope: nothing to say.
    expect(importModelLabel(cert({ fleet_scope: { mode: 'whole_network' } }))).toBeNull()
    expect(importModelLabel(cert())).toBeNull()
    expect(importModelLabel(null)).toBeNull()
  })

  it('states how the sampled Link is ranked in fmea_top', () => {
    const base = { ...REPORT, sections: { fmea_top: { status: 'ok' as const,
      payload: { top: [], import_link_ranking: { import_poc: 'class_b' },
        import_link_ranking_note: 'ranked ONCE' }, note: null } } }
    expect(fmeaImportRankingNote(base)).toMatch(/import_poc: Class-B row.*ranked ONCE/)
    expect(fmeaImportRankingNote(REPORT)).toBeNull()
  })

  it('renders the import line and the fleet-scope note in the certification block', async () => {
    const report = {
      ...REPORT,
      archetype: 'weak_flexible' as const,
      mc_lole_h: 585.28,
      completeness: { ...REPORT.completeness, certification: 'ok' as const },
      sections: {
        certification: { status: 'ok' as const, note: 'MC LOLE 585 h > target 3 h',
          payload: cert({ import_model: 'zonal', import_firmness: 'outage_and_grid_sampled',
            fleet_scope: scope({ import_model: 'zonal' }) }) },
      },
    }
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'weak_flexible', report,
    } as never)
    await openPanel()
    const line = await screen.findByTestId('eh-certification-import')
    expect(line.getAttribute('data-import-model')).toBe('zonal')
    expect(line.textContent).toMatch(/two-area MC/)
    expect(screen.getByTestId('eh-certification-scope-note').textContent)
      .toMatch(/hub side/)
  })
})

describe('zonal open items: grid areas, COPT import, common mode (2026-09-28)', () => {
  const area = (over: Record<string, unknown> = {}) => ({
    area: 0, links: ['poc_a'], sampled: true, reason: null, units: ['gen_a'],
    n_units: 1, capacity_mw: 200, demand_peak_mw: 170, storage: [],
    storage_dispatched: false, copt_surplus_fraction_min: 0.4, note: null, ...over,
  })

  it('summarises grid areas: count, sampled, storage, and unsampled reasons', () => {
    expect(gridAreasSummary(null)).toBeNull()
    expect(gridAreasSummary({ mode: 'hub_side', grid_areas: [] })).toBeNull()
    const two = gridAreasSummary({ mode: 'hub_side', grid_areas: [
      area({ storage: ['grid_bat'], storage_dispatched: true }),
      area({ area: 1, links: ['poc_b'], sampled: false, units: [], n_units: 0,
        capacity_mw: null, demand_peak_mw: null, reason: 'no occurrence data' }),
    ] })
    expect(two).toMatch(/2 grid areas, 1 sampled/)
    expect(two).toMatch(/grid storage dispatched: grid_bat/)
    expect(two).toMatch(/poc_b.*unbounded.*no occurrence data/)
  })

  it('lists common-mode events, applied or with the reason they are not', () => {
    expect(commonModeLines(null)).toEqual([])
    expect(commonModeLines({ mode: 'hub_side', import_common_mode: [
      { link: 'poc', rate: 0.05, mttr_hours: 24, basis: 'FOR', area: 0,
        applied: true, reason: null },
      { link: 'poc2', rate: 0.1, mttr_hours: null, basis: 'FOR', area: null,
        applied: false, reason: 'no MTTR' },
    ] })).toEqual([
      'poc: common-mode q=0.05 (FOR), MTTR 24 h — Link and grid area down together',
      'poc2: common-mode data not applied — no MTTR',
    ])
  })

  it('states the COPT import model and the exact import LOLE, never inventing one', () => {
    expect(coptImportSummary(null, null)).toBeNull()
    expect(coptImportSummary({ mode: 'hub_side', copt_import_model: 'two_state' }, null))
      .toBe('COPT screening: import Link as a two-state unit')
    const s = coptImportSummary(
      { mode: 'hub_side', copt_import_model: 'expected_surplus_profile' },
      { import_exact: { lole_hours: 576.1, eue_mwh: 11000, delta_mw: 1 } })
    expect(s).toMatch(/expected grid surplus/)
    expect(s).toMatch(/exact import LOLE 576\.10 h/)
    // An exact metric that could not be computed is null: no number shown.
    expect(coptImportSummary(
      { mode: 'hub_side', copt_import_model: 'expected_surplus_profile' },
      { import_exact: { lole_hours: null, eue_mwh: null } })).not.toMatch(/exact import LOLE/)
  })

  it('renders the three lines in the certification and FMEA blocks', async () => {
    const scope = {
      mode: 'hub_side' as const, import_model: 'zonal' as const,
      import_cap_mw_max: 80, import_firm_mw_max: null,
      copt_import_model: 'expected_surplus_profile' as const,
      grid_areas: [area({ storage: ['grid_bat'], storage_dispatched: true })],
      import_common_mode: [{ link: 'poc_a', rate: 0.05, mttr_hours: 24, basis: 'FOR',
        area: 0, applied: true, reason: null }],
      note: 'hub side',
    }
    const report = {
      ...REPORT,
      archetype: 'weak_flexible' as const,
      mc_lole_h: 600,
      completeness: { ...REPORT.completeness, certification: 'ok' as const, fmea_top: 'ok' as const },
      sections: {
        certification: { status: 'ok' as const, note: 'failed',
          payload: { metric: 'mc_lole', mc_lole_h: 600, target_lole_h: 3, verdict: 'failed' as const,
            import_model: 'zonal', fleet_scope: scope } },
        fmea_top: { status: 'ok' as const, note: 'Link-primary',
          payload: { top: [{ rank: 1, mode_id: 'generator:base:forced_outage',
            component_class: 'Generator', name: 'base', failure_class: 'A',
            criticality_eur_per_year: 1 }], fleet_scope: scope,
            copt_metrics: { lole_hours: 802, import_exact: { lole_hours: 576.1, eue_mwh: 1 } } } },
      },
    }
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'weak_flexible', report,
    } as never)
    await openPanel()
    expect((await screen.findByTestId('eh-certification-grid-areas')).textContent)
      .toMatch(/1 grid area, 1 sampled/)
    expect(screen.getByTestId('eh-certification-common-mode').textContent)
      .toMatch(/poc_a: common-mode q=0.05/)
    expect(screen.getByTestId('eh-fmea-top-copt-import').textContent)
      .toMatch(/exact import LOLE 576\.10 h/)
  })
})

describe('review of WP5: labels that do not hide an event or a firm Link', () => {
  it('labels a firm block under a sampled event and a firm Link behind a sampled grid', () => {
    expect(importModelLabel({ metric: 'mc_lole', import_model: 'firm_block',
      import_firmness: 'common_mode_sampled',
      fleet_scope: { mode: 'hub_side', import_cap_mw_max: 50 } }))
      .toMatch(/firm block; common-mode event sampled/)
    expect(importModelLabel({ metric: 'mc_lole', import_model: 'zonal',
      import_firmness: 'grid_sampled',
      fleet_scope: { mode: 'hub_side', import_cap_mw_max: 50 } }))
      .toMatch(/grid-side surplus sampled; Links firm/)
    expect(importModelLabel({ metric: 'mc_lole', import_model: 'sampled_unit',
      import_firmness: 'outage_and_common_mode_sampled',
      fleet_scope: { mode: 'hub_side', import_cap_mw_max: 50 } }))
      .toMatch(/Link outages \+ common-mode event sampled/)
  })

  it('says the screening mixes events, and when the exact metric is rounded', () => {
    expect(coptImportSummary({ mode: 'hub_side', copt_import_model: 'firm_block',
      copt_common_mode: 'event_mixture' }, null))
      .toMatch(/firm block.*common-mode events mixed exactly/)
    expect(coptImportSummary(
      { mode: 'hub_side', copt_import_model: 'expected_surplus_profile' },
      { import_exact: { lole_hours: 10, delta_mw: 4 } }))
      .toMatch(/import rounded down to 4 MW levels/)
    expect(coptImportSummary(
      { mode: 'hub_side', copt_import_model: 'expected_surplus_profile' },
      { import_exact: { lole_hours: 10, delta_mw: 1 } })).not.toMatch(/rounded/)
  })

  it('shows the COPT caveat and fidelity notes, and nothing on an older report', () => {
    const report = { ...REPORT, sections: { fmea_top: { status: 'ok' as const, note: null,
      payload: { top: [], copt_fidelity_note: 'areas [6] held UP',
        fleet_scope: { mode: 'hub_side' as const,
          copt_import_note: 'may over- or under-state LOLE against the MC' } } } } }
    expect(fmeaCoptNotes(report)).toEqual([
      'may over- or under-state LOLE against the MC', 'areas [6] held UP'])
    expect(fmeaCoptNotes(REPORT)).toEqual([])
  })

  it('does not call a zero-rate event an event, and keeps ignored entries', () => {
    expect(commonModeLines({ mode: 'hub_side', import_common_mode: [
      { link: 'p', rate: 0, mttr_hours: 24, basis: 'FOR', area: 0, applied: true, reason: null },
      { link: 'x', rate: 0.1, mttr_hours: null, basis: 'FOR', area: null, applied: false,
        reason: 'whole-network scope (no identified hub boundary) — common-mode data not modelled' },
    ] })).toEqual([
      'p: common-mode q=0 — no effect',
      'x: common-mode data not applied — whole-network scope (no identified hub boundary) — common-mode data not modelled',
    ])
  })

  it('does not list idle grid storage as dispatched', () => {
    expect(gridAreasSummary({ mode: 'hub_side', grid_areas: [{
      area: 0, links: ['a'], sampled: true, units: ['g'], n_units: 1, capacity_mw: 1,
      demand_peak_mw: 1, storage: ['bat'], storage_dispatched: false }] }))
      .not.toMatch(/dispatched/)
  })

  it('renders a CommonMode FMEA row and the COPT notes', async () => {
    const report = {
      ...REPORT,
      completeness: { ...REPORT.completeness, fmea_top: 'ok' as const },
      sections: {
        fmea_top: { status: 'ok' as const, note: 'Link-primary',
          payload: { top: [{ rank: 1, mode_id: 'common_mode:poc:forced_outage',
            component_class: 'CommonMode', name: 'common_mode:poc', failure_class: 'A',
            criticality_eur_per_year: 1200, delta_eue_mwh: 8 }],
          fleet_scope: { mode: 'hub_side' as const, copt_import_model: 'two_state' as const,
            copt_common_mode: 'event_mixture' as const,
            copt_import_note: 'common-mode events are MIXED exactly' } } },
      },
    }
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'weak_flexible', report,
    } as never)
    await openPanel()
    expect((await screen.findByTestId('eh-fmea-top-row-1')).textContent)
      .toMatch(/CommonMode/)
    expect(screen.getByTestId('eh-fmea-top-copt-notes').textContent)
      .toMatch(/MIXED exactly/)
    expect(screen.getByTestId('eh-fmea-top-copt-import').textContent)
      .toMatch(/common-mode events mixed exactly/)
  })
})
