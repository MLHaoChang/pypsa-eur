// Sequential-MC panel + the three-engine comparison table (spec §5).
//
// Every ★ block below is a bite-checked test: the docstring names the broken
// variant it must fail against, and the variant was applied to McPanel.tsx and
// demonstrated RED before this file was allowed to go green.
//
// Conventions copied from FmeaTab.test.tsx / LostLoadTab.test.tsx: the api
// module is `vi.mock`ed (this suite has no msw), the component is rendered
// inside a fresh QueryClient with retry off, and fixtures use the REAL backend
// key names (services/adequacy/mc.py §2.5, services/adequacy/elcc.py `_row`).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import {
  McPanel, blockerMessage, ciRange, loleStatement, elccShare,
} from './McPanel'
import type { McMetrics, McStatus, ElccRow } from '../../api/simulation'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getMc: vi.fn(), startMc: vi.fn(),
      getAdequacy: vi.fn(), getCopt: vi.fn(),
    },
  }
})

// Verbatim copy of services/adequacy/mc.py's MC_WARNING_V1. It lives in the
// FIXTURE and not in the component: the panel must render whatever the payload
// carries, so a backend re-wording ships without a frontend edit.
const MC_WARNING_V1 =
  'Sequential MC results rest on ONE weather realisation (the modelled '
  + "horizon's profiles — no inter-annual variability); unit outages are drawn "
  + 'INDEPENDENT of one another (no common-mode or cold-snap-correlated '
  + 'derating); and demand response is EXCLUDED as a resource (DSR slacks are '
  + 'excluded as slacks here, but in the LP they serve demand — so part of any '
  + 'MC-vs-LP-proxy gap is a missing resource, not foresight).'

const METRICS: McMetrics = {
  lole_hours: 9.3241,
  lole_ci: [9.1123, 9.5432],
  eue_mwh: 412.55,
  eue_ci: [388.12, 436.98],
  by_period: {},
  n_samples: 500,
  converged: true,
  time_basis: 'hours_per_year',
  horizon_years: 1.0,
  resolution_floor_h: 0.002,
  warning: MC_WARNING_V1,
}

const ELCC: ElccRow[] = [
  {
    kind: 'generator', name: 'CCGT-1', nameplate_mw: 400,
    elcc_mw: 312.5, elcc_share: 0.78125, status: 'ok', reason: null,
    baseline_lole_h: 9.3241, baseline_lole_ci: [9.1123, 9.5432],
  },
  {
    kind: 'storage_unit', name: 'Battery-A', nameplate_mw: 100,
    elcc_mw: null, elcc_share: null, status: 'unidentifiable',
    reason: 'baseline LOLE 0.0008 h is at or below the resolution floor of '
      + '500 draws — no credit is identifiable',
    baseline_lole_h: 0.0008, baseline_lole_ci: [0, 0.002],
  },
  {
    kind: 'vre', name: 'Wind-North', nameplate_mw: 250,
    elcc_mw: null, elcc_share: null, status: 'not_bracketed',
    reason: "a firm block of 250 MW — the asset's full nameplate — does not "
      + 'restore the baseline LOLE of 9.324 h',
    baseline_lole_h: 9.3241, baseline_lole_ci: [9.1123, 9.5432],
  },
]

const DONE: McStatus = {
  status: 'done',
  result: {
    engine: 'mc', fidelity: 'sequential_mc',
    metrics: METRICS, elcc: ELCC, warning: MC_WARNING_V1,
  },
  error: null, started_at: 1, finished_at: 2,
}

const ADEQUACY = {
  engine: 'lp_proxy', fidelity: 'deterministic_scenario',
  target: {
    basis: 'energy', binding: 'voll', zone_field_populated: true,
    system: { cap_mwh: 23.76, achieved_ens_mwh: 12.3, achieved_shed_hours: 4 },
    zones: [],
  },
  metrics: { ens_mwh: 12.3, shed_hours: 4 },
  energy: { involuntary_mwh: 12.3, demand_response_mwh: 0 },
}
const COPT = {
  engine: 'copt', fidelity: 'analytic_convolution',
  metrics: {
    lole_hours: 24, eue_mwh: 1080, lolp_max: 1,
    time_basis: 'hours_per_year', horizon_years: 1,
  },
  fleet: { units: 3, must_take: 0, delta_mw: 1 },
  voll_eur_per_mwh: 4000, per_mode: [],
}

/** An axios-shaped rejection, exactly as the client interceptor re-throws it. */
function httpError(status: number, detail: string) {
  const e = new Error(`Request failed with status code ${status}`) as Error & {
    response?: { status: number; data: { detail: string } }
  }
  e.response = { status, data: { detail } }
  return e
}

