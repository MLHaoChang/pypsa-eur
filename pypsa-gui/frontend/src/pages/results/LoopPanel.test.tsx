// The adequacy-coupled planning loop panel (Phase 7, plan §3 / spec §4).
//
// Every ★ block below is a bite-checked test: the docstring names the broken
// variant it must fail against, and the variant was applied to LoopPanel.tsx
// and demonstrated RED before this file was allowed to go green.
//
// Conventions copied verbatim from McPanel.test.tsx: the api module is
// `vi.mock`ed (no msw in this suite), the component renders inside a fresh
// QueryClient with retry off, and every fixture uses the REAL backend key
// names — routers/results.py `post_coupling_loop`'s record and
// services/adequacy/coupling.py `_row` / `_mc_block`.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import {
  LoopPanel, targetEcho, wireTarget, loleCell, restoreSentence, entryHorizonYears,
} from './LoopPanel'
import type {
  CouplingIteration, CouplingLoopPayload, McStatus,
} from '../../api/simulation'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getCouplingLoop: vi.fn(),
      startCouplingLoop: vi.fn(),
      abortCouplingLoop: vi.fn(),
      getMc: vi.fn(),
      getCopt: vi.fn(),
    },
  }
})

// A representative week: 168 weighted hours, so `horizon_years` is well under
// one and the h/yr → horizon conversion is a REAL conversion rather than the
// identity the annual case degenerates to.
const WEEK_YEARS = 168 / 8760

/** GET /results/mc, shaped as the panel reads it — only `horizon_years` and
 *  `time_basis` matter here; they are what the h/yr entry converts through. */
const MC_WEEK: McStatus = {
  status: 'done',
  result: {
    engine: 'mc', fidelity: 'sequential_mc', elcc: [], warning: 'w',
    metrics: {
      lole_hours: 4.2, lole_ci: [3.9, 4.5], eue_mwh: 120, eue_ci: [110, 130],
      by_period: {}, n_samples: 500, converged: true,
      time_basis: 'hours_per_horizon', horizon_years: WEEK_YEARS,
      resolution_floor_h: 0.336,
    },
  },
  error: null, started_at: 1, finished_at: 2,
}

/** GET /results/copt — the FALLBACK horizon source when no MC has been run. */
const COPT_WEEK = {
  engine: 'copt', fidelity: 'analytic_convolution',
  metrics: {
    lole_hours: 9, eue_mwh: 400, lolp_max: 1,
    time_basis: 'hours_per_horizon', horizon_years: WEEK_YEARS,
  },
  fleet: { units: 3, must_take: 0, delta_mw: 1 },
  voll_eur_per_mwh: 4000, per_mode: [],
}

/** services/adequacy/coupling.py `_row` + `_mc_block`, verbatim keys. */
function iterate(
  eps: number, over: Partial<CouplingIteration> = {},
): CouplingIteration {
  return {
    eps_permyriad: eps,
    solve_status: 'ok',
    condition: 'optimal',
    cost_eur: 1_234_567,
    ens_mwh: 12.5,
    cap_mwh: 40.0,
    binding: 'system_cap',
    plateau: false,
    mc: {
      engine: 'mc', fidelity: 'sequential_mc',
      lole_hours: 9.3241, lole_ci: [9.1123, 9.5432],
      eue_mwh: 412.55, eue_ci: [388.12, 436.98],
      n_samples: 500, by_period: {},
    },
    ...over,
  }
}

