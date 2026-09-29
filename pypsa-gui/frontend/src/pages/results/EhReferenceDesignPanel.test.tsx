import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import {
  buildEhStudyBody,
  COMPLETENESS_ORDER,
  EMPTY_PACK_FORM,
  certificationPayload,
  completenessRows,
  dtcPlanningCsvRows,
  dtcStressCsvRows,
  reportSummaryCsvRows,
  formFromTemplate,
  leverKeysFor,
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
  normalisedVerdict,
  lcohChip,
  leverCsvRows,
  multiEnergyCarrierEntries,
  multiEnergyLoadEntries,
  notEstablishedNotes,
  redundancyCsvRows,
  scrTone,
  statusTone,
  verdictTone,
} from './EhReferenceDesignPanel'
import { downloadCSV, downloadJSON } from './shared'

vi.mock('./shared', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./shared')>()
  return { ...actual, downloadCSV: vi.fn(), downloadJSON: vi.fn() }
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
      getEhReadiness: vi.fn(),
      getEhTemplate: vi.fn(),
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

const READINESS = {
  archetype: 'weak_flexible',
  import: { rule: 'eh_role', links: ['import'], applied: true },
  critical_buses: ['hub'],
  dtc: { derivable: true, reason: null },
  scr: { status: 'not_established', note: null, min_scr: null },
  storage_units: 1,
  class_b: { k: 5, closed_import_links: [], error: null },
  mc_boundary: { ok: false, error: 'tag eh_role/eh_poc to certify' },
  budget_solves: 30,
  estimated_solves: 14,
  stages: [
    { stage: 'fmea_top', prediction: 'skipped_budget', solves: 0, basis: 'exact',
      reason: 'needs 21 solves, 9 left' },
    { stage: 'ens_solve', prediction: 'run', solves: 1, basis: 'exact', reason: null },
  ],
  warnings: [],
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
  vi.mocked(resultsApi.getEhReadiness).mockReset().mockResolvedValue(READINESS as never)
  vi.mocked(resultsApi.getEhTemplate).mockReset().mockResolvedValue(null)
  vi.mocked(downloadCSV).mockReset()
  vi.mocked(downloadJSON).mockReset()
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
    // Guided-mode spec §2.9: 'ok' moved from the brand accent to the success token.
    expect(statusTone('ok')).toContain('success')
    expect(statusTone('skipped')).toContain('muted')
    expect(statusTone('not_established')).toContain('warn')
  })
})

describe('frontier / fmea CSV rows', () => {
  it('flattens frontier points and FMEA rows', () => {
    const rep = {
      ...REPORT,
      sections: {
        frontier: { status: 'ok' as const, note: null, payload: { points: [
          { target_permyriad: 10, status: 'ok',
            point: { total_system_cost_eur: 1500, achieved_ens_mwh: 2 } },
          { target_permyriad: 5, status: 'infeasible', point: null },
        ] } },
        fmea_top: { status: 'ok' as const, note: null, payload: { rows: [
          { mode_id: 'a', name: 'a', criticality_eur_per_year: 9,
            occurrence_per_year: 1, severity_eur: 9, delta_eue_mwh: 1 },
        ] } },
      },
    }
    // Merge 2026-09-28: the CSVs carry master's decision-3 columns (shed h,
    // binding, period basis, excl. shed) and the FMEA class / component, now
    // that class-A rows sit beside the Class-B ranking.
    expect(frontierCsvRows(rep as never)).toEqual([
      [10, 'ok', 1500, 2, '', '', '', 'yes'],
      [5, 'infeasible', '', '', '', '', '', 'yes'],
    ])
    expect(fmeaTopCsvRows(rep as never)).toEqual(
      [[1, 'B', 'Link', 'a', 9, 1, 1, 9, '']])
  })
})