afterEach(() => cleanup())

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Demo' })
  vi.mocked(resultsApi.getMc).mockReset().mockResolvedValue(null)
  vi.mocked(resultsApi.startMc).mockReset().mockResolvedValue({ status: 'running' })
  vi.mocked(resultsApi.getAdequacy).mockReset().mockResolvedValue(null as never)
  vi.mocked(resultsApi.getCopt).mockReset().mockResolvedValue(null as never)
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><McPanel /></QueryClientProvider>)
}

/** The panel ships collapsed, like FrontierPanel; open it before asserting. */
async function openPanel() {
  const user = userEvent.setup()
  renderPanel()
  await user.click(await screen.findByTestId('mc-toggle'))
  return user
}

// ── pure helpers ────────────────────────────────────────────────────────────

describe('ciRange', () => {
  // ★ Bite: `ciRange` rewritten to `mean ± half-width`.
  it('renders an interval as a RANGE, never as ±', () => {
    const s = ciRange([9.1123, 9.5432], 'h')
    expect(s).toBe('9.11–9.54 h')
    expect(s).not.toContain('±')
  })

  it('is silent when the engine reported no interval', () => {
    expect(ciRange(null, 'h')).toBeNull()
  })

  // An asymmetric interval is exactly why ± is banned: it would print a
  // half-width that belongs to neither bound.
  it('keeps both bounds of an asymmetric interval', () => {
    expect(ciRange([0.1, 4.9], 'MWh')).toBe('0.1–4.9 MWh')
  })
})

describe('loleStatement', () => {
  // ★ Bite: `loleStatement` always returns `${lole_hours} ${unit}` — the
  // all-clear case then reads as a bare "0 h", claiming a precision 500 draws
  // cannot support.
  it('states the resolution floor rather than a bare zero when nothing shed', () => {
    const s = loleStatement({ ...METRICS, lole_hours: 0, lole_ci: [0, 0] })
    expect(s).toMatch(/<\s*0\.002 h/)
    expect(s).not.toMatch(/^0 h/)
  })

  it('says the resolution is unknown rather than printing "< null h"', () => {
    const s = loleStatement({
      ...METRICS, lole_hours: 0, lole_ci: [0, 0], resolution_floor_h: null,
    })
    expect(s).not.toMatch(/null/)
    expect(s).toMatch(/unknown resolution/i)
  })

  it('renders a non-zero LOLE with the basis-carrying unit', () => {
    expect(loleStatement(METRICS)).toBe('9.32 h/yr')
  })
})

describe('blockerMessage', () => {
  // ★ Bite: the 409 handler discards the server detail and stores 'busy'.
  it('surfaces the server detail that NAMES the blocking study', () => {
    expect(blockerMessage(httpError(409, 'a frontier study is running — wait for it to finish')))
      .toMatch(/frontier study/)
  })

  it('falls back to the transport message when there is no detail', () => {
    expect(blockerMessage(new Error('Network Error'))).toBe('Network Error')
  })
})

describe('elccShare', () => {
  it('renders a share as a percentage, and refuses a missing one', () => {
    expect(elccShare(0.78125)).toBe('78.1%')
    expect(elccShare(null)).toBeNull()
  })
})

// ── the panel ───────────────────────────────────────────────────────────────