// routers/results.py UNREACHABLE_COPY_V1 — the [N6] three-mechanism sentence,
// held in the FIXTURE and never in the component: a backend re-wording must
// reach the user without a frontend edit.
const UNREACHABLE_COPY_V1 =
  'No cap this search could reach produced a plan that met the target on '
  + "the MC's own LOLE. Three mechanisms produce this, and they call for "
  + 'different responses: (a) the LP has perfect FORESIGHT over storage while '
  + 'the MC dispatches greedily, so a plan that leans on storage looks '
  + 'adequate to the solver and is not; (b) demand response serves the LP\'s '
  + 'cap but is EXCLUDED as a resource in the MC, so tightening ε buys cost '
  + 'without buying MC-LOLE and the plan stops changing; (c) tightening ε can '
  + 'substitute storage for thermal capacity and RAISE MC-LOLE. Check the '
  + 'per-iterate binding column and by_period rows before raising the target.'

const LOOP_WARNING =
  'Sequential MC results rest on ONE weather realisation. The loop maps a '
  + 'STEP FUNCTION: only evaluated iterates are answers.'

/** A finished, met study — routers/results.py's record, thread stripped. */
const MET: CouplingLoopPayload = {
  study: 'coupling_loop',
  status: 'met',
  target_lole_h: 0.0575342465753,
  basis: 'hours_per_horizon',
  horizon_years: WEEK_YEARS,
  draws: 500,
  seed: 0,
  eps0: 100,
  max_solves: 8,
  restore: 'base',
  base_restored: true,
  confident: true,
  eps_star: 2.5,
  resolution_floor_h: 0.336,
  solves_used: 4,
  iterations: [
    iterate(100, { binding: 'voll' }),
    iterate(25),
    iterate(2.5, {
      mc: { ...iterate(2.5).mc!, lole_hours: 0.02, lole_ci: [0.01, 0.05] },
    }),
  ],
  final: iterate(2.5, {
    mc: { ...iterate(2.5).mc!, lole_hours: 0.02, lole_ci: [0.01, 0.05] },
  }),
  verdict:
    'A plan meeting 0.0575342 h was verified at ε* = 2.5‱. Your original '
    + 'config has been re-solved, so the network you are holding is NOT that '
    + 'plan: to keep it, set ens_cap_permyriad = 2.5 and re-solve.',
  warning: LOOP_WARNING,
  error: null,
  started_at: 1,
  finished_at: 2,
}

const UNREACHABLE: CouplingLoopPayload = {
  ...MET,
  status: 'unreachable',
  confident: false,
  eps_star: null,
  final: null,
  verdict: UNREACHABLE_COPY_V1,
}

const RUNNING_1: CouplingLoopPayload = {
  ...MET,
  status: 'running',
  confident: false,
  eps_star: null,
  final: null,
  verdict: null,
  iterations: [iterate(100, { binding: 'voll' })],
  finished_at: null,
}

const RUNNING_3: CouplingLoopPayload = {
  ...RUNNING_1,
  iterations: [
    iterate(100, { binding: 'voll' }),
    iterate(25, { plateau: true }),
    iterate(6.25, {
      solve_status: 'infeasible', condition: 'infeasible', cost_eur: null,
      ens_mwh: null, cap_mwh: null, binding: null, mc: null,
    }),
  ],
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
  vi.mocked(resultsApi.getCouplingLoop).mockReset().mockResolvedValue(null)
  vi.mocked(resultsApi.startCouplingLoop).mockReset()
    .mockResolvedValue({ status: 'running' })
  vi.mocked(resultsApi.abortCouplingLoop).mockReset()
    .mockResolvedValue({ status: 'running', aborting: true })
  vi.mocked(resultsApi.getMc).mockReset().mockResolvedValue(null)
  vi.mocked(resultsApi.getCopt).mockReset().mockResolvedValue(null as never)
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={client}><LoopPanel /></QueryClientProvider>)
  return { ...utils, client }
}

/** Ships collapsed, like FrontierPanel and McPanel; open it before asserting. */
async function openPanel() {
  const user = userEvent.setup()
  const utils = renderPanel()
  await user.click(await screen.findByTestId('loop-toggle'))
  return { user, ...utils }
}

// ── pure helpers ────────────────────────────────────────────────────────────