describe('buildEhStudyBody', () => {
  it('sends only the archetype when the form is blank', () => {
    expect(buildEhStudyBody('strong_grid', EMPTY_PACK_FORM))
      .toEqual({ body: { archetype: 'strong_grid' }, error: null })
  })

  it('omits blank fields and nests the rest', () => {
    const { body, error } = buildEhStudyBody('weak_flexible', {
      ...EMPTY_PACK_FORM,
      ensCap: '5000', loleTarget: ' ', importMw: '80', budget: '12',
      draws: '300', seed: '', dsrBuses: 'flex, crit ', importEnergy: '0',
    })
    expect(error).toBeNull()
    expect(body).toEqual({
      archetype: 'weak_flexible',
      budget_solves: 12,
      pack_overrides: { ens_cap_permyriad: 5000, import_p_nom_mw: 80,
        import_energy_mwh_per_year: 0 },
      mc: { draws: 300 },
      dsr_buses: ['flex', 'crit'],
    })
  })

  it('ignores weak-only knobs for other archetypes', () => {
    const { body } = buildEhStudyBody('off_grid', {
      ...EMPTY_PACK_FORM, importMw: '80', dsrBuses: 'flex', importEnergy: '1000',
    })
    expect(body).toEqual({ archetype: 'off_grid' })
  })

  it('sends a custom stage list that always keeps the required stages', () => {
    const { body } = buildEhStudyBody('strong_grid', {
      ...EMPTY_PACK_FORM, stages: ['frontier'],
    })
    expect(body?.stages).toEqual(['apply_pack', 'ens_solve', 'frontier', 'assemble'])
  })

  it.each([
    ['ensCap', '0', /ENS target/],
    ['ensCap', 'abc', /ENS target/],
    ['loleTarget', '-1', /LOLE target/],
    ['budget', '0', /budget/],
    ['budget', '121', /budget/],
    ['budget', '2.5', /budget/],
    ['draws', '5000', /draws/],
    ['seed', '-3', /seed/],
  ])('rejects %s=%s', (field, value, msg) => {
    const { body, error } = buildEhStudyBody('strong_grid', {
      ...EMPTY_PACK_FORM, [field]: value,
    })
    expect(body).toBeNull()
    expect(error).toMatch(msg)
  })

  it('rejects a negative import energy budget on weak_flexible', () => {
    const { body, error } = buildEhStudyBody('weak_flexible', {
      ...EMPTY_PACK_FORM, importEnergy: '-5',
    })
    expect(body).toBeNull()
    expect(error).toMatch(/import energy budget/)
  })

  it('rejects a zero import cap on weak_flexible', () => {
    const { body, error } = buildEhStudyBody('weak_flexible', {
      ...EMPTY_PACK_FORM, importMw: '0',
    })
    expect(body).toBeNull()
    expect(error).toMatch(/import cap/)
  })

  it.each([
    ['draws', '0', /draws/],
  ])('rejects %s=%s', (field, value, msg) => {
    const { body, error } = buildEhStudyBody('strong_grid', {
      ...EMPTY_PACK_FORM, [field]: value,
    })
    expect(body).toBeNull()
    expect(error).toMatch(msg)
  })
})

describe('verdictTone', () => {
  it('tones pass / fail / inconclusive', () => {
    expect(verdictTone('pass')).toContain('accent')
    expect(verdictTone('fail')).toContain('danger')
    expect(verdictTone('inconclusive')).toContain('warn')
  })
})