describe('McPanel', () => {
  it('renders a finished study fetched on mount, without pressing Run', async () => {
    vi.mocked(resultsApi.getMc).mockResolvedValue(DONE)
    await openPanel()
    expect(await screen.findByTestId('mc-metrics')).toBeTruthy()
    expect(screen.getByTestId('mc-lole').textContent).toMatch(/9\.32 h\/yr/)
    expect(resultsApi.startMc).not.toHaveBeenCalled()
  })

  // ★ Bite: `ciRange` rewritten to `mean ± half-width` (same variant as the
  // pure test above, checked here at the rendered surface).
  it('renders BOTH intervals as ranges with the sample count beside them', async () => {
    vi.mocked(resultsApi.getMc).mockResolvedValue(DONE)
    await openPanel()
    const lole = await screen.findByTestId('mc-lole-ci')
    expect(lole.textContent).toMatch(/9\.11–9\.54 h/)
    expect(lole.textContent).toMatch(/n=500/)
    const eue = screen.getByTestId('mc-eue-ci')
    expect(eue.textContent).toMatch(/388\.12–436\.98 MWh/)
    expect(eue.textContent).toMatch(/n=500/)
    expect(screen.getByTestId('mc-metrics').textContent).not.toContain('±')
  })

  // ★ Bite: `loleStatement` always returns the plain value ("0 h").
  it('renders the all-clear case as a resolution-floor statement', async () => {
    vi.mocked(resultsApi.getMc).mockResolvedValue({
      ...DONE,
      result: { ...DONE.result!, metrics: { ...METRICS, lole_hours: 0, lole_ci: [0, 0] } },
    })
    await openPanel()
    expect((await screen.findByTestId('mc-lole')).textContent).toMatch(/<\s*0\.002 h/)
  })

  it('renders the standing warning FROM THE PAYLOAD, all three clauses', async () => {
    vi.mocked(resultsApi.getMc).mockResolvedValue(DONE)
    await openPanel()
    const w = (await screen.findByTestId('mc-warning')).textContent ?? ''
    expect(w).toMatch(/ONE weather realisation/)                  // clause 1
    expect(w).toMatch(/INDEPENDENT of one another/)               // clause 2
    expect(w).toMatch(/demand response is EXCLUDED as a resource/) // clause 3
  })

  // The warning must not be a component constant: a re-worded backend
  // constant has to reach the user without a frontend edit.
  it('renders whatever warning the payload carries, not a hardcoded string', async () => {
    vi.mocked(resultsApi.getMc).mockResolvedValue({
      ...DONE, result: { ...DONE.result!, warning: 'REWORDED-BY-BACKEND' },
    })
    await openPanel()
    expect((await screen.findByTestId('mc-warning')).textContent)
      .toMatch(/REWORDED-BY-BACKEND/)
  })

  // ★ Bite: the 409 handler shows a generic "busy" instead of the detail.
  it('names the blocker when a start is refused with 409', async () => {
    vi.mocked(resultsApi.startMc).mockRejectedValue(
      httpError(409, 'a frontier study is running — wait for it to finish'))
    const user = await openPanel()
    await user.click(screen.getByRole('button', { name: /run study/i }))
    const msg = await screen.findByTestId('mc-blocked')
    expect(msg.textContent).toMatch(/frontier study is running/)
    expect(msg.textContent).not.toMatch(/^busy$/i)
  })

  it('polls after a successful start and renders the finished study', async () => {
    vi.mocked(resultsApi.getMc).mockResolvedValueOnce(null).mockResolvedValue(DONE)
    const user = await openPanel()
    await user.click(screen.getByRole('button', { name: /run study/i }))
    await waitFor(() => expect(resultsApi.startMc).toHaveBeenCalled())
    expect((await screen.findByTestId('mc-lole')).textContent).toMatch(/9\.32/)
  })

  it('disables the Run button while a study is running', async () => {
    vi.mocked(resultsApi.getMc).mockResolvedValue(
      { status: 'running', result: null, error: null, started_at: 1 })
    await openPanel()
    const btn = await screen.findByRole('button', { name: /sampling/i })
    expect((btn as HTMLButtonElement).disabled).toBe(true)
  })

  it('surfaces a failed study\'s error rather than an empty panel', async () => {
    vi.mocked(resultsApi.getMc).mockResolvedValue({
      status: 'failed', result: null,
      error: 'nothing to sample: no electrical generator carries resolvable occurrence data',
      started_at: 1, finished_at: 2,
    })
    await openPanel()
    expect((await screen.findByTestId('mc-error')).textContent)
      .toMatch(/nothing to sample/)
  })

  it('sends the typed draw count to the backend', async () => {
    const user = await openPanel()
    const input = screen.getByLabelText(/draws/i) as HTMLInputElement
    await user.clear(input)
    await user.type(input, '250')
    await user.click(screen.getByRole('button', { name: /run study/i }))
    await waitFor(() => expect(resultsApi.startMc).toHaveBeenCalledWith({ draws: 250 }))
  })
})

// ── the ELCC table ──────────────────────────────────────────────────────────

describe('McPanel ELCC table', () => {
  // ★ Bite: non-ok rows render "—" and drop the reason.
  it('renders one row per asset, with refusals carrying their reason', async () => {
    vi.mocked(resultsApi.getMc).mockResolvedValue(DONE)
    await openPanel()
    const table = await screen.findByTestId('elcc-table')
    expect(table.querySelectorAll('tbody tr').length).toBe(3)

    // status "ok" → the number and the share
    const ok = screen.getByTestId('elcc-row-CCGT-1').textContent ?? ''
    expect(ok).toMatch(/312\.5/)
    expect(ok).toMatch(/78\.1%/)

    // refusals are DATA: the reason stands where the number would be
    const unid = screen.getByTestId('elcc-row-Battery-A').textContent ?? ''
    expect(unid).toMatch(/at or below the resolution floor/)
    expect(unid).toMatch(/unidentifiable/)

    const nb = screen.getByTestId('elcc-row-Wind-North').textContent ?? ''
    expect(nb).toMatch(/does not restore the baseline LOLE/)
    expect(nb).toMatch(/not.bracketed/)
  })

  it('never leaves a refused row blank', async () => {
    vi.mocked(resultsApi.getMc).mockResolvedValue(DONE)
    await openPanel()
    await screen.findByTestId('elcc-table')
    for (const name of ['Battery-A', 'Wind-North']) {
      const cell = screen.getByTestId(`elcc-verdict-${name}`)
      expect((cell.textContent ?? '').trim().length).toBeGreaterThan(10)
    }
  })

  it('states that credits do not add up', async () => {
    vi.mocked(resultsApi.getMc).mockResolvedValue(DONE)
    await openPanel()
    expect((await screen.findByTestId('elcc-non-additivity')).textContent)
      .toMatch(/marginal.*last-in|last-in.*marginal/i)
  })
})