describe('targetEcho', () => {
  // ★ [S12] Bite: `targetEcho` echoes the typed number back unchanged
  // ("3 h/yr"), so a user on a 168 h week never learns that the standard they
  // typed is 52x what the study will be measured against.
  it('echoes BOTH the standard and the horizon-basis number it converts to', () => {
    expect(targetEcho(3, WEEK_YEARS)).toBe('3 h/yr = 0.058 h / 168 h horizon')
  })

  // On an ~annual horizon the two coincide, and printing "3 h/yr = 3 h / 8760 h
  // horizon" would invent a distinction the network does not have.
  it('collapses to one number on an annual basis', () => {
    const s = targetEcho(3, 1)
    expect(s).toMatch(/^3 h\/yr/)
    expect(s).not.toMatch(/8760 h horizon/)
    expect(s).toMatch(/one year/i)
  })

  // ★ Bite: return a bare "3 h/yr" when the horizon is unknown — the panel
  // then silently ASSUMES a year without ever saying so, which is the one
  // failure mode the dual echo exists to prevent.
  it('says out loud that it is assuming a year when no horizon is known', () => {
    const s = targetEcho(3, null)
    expect(s).toMatch(/assum/i)
    expect(s).toMatch(/one year/i)
  })
})

describe('entryHorizonYears', () => {
  // The ENTRY field converts through the LIVE network's horizon, and the two
  // surfaces that report one without needing a coupling-loop run are the MC
  // study and the COPT screening — the COPT needs no solve at all, which is
  // why it is the fallback rather than the other way round.
  it('prefers the MC horizon, falls back to the COPT, else null', () => {
    expect(entryHorizonYears(MC_WEEK, COPT_WEEK as never)).toBe(WEEK_YEARS)
    expect(entryHorizonYears(null, COPT_WEEK as never)).toBe(WEEK_YEARS)
    expect(entryHorizonYears(null, null)).toBeNull()
  })

  // ★ Bite: return 1 (or 0) instead of null when neither surface has run. The
  // panel would then convert silently against an invented year instead of
  // saying out loud that it is assuming one — the [S12] failure mode.
  it('returns null — never a fabricated 1 — when nothing has reported one', () => {
    expect(entryHorizonYears({ ...MC_WEEK, result: null }, null)).toBeNull()
  })
})

describe('wireTarget', () => {
  // ★ Bite: `wireTarget` returns the h/yr number unconverted — the POST then
  // asks a 168 h study to meet 3 h, a target it meets trivially, and the
  // verdict certifies a plan against a standard 52x too loose.
  it('sends the HORIZON-BASIS number, never the h/yr one', () => {
    expect(wireTarget(3, WEEK_YEARS)).toBeCloseTo(0.05753424, 7)
    expect(wireTarget(3, WEEK_YEARS)).not.toBe(3)
  })

  it('is the identity on an annual horizon and on an unknown one', () => {
    expect(wireTarget(3, 1)).toBe(3)
    expect(wireTarget(3, null)).toBe(3)
    expect(wireTarget(3, 0)).toBe(3)
  })
})

describe('loleCell', () => {
  const mc = iterate(1).mc!

  // ★ Bite: render `mean ± half-width`. The interval the engine reports may be
  // asymmetric, so a half-width belongs to neither bound; and near zero ±
  // prints a negative lower bound for a quantity that cannot be negative.
  it('renders MC-LOLE as a RANGE with the basis-carrying unit, never as ±', () => {
    const s = loleCell(mc, 'h/yr', 0.002)
    expect(s).toBe('9.11–9.54 h/yr')
    expect(s).not.toContain('±')
  })

  it('renders a dash for an iterate that was never evaluated', () => {
    expect(loleCell(null, 'h/yr', 0.002)).toBe('—')
  })

  // ★ Bite: render the bare `lole_hours`, so an all-clear evaluation reads as
  // "0 h/yr" — a precision 500 draws never had, and an invitation to compare
  // it against a statutory standard the study cannot support.
  it('states the resolution floor rather than a bare zero', () => {
    const s = loleCell({ ...mc, lole_hours: 0, lole_ci: [0, 0] }, 'h/yr', 0.002)
    expect(s).toMatch(/<\s*0\.002 h\/yr/)
    expect(s).not.toMatch(/^0[\s–]/)
  })

  it('says the resolution is unknown rather than printing "< null"', () => {
    const s = loleCell({ ...mc, lole_hours: 0, lole_ci: [0, 0] }, 'h/yr', null)
    expect(s).not.toMatch(/null/)
    expect(s).toMatch(/unknown resolution/i)
  })
})