describe('notEstablishedNotes', () => {
  it('lists not_established sections with notes, except gates / multi_energy', () => {
    const rows = notEstablishedNotes({
      ...REPORT,
      completeness: {
        target: 'not_established', levers: 'not_established', dtc: 'skipped',
        gates: 'not_established', multi_energy: 'not_established',
        cost: 'not_established',
      },
      sections: {
        target: { status: 'not_established', note: 'infeasible' },
        levers: { status: 'not_established', note: 'budget_solves exhausted' },
        dtc: { status: 'skipped', note: 'not requested' },
        gates: { status: 'not_established', note: 'x' },
        multi_energy: { status: 'not_established', note: 'y' },
        cost: { status: 'not_established', note: null },
      },
    })
    expect(rows).toEqual([
      { name: 'target', note: 'infeasible' },
      { name: 'levers', note: 'budget_solves exhausted' },
    ])
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

  it('sends pack settings and blocks Run on an invalid value', async () => {
    const user = await openPanel()
    await user.selectOptions(screen.getByTestId('eh-archetype'), 'weak_flexible')
    await user.click(screen.getByTestId('eh-pack-settings-toggle'))
    await user.type(screen.getByTestId('eh-pack-ens-cap'), '0')
    const run = screen.getByTestId('eh-run') as HTMLButtonElement
    expect(run.disabled).toBe(true)
    expect(run.getAttribute('title')).toMatch(/ENS target/)
    await user.clear(screen.getByTestId('eh-pack-ens-cap'))
    await user.type(screen.getByTestId('eh-pack-ens-cap'), '5000')
    expect(screen.getByTestId('eh-pack-lole-target').closest('label')?.textContent)
      .toMatch(/h\/yr/)
    await user.click(run)
    await waitFor(() => expect(resultsApi.startEhStudy).toHaveBeenCalledWith({
      archetype: 'weak_flexible',
      pack_overrides: { ens_cap_permyriad: 5000 },
    }))
  })

  it('shows readiness for the selected archetype before Run', async () => {
    const user = await openPanel()
    await user.selectOptions(screen.getByTestId('eh-archetype'), 'weak_flexible')
    const box = await screen.findByTestId('eh-readiness')
    expect(box.textContent).toMatch(/import/)
    expect(screen.getByTestId('eh-readiness-solves').textContent).toMatch(/14 \/ 30/)
    expect(screen.getByTestId('eh-readiness-boundary').textContent).toMatch(/eh_role/)
    expect(screen.getByTestId('eh-readiness-skipped').textContent)
      .toMatch(/fmea_top — skipped \(budget\): needs 21 solves/)
    await waitFor(() => expect(resultsApi.getEhReadiness)
      .toHaveBeenCalledWith('weak_flexible', undefined, undefined, {}))
  })

  it('labels every non-run prediction, including may-skip after an estimate', async () => {
    vi.mocked(resultsApi.getEhReadiness).mockResolvedValue({
      ...READINESS,
      stages: [
        { stage: 'apply_pack', prediction: 'run', solves: 0, basis: 'exact', reason: null },
        { stage: 'redundancy', prediction: 'run', solves: 4, basis: 'upper_bound', reason: null },
        { stage: 'levers', prediction: 'may_skip_budget', solves: 0, basis: 'exact',
          reason: 'budget_solves exhausted before this stage' },
        { stage: 'dtc_stress', prediction: 'not_reached', solves: 0, basis: 'exact',
          reason: 'apply_pack fails' },
      ],
    } as never)
    await openPanel()
    const list = await screen.findByTestId('eh-readiness-skipped')
    expect(list.textContent).toMatch(/levers — may skip \(budget\)/)
    expect(list.textContent).toMatch(/dtc_stress — not reached: apply_pack fails/)
    expect(list.textContent).not.toMatch(/redundancy/)
  })

  it('does not ask for readiness while a study runs', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue(
      { status: 'running', study: 'eh_study', archetype: 'strong_grid' } as never)
    await openPanel()
    await waitFor(() => expect(resultsApi.getEhStudy).toHaveBeenCalled())
    await new Promise(r => setTimeout(r, 50))
    expect(resultsApi.getEhReadiness).not.toHaveBeenCalled()
  })

  it('debounces the budget before asking for readiness', async () => {
    const user = await openPanel()
    await waitFor(() => expect(resultsApi.getEhReadiness)
      .toHaveBeenCalledWith('strong_grid', undefined, undefined, {}))
    await user.click(screen.getByTestId('eh-pack-settings-toggle'))
    await user.type(screen.getByTestId('eh-pack-budget'), '25')
    await waitFor(() => expect(resultsApi.getEhReadiness)
      .toHaveBeenCalledWith('strong_grid', 25, undefined, {}), { timeout: 2000 })
    expect(resultsApi.getEhReadiness).not.toHaveBeenCalledWith(
      'strong_grid', 2, undefined, {})
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

  it('shows a failed study error beside its partial report, with reasons', async () => {
    const why = "ens_solve warning:infeasible — the pack's ENS target cannot be met"
    const failedReport = {
      ...REPORT,
      achieved_ens_permyriad: null,
      cost_at_target_eur: null,
      tea: null,
      completeness: { ...REPORT.completeness, target: 'not_established' as const },
      sections: {
        target: { status: 'not_established' as const, note: why },
        gates: { status: 'not_established' as const, note: 'gates own note' },
      },
      pipeline: { aborted: false, solves_consumed: 1, budget_solves: 30 },
    }
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'failed', study: 'eh_study', archetype: 'weak_flexible',
      report: failedReport, error: why,
    } as never)
    await openPanel()
    expect((await screen.findByTestId('eh-error')).textContent).toMatch(/infeasible/)
    expect(screen.queryByTestId('eh-aborted')).toBeNull()
    expect(screen.getByTestId('eh-section-note-target').textContent).toMatch(/infeasible/)
    expect(screen.queryByTestId('eh-section-note-gates')).toBeNull()
    expect(screen.getByTestId('eh-section-target').getAttribute('title')).toBe(why)
    expect(screen.getByTestId('eh-report-solves').textContent).toMatch(/1 \/ 30/)
  })

  it('shows MC LOLE per year with its CI and the certification verdict', async () => {
    const certReport = {
      ...REPORT,
      mc_lole_h: 12.5,
      certified: false,
      completeness: { ...REPORT.completeness, certification: 'ok' as const },
      sections: {
        certification: {
          status: 'ok' as const,
          note: null,
          payload: {
            verdict: 'fail', horizon_years: 0.5, lole_h_per_year: 12.5,
            lole_ci: [5.0, 7.5], target_lole_h: 3,
          },
        },
      },
    }
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'off_grid', report: certReport,
    } as never)
    await openPanel()
    const lole = await screen.findByTestId('eh-report-lole')
    // CI is per horizon on the wire; shown per year (÷ horizon_years).
    expect(lole.textContent).toMatch(/12\.50 h\/yr/)
    expect(lole.textContent).toMatch(/10\.00.15\.00/)
    const verdict = screen.getByTestId('eh-certification-verdict')
    expect(verdict.getAttribute('data-verdict')).toBe('fail')
    expect(verdict.textContent).toMatch(/not certified/i)
    expect(verdict.textContent).toMatch(/3 h\/yr/)
    expect(screen.getByTestId('eh-section-certification').getAttribute('data-status'))
      .toBe('ok')
  })

  it('shows no LOLE block when certification did not run', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'strong_grid', report: REPORT,
    } as never)
    await openPanel()
    await screen.findByTestId('eh-report')
    expect(screen.queryByTestId('eh-report-lole')).toBeNull()
    expect(screen.queryByTestId('eh-certification-verdict')).toBeNull()
  })

  it('renders the frontier and FMEA top-N tables with CSV export', async () => {
    const rep = {
      ...REPORT,
      completeness: { ...REPORT.completeness, frontier: 'ok' as const, fmea_top: 'ok' as const },
      sections: {
        frontier: {
          status: 'ok' as const, note: null,
          payload: {
            pack_target_permyriad: 10,
            knee_index: 0,
            points: [
              { target_permyriad: 20, status: 'ok',
                point: { total_system_cost_eur: 1000, achieved_ens_mwh: 5 } },
              { target_permyriad: 10, status: 'ok',
                point: { total_system_cost_eur: 1500, achieved_ens_mwh: 2 } },
              { target_permyriad: 5, status: 'infeasible', point: null },
            ],
          },
        },
        fmea_top: {
          status: 'ok' as const, note: 'Link-primary residual risk',
          payload: {
            k_links: 3,
            unsolved: [{ id: 'feed2', status: 'infeasible' }],
            rows: [
              { mode_id: 'feed1', name: 'feed1', criticality_eur_per_year: 900,
                occurrence_per_year: 7.3, severity_eur: 123, delta_eue_mwh: 4 },
            ],
          },
        },
      },
    }
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'strong_grid', report: rep,
    } as never)
    const user = await openPanel()
    const fr = await screen.findByTestId('eh-frontier')
    expect(fr.textContent).toMatch(/infeasible/)
    expect(screen.getByTestId('eh-frontier-row-1').getAttribute('data-pack-target'))
      .toBe('true')
    expect(screen.getByTestId('eh-fmea-top').textContent).toMatch(/feed1/)
    expect(screen.getByTestId('eh-fmea-unsolved').textContent).toMatch(/feed2 \(infeasible\)/)
    // knee_index 0 of the OK points → the 20‱ row.
    expect(screen.getByTestId('eh-frontier-row-0').textContent).toMatch(/knee/)
    await user.click(screen.getByTestId('eh-frontier-csv'))
    await user.click(screen.getByTestId('eh-fmea-top-csv'))
    expect(downloadCSV).toHaveBeenCalledWith(
      'eh-frontier.csv', expect.any(Array), frontierCsvRows(rep as never))
    expect(downloadCSV).toHaveBeenCalledWith(
      'eh-fmea-top.csv', expect.any(Array), fmeaTopCsvRows(rep as never))
  })

  it('shows study-level notes (e.g. the DSR preflight)', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'weak_flexible',
      report: { ...REPORT, notes: ['DSR stays OFF (never applied globally)'] },
    } as never)
    await openPanel()
    expect((await screen.findByTestId('eh-report-notes')).textContent)
      .toMatch(/DSR stays OFF/)
  })

  it('renders no notes block when the report has none', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'strong_grid',
      report: { ...REPORT, notes: [] },
    } as never)
    await openPanel()
    await screen.findByTestId('eh-report')
    expect(screen.queryByTestId('eh-report-notes')).toBeNull()
  })

  it('hides the previous report and tables while a new study runs', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValueOnce({
      status: 'done', study: 'eh_study', archetype: 'strong_grid', report: null,
    } as never).mockResolvedValue({
      status: 'running', study: 'eh_study', archetype: 'off_grid', report: null,
    } as never)
    vi.mocked(resultsApi.getEhReferenceDesign).mockResolvedValue(REPORT as never)
    vi.mocked(resultsApi.getEhLevers).mockResolvedValue({
      kind: 'storage_duration', options: [{ kind: 'storage_duration', value: 4, status: 'ok' }],
    } as never)
    const user = await openPanel()
    expect(await screen.findByTestId('eh-report')).toBeTruthy()
    await user.click(screen.getByTestId('eh-run'))
    await screen.findByTestId('eh-abort')
    expect(screen.queryByTestId('eh-report')).toBeNull()
    expect(screen.queryByTestId('eh-levers')).toBeNull()
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

  it('shows per-Load DtC results with the disclosed VOLL premium (P16)', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'weak_flexible', report: REPORT,
    } as never)
    vi.mocked(resultsApi.getEhDtc).mockResolvedValue({
      attribution: 'per_load', voll_premium_eps: 0.05,
      contingencies: [{
        contingency: 'grid_import', status: 'optimal',
        critical_unserved_mwh: 40, noncritical_unserved_mwh: 100,
        critical_unserved_by_load: { hospital: 40, pump: 0 },
        critical_loads: ['hospital', 'pump'], noncritical_loads: ['offices'],
      }],
    } as never)
    await openPanel()
    const chip = await screen.findByTestId('eh-dtc-attribution')
    expect(chip.textContent).toBe('per_load · critical VOLL +5% priority')
    expect(screen.queryByTestId('eh-dtc-priority-caveat')).toBeNull()
    expect(screen.getByTestId('eh-dtc-by-load-grid_import').textContent)
      .toBe('hospital=40; pump=0')
    expect(screen.getByText(/Critical by Load/)).toBeTruthy()
    expect(dtcStressCsvRows({
      contingencies: [{ contingency: 'x', status: 'ok',
        critical_unserved_by_load: { hospital: 1.23456 } }],
    })[0][5]).toBe('hospital=1.235')
  })

  it('warns when a lossy path can invert the per-Load priority', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'weak_flexible', report: REPORT,
    } as never)
    vi.mocked(resultsApi.getEhDtc).mockResolvedValue({
      attribution: 'per_load', voll_premium_eps: 0.05, priority_exact: false,
      priority_caveat_links: ['feeder'], priority_caveat_line_losses: false,
      contingencies: [{ contingency: 'grid_import', status: 'optimal',
        critical_unserved_mwh: 60, noncritical_unserved_mwh: 20,
        critical_unserved_by_load: { hospital: 60 } }],
    } as never)
    await openPanel()
    expect((await screen.findByTestId('eh-dtc-priority-caveat')).textContent)
      .toMatch(/lossy Links feeder can invert the priority/)
  })

  it('shows a per_load refusal', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'weak_flexible', report: REPORT,
    } as never)
    vi.mocked(resultsApi.getEhDtc).mockResolvedValue({
      attribution: 'per_load', voll_premium_eps: 0.05,
      refused: 'per_load attribution needs Load-keyed shed data',
      contingencies: [{ contingency: 'grid_import', status: 'refused' }],
    } as never)
    await openPanel()
    expect((await screen.findByTestId('eh-dtc-refused')).textContent)
      .toMatch(/Load-keyed/)
  })

  it('keeps the bus-aggregate table without a per-Load column', async () => {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'weak_flexible', report: REPORT,
    } as never)
    vi.mocked(resultsApi.getEhDtc).mockResolvedValue(DTC as never)
    await openPanel()
    expect((await screen.findByTestId('eh-dtc-attribution')).textContent)
      .toBe('bus_aggregate_not_per_load')
    expect(screen.queryByText(/Critical by Load/)).toBeNull()
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