// ── the cross-engine comparison table ───────────────────────────────────────

describe('EngineComparison', () => {
  it('has one row per engine even when none has run', async () => {
    await openPanel()
    const t = await screen.findByTestId('engine-comparison')
    expect(t).toBeTruthy()
    for (const e of ['lp_proxy', 'copt', 'mc']) {
      expect(screen.getByTestId(`cmp-row-${e}`)).toBeTruthy()
    }
  })

  it('renders an em-dash titled "not run" for an engine with no result', async () => {
    vi.mocked(resultsApi.getAdequacy).mockResolvedValue(ADEQUACY as never)
    await openPanel()
    const cell = await screen.findByTestId('cmp-value-mc')
    expect(cell.textContent).toContain('—')
    expect(cell.getAttribute('title')).toMatch(/not run/i)
  })

  it('renders each engine\'s own metric when that engine has produced one', async () => {
    vi.mocked(resultsApi.getAdequacy).mockResolvedValue(ADEQUACY as never)
    vi.mocked(resultsApi.getCopt).mockResolvedValue(COPT as never)
    vi.mocked(resultsApi.getMc).mockResolvedValue(DONE)
    await openPanel()
    expect((await screen.findByTestId('cmp-value-lp_proxy')).textContent).toMatch(/12\.3/)
    expect(screen.getByTestId('cmp-value-copt').textContent).toMatch(/1080/)
    const mc = screen.getByTestId('cmp-value-mc').textContent ?? ''
    expect(mc).toMatch(/412\.55/)
    expect(mc).toMatch(/388\.12–436\.98/)   // the +CI column requirement
  })

  // ★ Bite: the header tooltip is emptied (title dropped), so the table reads
  // as three unrelated numbers — apples to oranges.
  it('states the metric alignment in the header tooltip', async () => {
    await openPanel()
    const th = await screen.findByTestId('cmp-metric-header')
    const tip = th.getAttribute('title') ?? ''
    expect(tip).toMatch(/ENS[\s\S]*EUE/)
    expect(tip).toMatch(/shed-hours[\s\S]*LOLE/)
  })

  it('renders the COPT storage cell as a STRUCTURAL dash, not a "no"', async () => {
    await openPanel()
    const cell = await screen.findByTestId('cmp-storage-copt')
    expect(cell.textContent).toContain('—')
    expect(cell.textContent?.toLowerCase()).not.toContain('no')
    expect(cell.getAttribute('title')).toMatch(/convolution/i)
    expect(cell.getAttribute('title')).toMatch(/storage/i)
  })

  it('marks the MC and the LP proxy as storage-aware', async () => {
    await openPanel()
    expect((await screen.findByTestId('cmp-storage-mc')).textContent?.toLowerCase())
      .toMatch(/yes/)
    expect(screen.getByTestId('cmp-storage-lp_proxy').textContent?.toLowerCase())
      .toMatch(/yes/)
  })

  it('records that only the LP proxy sees DSR as a resource', async () => {
    await openPanel()
    expect((await screen.findByTestId('cmp-dsr-lp_proxy')).textContent?.toLowerCase())
      .toMatch(/yes/)
    expect(screen.getByTestId('cmp-dsr-mc').textContent?.toLowerCase()).toMatch(/no/)
    expect(screen.getByTestId('cmp-dsr-copt').textContent?.toLowerCase()).toMatch(/no/)
  })

  it('names each engine\'s foresight and time basis', async () => {
    vi.mocked(resultsApi.getMc).mockResolvedValue(DONE)
    await openPanel()
    expect((await screen.findByTestId('cmp-foresight-lp_proxy')).textContent)
      .toMatch(/perfect/i)
    expect(screen.getByTestId('cmp-foresight-mc').textContent).toMatch(/none|chronolog/i)
    expect(screen.getByTestId('cmp-basis-mc').textContent).toMatch(/hours_per_year|h\/yr/)
  })
})