describe('restoreSentence', () => {
  // ★ [S9] Bite: one shared sentence for both modes — the user then cannot
  // tell whether the network they are left holding IS the certified plan,
  // which is the entire decision the toggle exists to make.
  it('says the network is re-solved at the ORIGINAL target on "base"', () => {
    const s = restoreSentence('base', 2.5)
    expect(s).toMatch(/original/i)
    expect(s).toMatch(/ens_cap_permyriad = 2\.5/)
  })

  it('says ε* is LEFT APPLIED on "final"', () => {
    const s = restoreSentence('final', 2.5)
    expect(s).toMatch(/ens_cap_permyriad = 2\.5/)
    expect(s).toMatch(/appli/i)
    expect(s).not.toMatch(/original/i)
  })

  it('names ε* symbolically before a run has produced one', () => {
    expect(restoreSentence('base', null)).toMatch(/ε\*/)
  })
})

// ── the panel ───────────────────────────────────────────────────────────────

describe('LoopPanel target entry', () => {
  // ★ [S12] Bite: POST the raw h/yr field.
  it('converts the typed h/yr target through the MC horizon before POSTing', async () => {
    vi.mocked(resultsApi.getMc).mockResolvedValue(MC_WEEK)
    const { user } = await openPanel()
    const input = await screen.findByLabelText(/target/i) as HTMLInputElement
    await user.clear(input)
    await user.type(input, '3')
    await waitFor(() => expect(screen.getByTestId('loop-target-echo').textContent)
      .toBe('3 h/yr = 0.058 h / 168 h horizon'))
    await user.click(screen.getByRole('button', { name: /run loop/i }))
    await waitFor(() => expect(resultsApi.startCouplingLoop).toHaveBeenCalled())
    const body = vi.mocked(resultsApi.startCouplingLoop).mock.calls[0][0]
    expect(body.target_lole_h).toBeCloseTo(0.05753424, 7)
    expect(body.target_lole_h).not.toBe(3)
  })

  // The COPT needs no solve at all, so it is the horizon source that exists
  // BEFORE any MC has been run — without the fallback the first loop of a
  // session would silently assume a year.
  it('falls back to the COPT horizon when no MC study has run', async () => {
    vi.mocked(resultsApi.getCopt).mockResolvedValue(COPT_WEEK as never)
    const { user } = await openPanel()
    const input = await screen.findByLabelText(/target/i) as HTMLInputElement
    await user.clear(input)
    await user.type(input, '3')
    await waitFor(() => expect(screen.getByTestId('loop-target-echo').textContent)
      .toMatch(/168 h horizon/))
  })
})