describe('P18 — pipeline table and whole-report export', () => {
  const STAGES = [
    { stage: 'apply_pack', status: 'run', solves_charged: 0, note: null },
    { stage: 'ens_solve', status: 'run', solves_charged: 1, note: null },
    { stage: 'frontier', status: 'skipped', solves_charged: 0,
      note: 'frontier not requested' },
    { stage: 'mc_certify', status: 'failed', solves_charged: 0,
      note: 'MC certification failed: boom' },
    { stage: 'levers', status: 'aborted', solves_charged: 2, note: 'stopped' },
    { stage: 'dtc_stress', status: 'pending', note: null },
    { stage: 'assemble', status: 'run', solves_charged: 0, note: null },
  ]
  const WITH_PIPE = {
    ...REPORT, certified: false, mc_lole_h: 4.2,
    pipeline: { aborted: false, solves_consumed: 3, budget_solves: 30, stages: STAGES },
    sections: { gates: { status: 'not_established', note: 'no SCR data' } },
    notes: ['DSR preflight: bus flex hosts a battery'],
  }

  function done() {
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'strong_grid', report: WITH_PIPE,
    } as never)
  }

  it('renders every stage with its status, solves and note', async () => {
    done()
    const user = await openPanel()
    const toggle = await screen.findByTestId('eh-pipeline-toggle')
    expect(toggle.textContent).toMatch(/7 stages/)
    expect(screen.queryByTestId('eh-pipeline-table')).toBeNull()   // collapsed
    await user.click(toggle)
    for (const st of STAGES) {
      const row = screen.getByTestId(`eh-pipeline-row-${st.stage}`)
      expect(row.querySelector('[data-status]')?.getAttribute('data-status'))
        .toBe(st.status)
      expect(row.textContent).toContain(String(st.solves_charged ?? 0))
      if (st.note) expect(row.textContent).toContain(st.note)
    }
  })

  it('downloads the stored export body as JSON', async () => {
    done()
    const exportBody = { ...WITH_PIPE, pack_hash: '0123456789abcdef' }
    vi.mocked(resultsApi.getEhReferenceDesign).mockResolvedValue(exportBody as never)
    const user = await openPanel()
    await user.click(await screen.findByTestId('eh-report-json'))
    await waitFor(() => expect(downloadJSON).toHaveBeenCalledWith(
      'eh-reference-design-strong_grid-01234567.json', exportBody))
  })

  it('refuses both exports together when the stored report was cleared', async () => {
    // A later foreground solve clears the stored report while the study
    // record keeps its copy: the panel still shows it, but neither export
    // may disagree with GET /eh_reference_design.
    done()
    vi.mocked(resultsApi.getEhReferenceDesign).mockResolvedValue(null)
    await openPanel()
    expect((await screen.findByTestId('eh-report-not-stored')).textContent)
      .toMatch(/cleared by a later solve/)
    expect((screen.getByTestId('eh-report-json') as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByTestId('eh-report-summary-csv') as HTMLButtonElement).disabled)
      .toBe(true)
  })

  it('says so if the stored report vanished between render and click', async () => {
    done()
    vi.mocked(resultsApi.getEhReferenceDesign)
      .mockResolvedValueOnce(WITH_PIPE as never).mockResolvedValue(null)
    const user = await openPanel()
    const btn = await screen.findByTestId('eh-report-json')
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false))
    await user.click(btn)
    expect((await screen.findByTestId('eh-report-json-error')).textContent)
      .toMatch(/No stored reference design/)
    expect(downloadJSON).not.toHaveBeenCalled()
  })

  it('exports a flat summary CSV of headline, completeness and notes', async () => {
    done()
    vi.mocked(resultsApi.getEhReferenceDesign).mockResolvedValue(WITH_PIPE as never)
    const user = await openPanel()
    const btn = await screen.findByTestId('eh-report-summary-csv')
    await waitFor(() => expect((btn as HTMLButtonElement).disabled).toBe(false))
    await user.click(btn)
    expect(downloadCSV).toHaveBeenCalledWith(
      'eh-reference-design-strong_grid.csv', ['group', 'key', 'value'],
      expect.any(Array))
    const rows = reportSummaryCsvRows(WITH_PIPE as never)
    expect(rows).toContainEqual(['headline', 'certified', false])
    expect(rows).toContainEqual(['headline', 'solves_consumed', 3])
    expect(rows).toContainEqual(['completeness', 'gates', 'not_established: no SCR data'])
    expect(rows).toContainEqual(['note', '1', 'DSR preflight: bus flex hosts a battery'])
    expect(rows.every(r => r.length === 3)).toBe(true)
  })
})