describe('LoopPanel restore affordance', () => {
  // ★ [S9] Bite: the mutation hardcodes `restore: "base"` and ignores the
  // toggle — the user asks to keep the certified plan and is silently handed
  // their original one back.
  it('sends restore:"final" when the user asks to keep the certified plan', async () => {
    const { user } = await openPanel()
    const input = await screen.findByLabelText(/target/i) as HTMLInputElement
    await user.clear(input)
    await user.type(input, '3')
    await user.click(screen.getByTestId('loop-restore-toggle'))
    await user.click(screen.getByRole('button', { name: /run loop/i }))
    await waitFor(() => expect(resultsApi.startCouplingLoop).toHaveBeenCalled())
    expect(vi.mocked(resultsApi.startCouplingLoop).mock.calls[0][0].restore)
      .toBe('final')
  })

  it('defaults to "base" — the loop never applies an unasked-for cap', async () => {
    const { user } = await openPanel()
    const input = await screen.findByLabelText(/target/i) as HTMLInputElement
    await user.clear(input)
    await user.type(input, '3')
    await user.click(screen.getByRole('button', { name: /run loop/i }))
    await waitFor(() => expect(resultsApi.startCouplingLoop).toHaveBeenCalled())
    expect(vi.mocked(resultsApi.startCouplingLoop).mock.calls[0][0].restore)
      .toBe('base')
  })

  it('explains BOTH modes, and names ε* once the study has produced one', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(MET)
    await openPanel()
    const s = (await screen.findByTestId('loop-restore-explain')).textContent ?? ''
    expect(s).toMatch(/ens_cap_permyriad = 2\.5/)
  })
})

describe('LoopPanel verdict', () => {
  // ★ [N6] Bite: render a status word only, or a frontend-authored sentence —
  // the three mechanisms then never reach the user, and "unreachable" is
  // unactionable because the next step differs by which one is operating.
  it('renders the payload verdict VERBATIM, three mechanisms and all', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(UNREACHABLE)
    await openPanel()
    const v = (await screen.findByTestId('loop-verdict')).textContent ?? ''
    expect(v).toMatch(/\(a\) the LP has perfect FORESIGHT over storage/)
    expect(v).toMatch(/\(b\) demand response serves the LP's cap/)
    expect(v).toMatch(/\(c\) tightening ε can/)
  })

  it('renders whatever verdict the payload carries, not a hardcoded string', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(
      { ...MET, verdict: 'REWORDED-BY-BACKEND' })
    await openPanel()
    expect((await screen.findByTestId('loop-verdict')).textContent)
      .toMatch(/REWORDED-BY-BACKEND/)
  })

  it('shows the machine-readable status as a chip beside it', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(UNREACHABLE)
    await openPanel()
    expect((await screen.findByTestId('loop-status')).textContent)
      .toMatch(/unreachable/)
  })

  // ★ Bite: render the `confident` badge unconditionally. `confident` is the
  // 95% CI upper bound clearing the target — a draws property, never iterated
  // for — so a badge on a false value certifies a claim the study did not make.
  it('badges `confident` only when the payload says true', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(MET)
    const first = renderPanel()
    await userEvent.setup().click(await screen.findByTestId('loop-toggle'))
    expect(await screen.findByTestId('loop-confident')).toBeTruthy()
    first.unmount()
    cleanup()

    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(
      { ...MET, confident: false })
    await openPanel()
    await screen.findByTestId('loop-verdict')
    expect(screen.queryByTestId('loop-confident')).toBeNull()
  })

  // ★ Bite: render `final.mc.lole_hours` bare.
  it('renders an all-clear final as "< floor", never as 0', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue({
      ...MET,
      final: iterate(2.5, {
        mc: { ...iterate(2.5).mc!, lole_hours: 0, lole_ci: [0, 0] },
      }),
    })
    await openPanel()
    const cell = (await screen.findByTestId('loop-final-lole')).textContent ?? ''
    expect(cell).toMatch(/<\s*0\.336/)
    expect(cell).not.toMatch(/^0\b/)
  })

  // ★ Bite: render the MC-LOLE numbers with a bare "h". 0.02 h on a 168 h week
  // reads as a system comfortably inside a 3 h/yr standard when the annualised
  // truth is ~52x looser — the exact misreading `basisSuffix` exists to block,
  // and the payload's own `basis` is `hours_per_horizon` here.
  it('carries the payload basis into every LOLE it prints, never a bare h', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(MET)
    await openPanel()
    expect((await screen.findByTestId('loop-final-lole')).textContent)
      .toMatch(/h \/ 168 h horizon/)
    expect((await screen.findByTestId('loop-iterations')).textContent)
      .toMatch(/h \/ 168 h horizon/)
  })

  // The verdict is a sentence about a NUMBER, and the number the study was
  // measured against is horizon-basis — the same conversion the entry field
  // did on the way in has to be legible on the way out, or the user cannot
  // check that the standard they typed is the standard that was tested.
  it('reports the target it was actually run against, in the study basis', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(MET)
    await openPanel()
    const t = (await screen.findByTestId('loop-target')).textContent ?? ''
    expect(t).toMatch(/0\.0575/)
    expect(t).toMatch(/h \/ 168 h horizon/)
  })

  // ★ [S9] Bite: render nothing for `base_restored`. The closing re-solve is
  // wrapped in try/finally precisely because it CAN fail, and a false here
  // means the network on screen is the last iterate's plan rather than the one
  // the verdict describes — the single most misleading state the study has.
  it('says whether the closing restore actually ran, and warns when it did not', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(MET)
    const first = renderPanel()
    await userEvent.setup().click(await screen.findByTestId('loop-toggle'))
    expect((await screen.findByTestId('loop-restored')).textContent)
      .toMatch(/restored/i)
    first.unmount()
    cleanup()

    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(
      { ...MET, base_restored: false })
    await openPanel()
    const s = (await screen.findByTestId('loop-restored')).textContent ?? ''
    expect(s).toMatch(/not/i)
  })

  it('reports ε* and the solves the search actually spent', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(MET)
    await openPanel()
    expect((await screen.findByTestId('loop-eps-star')).textContent).toMatch(/2\.5/)
    expect((await screen.findByTestId('loop-solves-used')).textContent).toMatch(/4/)
  })

  it('renders the standing warning FROM THE PAYLOAD', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(
      { ...MET, warning: 'REWORDED-WARNING-V9' })
    await openPanel()
    expect((await screen.findByTestId('loop-warning')).textContent)
      .toMatch(/REWORDED-WARNING-V9/)
  })
})

describe('LoopPanel iteration table', () => {
  it('renders one row per iterate with ε, status, binding, cost and plateau', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(RUNNING_3)
    await openPanel()
    const table = await screen.findByTestId('loop-iterations')
    expect(table.querySelectorAll('tbody tr').length).toBe(3)
    const first = screen.getByTestId('loop-iter-0').textContent ?? ''
    expect(first).toMatch(/100/)          // ε permyriad
    expect(first).toMatch(/voll/)         // binding
    expect(first).toMatch(/9\.11–9\.54/)  // MC-LOLE as a range
    // a plateau iterate is MARKED — its metrics were reused, not sampled
    expect(screen.getByTestId('loop-iter-1').textContent).toMatch(/plateau/i)
  })

  // ★ Bite: render `±` in the MC-LOLE column (same variant as the pure test
  // above, checked at the rendered surface).
  it('never renders ± in the MC-LOLE column', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(RUNNING_3)
    await openPanel()
    expect((await screen.findByTestId('loop-iterations')).textContent)
      .not.toContain('±')
  })

  it('renders a dash for an infeasible iterate that was never evaluated', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(RUNNING_3)
    await openPanel()
    const row = (await screen.findByTestId('loop-iter-2')).textContent ?? ''
    expect(row).toMatch(/infeasible/)
    expect(screen.getByTestId('loop-iter-2-lole').textContent).toBe('—')
  })

  // ★ [S6] Bite: snapshot the iterations into component state on first render.
  // The record's list GROWS between polls — that is the whole point of the
  // surface, since a run is minutes long — and a frozen first snapshot leaves
  // the user watching an empty table for ten minutes.
  it('grows the table as new iterates land mid-run', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(RUNNING_1)
    const { client } = await openPanel()
    await waitFor(() => expect(
      screen.getByTestId('loop-iterations').querySelectorAll('tbody tr').length,
    ).toBe(1))

    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(RUNNING_3)
    await act(async () => { await client.invalidateQueries() })
    await waitFor(() => expect(
      screen.getByTestId('loop-iterations').querySelectorAll('tbody tr').length,
    ).toBe(3))
  })

  it('keeps polling while the study reports running', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(RUNNING_1)
    await openPanel()
    await waitFor(
      () => expect(vi.mocked(resultsApi.getCouplingLoop).mock.calls.length)
        .toBeGreaterThan(1),
      { timeout: 5000 },
    )
  }, 10_000)
})