describe('P18 — DtC attribution choice', () => {
  it('sends dtc_attribution only when chosen', () => {
    expect(buildEhStudyBody('weak_flexible', EMPTY_PACK_FORM).body)
      .toEqual({ archetype: 'weak_flexible' })
    expect(buildEhStudyBody('weak_flexible', {
      ...EMPTY_PACK_FORM, dtcAttribution: 'per_load' }).body)
      .toEqual({ archetype: 'weak_flexible', dtc_attribution: 'per_load' })
  })

  it('asks readiness for the chosen attribution and runs with it', async () => {
    const user = await openPanel()
    await user.click(screen.getByTestId('eh-pack-settings-toggle'))
    await user.selectOptions(screen.getByTestId('eh-pack-dtc-attribution'), 'per_load')
    await waitFor(() => expect(resultsApi.getEhReadiness)
      .toHaveBeenCalledWith('strong_grid', undefined, 'per_load', {}))
    await user.click(screen.getByTestId('eh-run'))
    await waitFor(() => expect(resultsApi.startEhStudy).toHaveBeenCalledWith({
      archetype: 'strong_grid', dtc_attribution: 'per_load' }))
  })
})


describe('P19 — EH template banner', () => {
  const META = {
    id: 'eh_datacenter', name: 'Data Center Energy Hub',
    recommended_archetype: 'weak_flexible',
    pack_overrides: { import_p_nom_mw: 40 }, stages: null,
    dtc_attribution: 'per_load',
    study_notes: ['IT load is critical (it_bus); cooling and offices are not.'],
    provenance: 'synthetic illustrative data',
  }

  it('maps template metadata onto the pack form', () => {
    expect(formFromTemplate(META as never)).toEqual({
      ...EMPTY_PACK_FORM, importMw: '40', dtcAttribution: 'per_load' })
    expect(formFromTemplate({ ...META, stages: ['frontier', 'bogus', 'levers'] } as never)
      .stages).toEqual(['frontier', 'levers'])
  })

  it('offers and applies the recommended settings', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue(META as never)
    const user = await openPanel()
    const banner = await screen.findByTestId('eh-template-banner')
    expect(banner.textContent).toMatch(/Data Center Energy Hub template/)
    expect(banner.textContent).toMatch(/IT load is critical/)
    await user.click(screen.getByTestId('eh-template-apply'))
    expect((screen.getByTestId('eh-archetype') as HTMLSelectElement).value)
      .toBe('weak_flexible')
    await user.click(screen.getByTestId('eh-run'))
    await waitFor(() => expect(resultsApi.startEhStudy).toHaveBeenCalledWith({
      archetype: 'weak_flexible', pack_overrides: { import_p_nom_mw: 40 },
      dtc_attribution: 'per_load' }))
  })

  it('shows no banner for a project not made from an EH template', async () => {
    await openPanel()
    await waitFor(() => expect(resultsApi.getEhTemplate).toHaveBeenCalled())
    expect(screen.queryByTestId('eh-template-banner')).toBeNull()
  })
})


describe('E2E review — panel fixes', () => {
  it('certifies a LOLE target with the MC (strong_grid factory metric is none)', () => {
    expect(buildEhStudyBody('strong_grid', { ...EMPTY_PACK_FORM, loleTarget: '3' }).body)
      .toEqual({ archetype: 'strong_grid',
        pack_overrides: { target_lole_h: 3, certification_metric: 'mc_lole' } })
  })

  it('sends only the lever flags the user set, per archetype', () => {
    const form = { ...EMPTY_PACK_FORM,
      levers: { import_energy: 'on' as const, redundancy: 'off' as const } }
    expect(buildEhStudyBody('weak_flexible', form).body?.pack_overrides)
      .toEqual({ levers: { import_energy: true, redundancy: false } })
    // import_energy is weak_flexible only — never sent for strong_grid
    expect(buildEhStudyBody('strong_grid', form).body?.pack_overrides)
      .toEqual({ levers: { redundancy: false } })
    expect(leverKeysFor('off_grid')).not.toContain('import_cap')
  })

  it('previews the stages and overrides that will run', async () => {
    const user = await openPanel()
    await user.click(screen.getByTestId('eh-pack-settings-toggle'))
    await user.type(screen.getByTestId('eh-pack-lole-target'), '3')
    await waitFor(() => expect(resultsApi.getEhReadiness).toHaveBeenCalledWith(
      'strong_grid', undefined, undefined,
      { pack_overrides: { target_lole_h: 3, certification_metric: 'mc_lole' } }),
    { timeout: 2000 })
  })
})