describe('LoopPanel run controls', () => {
  // ★ Bite: the 409 handler discards the server detail and stores 'busy'. The
  // mesh has five members now (solve / sweep / frontier / MC / loop) and the
  // user cannot act on the block without knowing which one to wait for.
  it('names the blocker when a start is refused with 409', async () => {
    vi.mocked(resultsApi.startCouplingLoop).mockRejectedValue(
      httpError(409, 'a frontier study is running — wait for it to finish'))
    const { user } = await openPanel()
    const input = await screen.findByLabelText(/target/i) as HTMLInputElement
    await user.clear(input)
    await user.type(input, '3')
    await user.click(screen.getByRole('button', { name: /run loop/i }))
    const msg = await screen.findByTestId('loop-blocked')
    expect(msg.textContent).toMatch(/frontier study is running/)
    expect(msg.textContent).not.toMatch(/^busy$/i)
  })

  // ★ [S8] Bite: no abort button while running. A loop is up to eight full
  // capacity expansions; without an abort the user's only exit is to wait.
  it('offers an abort while the loop runs, and calls the abort route', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(RUNNING_1)
    const { user } = await openPanel()
    await user.click(await screen.findByTestId('loop-abort'))
    await waitFor(() => expect(resultsApi.abortCouplingLoop).toHaveBeenCalled())
  })

  it('hides the abort once the study has finished', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(MET)
    await openPanel()
    await screen.findByTestId('loop-verdict')
    expect(screen.queryByTestId('loop-abort')).toBeNull()
  })

  it('disables the run button while a loop is running', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue(RUNNING_1)
    await openPanel()
    const btn = await screen.findByTestId('loop-run')
    expect((btn as HTMLButtonElement).disabled).toBe(true)
  })

  // A target is the ONE required field, and the backend refuses a missing one
  // with a 422 — asking for it here costs a round-trip and an error banner.
  it('refuses to start without a target rather than POSTing an empty body', async () => {
    const { user } = await openPanel()
    const btn = await screen.findByTestId('loop-run')
    expect((btn as HTMLButtonElement).disabled).toBe(true)
    await user.click(btn)
    expect(resultsApi.startCouplingLoop).not.toHaveBeenCalled()
  })

  // 204 before any run (routers/results.py `get_coupling_loop`). "Not run" is a
  // different statement from a result of zero, and the panel says which — the
  // same NOT_RUN_TIP discipline the engine-comparison table already keeps.
  it('says the study has not been run rather than rendering an empty box', async () => {
    await openPanel()
    expect((await screen.findByTestId('loop-not-run')).textContent?.length ?? 0)
      .toBeGreaterThan(30)
    expect(screen.queryByTestId('loop-iterations')).toBeNull()
    expect(screen.queryByTestId('loop-verdict')).toBeNull()
  })

  it('surfaces a failed study\'s error rather than an empty panel', async () => {
    vi.mocked(resultsApi.getCouplingLoop).mockResolvedValue({
      ...MET, status: 'failed', final: null, eps_star: null,
      verdict: 'The study did not complete.',
      error: 'the controller raised',
    })
    await openPanel()
    expect((await screen.findByTestId('loop-error')).textContent)
      .toMatch(/the controller raised/)
  })
})