it('offers the study and tagging walkthroughs (P21)', async () => {
  await openPanel()
  expect(screen.getByTestId('eh-guide-button').textContent).toMatch(/Guide/)
  expect(screen.getByTestId('eh-tagging-guide-button').textContent)
    .toMatch(/How to tag the network/)
})


it('opens the assistant with a review request prefilled, not sent (P22)', async () => {
  const { useChatStore } = await import('../../store/chatStore')
  vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
    status: 'done', study: 'eh_study', archetype: 'strong_grid', report: REPORT,
  } as never)
  useUIStore.setState({ assistantDockOpen: false })
  const user = await openPanel()
  const btn = await screen.findByTestId('eh-ask-assistant')
  expect(btn.textContent).toMatch(/review/)
  await user.click(btn)
  expect(useUIStore.getState().assistantDockOpen).toBe(true)
  expect(useChatStore.getState().composerSeed).toMatch(/Review my latest Energy Hub study/)
  useChatStore.getState().seedComposer(null)
})

describe('P19–P22 gate — panel', () => {
  it('shows catalogue hover help on the pack controls (BINDING 4)', async () => {
    const { guidesApi } = await import('../../components/GuidedTour')
    vi.spyOn(guidesApi, 'getGuide').mockResolvedValue({
      version: 1, tours: {},
      fields: { ens_cap_permyriad: 'ENS target — the share of energy demand allowed unserved.' },
    } as never)
    const user = await openPanel()
    await user.click(screen.getByTestId('eh-pack-settings-toggle'))
    const label = screen.getByTestId('eh-pack-ens-cap-label')
    const trigger = await waitFor(() => {
      const t = label.querySelector('[tabindex="0"]')
      if (!t) throw new Error('help icon not rendered yet')
      return t
    })
    await user.hover(trigger)
    expect(await screen.findByText(/share of energy demand allowed unserved/)).toBeTruthy()
  })

  it('preselects the template recommendation so Run runs the right pack', async () => {
    vi.mocked(resultsApi.getEhTemplate).mockResolvedValue({
      id: 'eh_datacenter', name: 'Data Center Energy Hub',
      recommended_archetype: 'weak_flexible',
      pack_overrides: { import_p_nom_mw: 40 }, stages: null,
      dtc_attribution: 'per_load',
    } as never)
    const user = await openPanel()
    await waitFor(() => expect(
      (screen.getByTestId('eh-archetype') as HTMLSelectElement).value).toBe('weak_flexible'))
    await user.click(screen.getByTestId('eh-run'))
    await waitFor(() => expect(resultsApi.startEhStudy).toHaveBeenCalledWith({
      archetype: 'weak_flexible', pack_overrides: { import_p_nom_mw: 40 },
      dtc_attribution: 'per_load' }))
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
    // Merge 2026-09-28: one headline — the P11 LOLE chip and verdict chip
    // (a stored `certified` verdict is read as `pass`).
    expect(screen.getByTestId('eh-report-lole').textContent).toMatch(/1\.25 h/)
    const verdict = screen.getByTestId('eh-certification-verdict')
    expect(verdict.textContent).toMatch(/3 h\/yr/)
    expect(verdict.getAttribute('data-verdict')).toBe('pass')
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
    const verdict = await screen.findByTestId('eh-certification-verdict')
    expect(verdict.getAttribute('data-verdict')).toBe('fail')
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
    expect(screen.queryByTestId('eh-report-lole')).toBeNull()
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
    expect(screen.queryByTestId('eh-report-lole')).toBeNull()
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

describe('merge of master (2026-09-28): one rendering, both payload shapes', () => {
  it('normalises a stored certified/failed verdict to the P11 vocabulary', () => {
    expect(normalisedVerdict('certified')).toBe('pass')
    expect(normalisedVerdict('failed')).toBe('fail')
    expect(normalisedVerdict('no_target')).toBeNull()
    expect(normalisedVerdict('not_established')).toBeNull()
    expect(normalisedVerdict('inconclusive')).toBe('inconclusive')
    expect(normalisedVerdict(undefined)).toBeNull()
  })

  it('labels an import the P11 boundary did not count', () => {
    expect(importModelLabel({ metric: 'mc_lole', import_model: 'excluded',
      import_firmness: 'not_counted', fleet_scope: { mode: 'hub_side' } }))
      .toMatch(/not counted in the MC.*decision 6/)
  })

  it('lists the Class-B ranking first, then the class-A screening block', async () => {
    const report = {
      ...REPORT,
      completeness: { ...REPORT.completeness, fmea_top: 'ok' as const },
      sections: {
        fmea_top: { status: 'ok' as const, note: 'Link-primary residual risk',
          payload: {
            rows: [{ mode_id: 'link:feed1:forced_outage', name: 'feed1',
              component_class: 'Link', failure_class: 'B',
              criticality_eur_per_year: 900, delta_eue_mwh: 4 }],
            class_a: { status: 'ok', rows: [{ rank: 1,
              mode_id: 'generator:base:forced_outage', component_class: 'Generator',
              name: 'base', failure_class: 'A', criticality_eur_per_year: 300 }],
              fleet_scope: { mode: 'hub_side', copt_import_model: 'two_state' },
              import_link_ranking: { import_poc: 'class_b' },
              import_link_ranking_note: 'ranked ONCE' },
          } },
      },
    }
    expect(fmeaTopModes(report).map(m => [m.rank, m.failure_class, m.name]))
      .toEqual([[1, 'B', 'feed1'], [2, 'A', 'base']])
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'weak_flexible', report,
    } as never)
    await openPanel()
    expect((await screen.findByTestId('eh-fmea-top-row-1')).getAttribute('data-class'))
      .toBe('B')
    expect(screen.getByTestId('eh-fmea-top-row-2').getAttribute('data-class')).toBe('A')
    expect(screen.getByTestId('eh-fmea-top-copt-import').textContent)
      .toMatch(/two-state unit/)
    expect(screen.getByTestId('eh-fmea-top-import-ranking').textContent)
      .toMatch(/import_poc: Class-B row/)
  })

  it('says why the class-A screening is missing, never inventing rows', async () => {
    const report = {
      ...REPORT,
      completeness: { ...REPORT.completeness, fmea_top: 'ok' as const },
      sections: {
        fmea_top: { status: 'ok' as const, note: null,
          payload: {
            rows: [{ mode_id: 'link:feed1:forced_outage', name: 'feed1',
              criticality_eur_per_year: 900 }],
            class_a: { status: 'not_established', rows: [],
              reason: 'nothing to sample: no electrical generator carries resolvable occurrence data' },
          } },
      },
    }
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'strong_grid', report,
    } as never)
    await openPanel()
    expect((await screen.findByTestId('eh-fmea-class-a-reason')).textContent)
      .toMatch(/occurrence data/)
    expect(screen.queryByTestId('eh-fmea-top-row-2')).toBeNull()
  })

  it('shows the per-year LOLE with the CI on the same basis', async () => {
    const report = {
      ...REPORT,
      mc_lole_h: 12.5,
      completeness: { ...REPORT.completeness, certification: 'ok' as const },
      sections: {
        certification: { status: 'ok' as const, note: null, payload: {
          metric: 'mc_lole', verdict: 'fail', horizon_years: 0.5,
          lole_h_per_year: 12.5, mc_lole_h: 12.5, lole_ci: [5.0, 7.5],
          target_lole_h: 3 } },
      },
    }
    vi.mocked(resultsApi.getEhStudy).mockResolvedValue({
      status: 'done', study: 'eh_study', archetype: 'off_grid', report,
    } as never)
    await openPanel()
    const line = await screen.findByTestId('eh-certification-lole')
    expect(line.textContent).toMatch(/12\.50 h\/yr \[10\.00, 15\.00\]/)
  })
})
