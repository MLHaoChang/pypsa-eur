// The MARGIN-driven planning loop panel (margin-loop spec §3, plan v2 §4).
//
// The sibling of LoopPanel.test.tsx, and the same discipline: every ★ block
// names the broken variant it must fail against, and the variant was applied
// to the source and demonstrated RED before this file was allowed to go green.
//
// Conventions copied verbatim from LoopPanel.test.tsx / McPanel.test.tsx: the
// api module is `vi.mock`ed (no msw in this suite), the component renders
// inside a fresh QueryClient with retry off, and every fixture uses the REAL
// backend key names — routers/results.py `post_margin_loop`'s record and its
// `_translate` row, NOT the coupling loop's.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { resultsApi } from '../../api/simulation'
import { compact, restoreSentence, CAP_LEVER } from './LoopPanel'
import { MarginLoopPanel, MARGIN_LEVER, leverPct } from './MarginLoopPanel'
import type {
  MarginIteration, MarginLoopPayload, McStatus,
} from '../../api/simulation'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getMarginLoop: vi.fn(),
      startMarginLoop: vi.fn(),
      abortMarginLoop: vi.fn(),
      getMc: vi.fn(),
      getCopt: vi.fn(),
    },
  }
})

/** A representative week — the h/yr → horizon conversion is a REAL one. */
const WEEK_YEARS = 168 / 8760

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

/**
 * routers/results.py `post_margin_loop._translate` — VERBATIM keys.
 *
 * ★ Note what is NOT here: `eps_permyriad`. The route translates the
 * controller's internal `x` into `lever_value` (a MARGIN) on the way into the
 * record, so no `x` and no per-myriad cap ever reaches the wire. A panel that
 * reads `eps_permyriad` off one of these rows reads `undefined`.
 *
 * ★ And `cap_mwh` is `null` on every row (spec §2.2): the margin lever has no
 * energy cap at all, and the backend's `solve_at` returns `cap_mwh=None`
 * deliberately, because passing the report's `0.0` through fires the
 * controller's ENERGY_FLOOR test on the first miss.
 */
function iterate(
  m: number, over: Partial<MarginIteration> = {},
): MarginIteration {
  return {
    lever_value: m,
    solve_status: 'ok',
    condition: 'optimal',
    cost_eur: 1_234_567,
    ens_mwh: 12.5,
    cap_mwh: null,
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

const MARGIN_WARNING =
  'Sequential MC results rest on ONE weather realisation. The loop maps a '
  + 'STEP FUNCTION over the reserve margin: only evaluated iterates are '
  + 'answers.'

/** A finished, met study — the record `get_margin_loop` serves, thread and
 *  stop-event stripped exactly as the route strips them. */
const MET: MarginLoopPayload = {
  study: 'margin_loop',
  lever: 'reserve_margin',
  lever_label: 'planning reserve margin',
  lever_unit: '%',
  status: 'met',
  target_lole_h: 0.0575342465753,
  basis: 'hours_per_horizon',
  horizon_years: WEEK_YEARS,
  draws: 500,
  seed: 0,
  margin0: 0.21,
  margin_tight: 0.2,
  margin_ceiling: 2.2,
  max_solves: 8,
  restore: 'base',
  base_restored: true,
  confident: true,
  lever_star: 1.357,
  resolution_floor_h: 0.336,
  solves_used: 4,
  probe_solves: 1,
  iterations: [
    iterate(0.21, { binding: 'voll' }),
    iterate(0.84, { plateau: true }),
    iterate(1.357, {
      mc: { ...iterate(1.357).mc!, lole_hours: 0.02, lole_ci: [0.01, 0.05] },
    }),
  ],
  final: iterate(1.357, {
    mc: { ...iterate(1.357).mc!, lole_hours: 0.02, lole_ci: [0.01, 0.05] },
  }),
  verdict:
    'A plan meeting 0.0575342 h was verified at a reserve margin of 135.7%. '
    + 'Your original config has been re-solved, so the network you are '
    + 'holding is NOT that plan: to keep it, set reserve_margin = 1.357 and '
    + 're-solve.',
  warning: MARGIN_WARNING,
  error: null,
  started_at: 1,
  finished_at: 2,
}

const UNREACHABLE: MarginLoopPayload = {
  ...MET,
  status: 'unreachable',
  confident: false,
  lever_star: null,
  final: null,
  verdict:
    'No reserve margin this search could reach produced a plan that met '
    + "0.0575342 h on the MC's own LOLE. The search is bounded above by "
    + '220.0% — the largest margin your candidate set can reach — and beyond '
    + 'it no plan exists at all. (a) the added firm capacity is DERATED by '
    + 'the same outage data the MC samples; (b) the standard is enforced at '
    + 'the PEAK; (c) energy-limited resources take a duration haircut.',
}

const RUNNING_1: MarginLoopPayload = {
  ...MET,
  status: 'running',
  confident: false,
  lever_star: null,
  final: null,
  verdict: null,
  iterations: [iterate(0.21, { binding: 'voll' })],
  finished_at: null,
}

const RUNNING_3: MarginLoopPayload = {
  ...RUNNING_1,
  iterations: [
    iterate(0.21, { binding: 'voll' }),
    iterate(0.84, { plateau: true }),
    iterate(3.36, {
      solve_status: 'validation_failed', condition: 'infeasible',
      cost_eur: null, ens_mwh: null, cap_mwh: null, binding: null, mc: null,
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
  vi.mocked(resultsApi.getMarginLoop).mockReset().mockResolvedValue(null)
  vi.mocked(resultsApi.startMarginLoop).mockReset()
    .mockResolvedValue({ status: 'running' })
  vi.mocked(resultsApi.abortMarginLoop).mockReset()
    .mockResolvedValue({ status: 'running', aborting: true })
  vi.mocked(resultsApi.getMc).mockReset().mockResolvedValue(null)
  vi.mocked(resultsApi.getCopt).mockReset().mockResolvedValue(null as never)
})

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={client}><MarginLoopPanel /></QueryClientProvider>)
  return { ...utils, client }
}

async function openPanel() {
  const user = userEvent.setup()
  const utils = renderPanel()
  await user.click(await screen.findByTestId('margin-loop-toggle'))
  return { user, ...utils }
}

// ── ★ the no-nullable-alias hazard, at the helper level ──────────────────────

describe('compact is a NUMBER-only helper', () => {
  // ★ THE reason spec §3 forbids a nullable alias. `compact` guards with
  // `isFinite`, and `isFinite(null)` is TRUE in JS — null coerces to 0 — so
  // the guard waves a null straight through to `.toPrecision(2)`, which
  // throws. Inside `rows.map` that throw is not a dash in one cell: it is the
  // whole panel unmounted by React, mid-study, with no partial render.
  //
  // ★ Bite: type the margin row's lever value as `eps_permyriad: number |
  // null` (the "nullable alias" shortcut) and feed it here — the panel tests
  // below go red with a TypeError rather than a missing cell.
  it('does NOT survive a null: isFinite(null) is true and toPrecision throws', () => {
    expect(isFinite(null as unknown as number)).toBe(true)
    expect(() => compact(null as unknown as number)).toThrow(TypeError)
  })

  it('renders a dash for the non-finite values it IS typed to take', () => {
    expect(compact(NaN)).toBe('—')
    expect(compact(Infinity)).toBe('—')
  })
})

// ── ★ restoreSentence names the RIGHT config field ───────────────────────────

describe('restoreSentence is lever-driven', () => {
  // ★ Bite: hard-code `ens_cap_permyriad = ${v}` in BOTH branches (which is
  // what it did before this phase). The sentence renders UNCONDITIONALLY, on
  // both modes, before any run — so a margin study would tell the user to set
  // the energy-cap field regardless of what the backend verdict says, and the
  // number it named would be a margin typed into a per-myriad cap.
  it('names reserve_margin — never the cap field — for the margin lever', () => {
    for (const mode of ['base', 'final'] as const) {
      const s = restoreSentence(mode, 1.357, MARGIN_LEVER)
      expect(s).toMatch(/reserve_margin = 1\.357/)
      expect(s).not.toMatch(/ens_cap_permyriad/)
      expect(s).not.toMatch(/‱/)
    }
  })

  it('names the certified margin SYMBOLICALLY before a run has produced one', () => {
    const s = restoreSentence('base', null, MARGIN_LEVER)
    expect(s).toMatch(/m\*/)
    expect(s).not.toMatch(/ens_cap_permyriad/)
  })

  // The shared helper still has to be right for the panel it came from.
  it('still names ens_cap_permyriad for the cap lever', () => {
    expect(restoreSentence('base', 2.5, CAP_LEVER))
      .toMatch(/ens_cap_permyriad = 2\.5/)
    expect(restoreSentence('final', 2.5, CAP_LEVER))
      .not.toMatch(/reserve_margin/)
  })
})

describe('leverPct', () => {
  // ★ Bite: render the raw fraction (`1.357`) or reuse the cap's `‱`. A
  // margin of 1.357 is 135.7% — a fleet nearly two and a half times peak —
  // and "1.357‱" reads as a number four orders of magnitude smaller in a
  // unit this lever does not have.
  it('renders a margin as a PERCENTAGE, never as a per-myriad', () => {
    expect(leverPct(1.357, '%')).toBe('135.7%')
    expect(leverPct(0.21, '%')).toBe('21%')
    expect(leverPct(0.0001, '%')).toBe('0.01%')
    expect(leverPct(1.357, '%')).not.toMatch(/‱/)
  })

  it('carries the payload unit rather than a hardcoded one', () => {
    expect(leverPct(1.357, 'pu')).toBe('135.7pu')
  })
})

// ── the panel ───────────────────────────────────────────────────────────────

describe('MarginLoopPanel empty state', () => {
  it('says the study has not been run rather than rendering an empty box', async () => {
    await openPanel()
    expect((await screen.findByTestId('margin-loop-not-run')).textContent?.length ?? 0)
      .toBeGreaterThan(30)
    expect(screen.queryByTestId('margin-loop-iterations')).toBeNull()
    expect(screen.queryByTestId('margin-loop-verdict')).toBeNull()
  })

  // The restore choice is made BEFORE the run, so both sentences render with
  // no payload at all — which is exactly the state the hard-coded field name
  // used to be wrong in.
  it('explains both restore modes, naming reserve_margin, before any run', async () => {
    await openPanel()
    const s = (await screen.findByTestId('margin-loop-restore-explain')).textContent ?? ''
    expect(s).toMatch(/reserve_margin/)
    expect(s).not.toMatch(/ens_cap_permyriad/)
  })
})

describe('MarginLoopPanel iteration table', () => {
  // ★ THE no-nullable-alias test at the rendered surface. These rows are the
  // backend's own shape: `lever_value` and NO `eps_permyriad`. A panel that
  // reads the cap's field — directly or through a `number | null` alias —
  // hands `undefined`/`null` to `compact` and React unmounts the whole panel.
  it('renders backend-shaped margin rows without crashing the panel', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(RUNNING_3)
    await openPanel()
    const table = await screen.findByTestId('margin-loop-iterations')
    expect(table.querySelectorAll('tbody tr').length).toBe(3)
    const all = table.textContent ?? ''
    expect(all).not.toMatch(/NaN|undefined|null/)
    expect(all).not.toContain('—%')
  })

  it('renders each iterate\'s MARGIN as a percentage, never as a per-myriad', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(RUNNING_3)
    await openPanel()
    const first = (await screen.findByTestId('margin-loop-iter-0')).textContent ?? ''
    expect(first).toMatch(/21%/)
    expect(first).toMatch(/voll/)
    expect(first).toMatch(/9\.11–9\.54/)
    expect((await screen.findByTestId('margin-loop-iterations')).textContent)
      .not.toContain('‱')
  })

  // ★ Bite: hardcode "ε ‱" as the column header. The column holds margins.
  it('takes the lever column header from the payload', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue({
      ...RUNNING_3, lever_label: 'REWORDED-LEVER', lever_unit: 'pu',
    })
    await openPanel()
    const head = (await screen.findByTestId('margin-loop-iterations'))
      .querySelector('thead')?.textContent ?? ''
    expect(head).toMatch(/REWORDED-LEVER/)
    expect(head).toMatch(/pu/)
    expect(head).not.toContain('‱')
  })

  it('marks a plateau iterate and dashes one that was never evaluated', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(RUNNING_3)
    await openPanel()
    expect(screen.getByTestId('margin-loop-iter-1').textContent).toMatch(/plateau/i)
    const row = (await screen.findByTestId('margin-loop-iter-2')).textContent ?? ''
    expect(row).toMatch(/validation_failed/)
    expect(screen.getByTestId('margin-loop-iter-2-lole').textContent).toBe('—')
  })

  it('never renders ± in the MC-LOLE column', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(RUNNING_3)
    await openPanel()
    expect((await screen.findByTestId('margin-loop-iterations')).textContent)
      .not.toContain('±')
  })

  // ★ [S6] Bite: snapshot the iterations into component state on first render.
  it('grows the table as new iterates land mid-run', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(RUNNING_1)
    const { client } = await openPanel()
    await waitFor(() => expect(
      screen.getByTestId('margin-loop-iterations').querySelectorAll('tbody tr').length,
    ).toBe(1))
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(RUNNING_3)
    await act(async () => { await client.invalidateQueries() })
    await waitFor(() => expect(
      screen.getByTestId('margin-loop-iterations').querySelectorAll('tbody tr').length,
    ).toBe(3))
  })
})

describe('MarginLoopPanel verdict', () => {
  it('renders the payload verdict VERBATIM', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(UNREACHABLE)
    await openPanel()
    const v = (await screen.findByTestId('margin-loop-verdict')).textContent ?? ''
    expect(v).toMatch(/\(a\) the added firm capacity is DERATED/)
    expect(v).toMatch(/\(c\) energy-limited resources take a duration haircut/)
    expect((await screen.findByTestId('margin-loop-status')).textContent)
      .toMatch(/unreachable/)
  })

  // ★ Bite: render `lever_star` with the cap's `‱` suffix, or raw.
  it('badges the certified MARGIN as a percentage', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(MET)
    await openPanel()
    const badge = (await screen.findByTestId('margin-loop-lever-star')).textContent ?? ''
    expect(badge).toMatch(/135\.7%/)
    expect(badge).not.toContain('‱')
  })

  // ★ Bite: render the `confident` badge unconditionally — a badge on a false
  // value certifies a claim the study did not make.
  it('badges `confident` only when the payload says true', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(MET)
    const first = renderPanel()
    await userEvent.setup().click(await screen.findByTestId('margin-loop-toggle'))
    expect(await screen.findByTestId('margin-loop-confident')).toBeTruthy()
    first.unmount()
    cleanup()

    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue({ ...MET, confident: false })
    await openPanel()
    await screen.findByTestId('margin-loop-verdict')
    expect(screen.queryByTestId('margin-loop-confident')).toBeNull()
  })

  // ★ Bite: render `final.mc.lole_hours` bare — an all-clear reads as "0 h",
  // a precision 500 draws never had.
  it('renders an all-clear final as "< floor", never as 0', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue({
      ...MET,
      final: iterate(1.357, {
        mc: { ...iterate(1.357).mc!, lole_hours: 0, lole_ci: [0, 0] },
      }),
    })
    await openPanel()
    const cell = (await screen.findByTestId('margin-loop-final-lole')).textContent ?? ''
    expect(cell).toMatch(/<\s*0\.336/)
    expect(cell).not.toMatch(/^0\b/)
  })

  it('carries the payload basis into every LOLE it prints', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(MET)
    await openPanel()
    expect((await screen.findByTestId('margin-loop-final-lole')).textContent)
      .toMatch(/h \/ 168 h horizon/)
    expect((await screen.findByTestId('margin-loop-target')).textContent)
      .toMatch(/0\.0575/)
  })

  // ★ Bite: report `solves_used` alone. The probing solve of §2.3 is OUTSIDE
  // the controller's budget (amendment v1.1(5)) — a user timing the run must
  // be able to account for every solve it made, and folding the probe into
  // the budget would misreport both numbers.
  it('reports the probe solve separately from the search budget', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(MET)
    await openPanel()
    const s = (await screen.findByTestId('margin-loop-solves-used')).textContent ?? ''
    expect(s).toMatch(/4/)
    expect(s).toMatch(/probe/i)
    expect(s).toMatch(/1/)
  })

  // ★ Bite: drop the ceiling chip. The search is BOUNDED above by the fleet's
  // own reachable margin; an `unreachable` verdict without it reads as "no
  // margin works" rather than "no margin under 220% works".
  it('reports the fleet margin ceiling as a percentage when the payload has one', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(UNREACHABLE)
    await openPanel()
    expect((await screen.findByTestId('margin-loop-ceiling')).textContent)
      .toMatch(/220%/)
  })

  it('says the ceiling is unbounded rather than printing "null"', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(
      { ...UNREACHABLE, margin_ceiling: null })
    await openPanel()
    const s = (await screen.findByTestId('margin-loop-ceiling')).textContent ?? ''
    expect(s).not.toMatch(/null/)
    expect(s).toMatch(/unbounded/i)
  })

  // ★ [S9] Bite: render nothing for `base_restored`.
  it('says whether the closing restore actually ran', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(MET)
    const first = renderPanel()
    await userEvent.setup().click(await screen.findByTestId('margin-loop-toggle'))
    expect((await screen.findByTestId('margin-loop-restored')).textContent)
      .toMatch(/restored/i)
    first.unmount()
    cleanup()

    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(
      { ...MET, base_restored: false })
    await openPanel()
    expect((await screen.findByTestId('margin-loop-restored')).textContent ?? '')
      .toMatch(/not/i)
  })

  it('renders the standing warning FROM THE PAYLOAD', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(
      { ...MET, warning: 'REWORDED-WARNING-V9' })
    await openPanel()
    expect((await screen.findByTestId('margin-loop-warning')).textContent)
      .toMatch(/REWORDED-WARNING-V9/)
  })

  it('surfaces a failed study\'s error rather than an empty panel', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue({
      ...MET, status: 'failed', final: null, lever_star: null,
      verdict: 'The study did not complete.', error: 'the controller raised',
    })
    await openPanel()
    expect((await screen.findByTestId('margin-loop-error')).textContent)
      .toMatch(/the controller raised/)
  })

  it('names the certified margin in the restore explainer once a run produced one', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(MET)
    await openPanel()
    const s = (await screen.findByTestId('margin-loop-restore-explain')).textContent ?? ''
    expect(s).toMatch(/reserve_margin = 1\.357/)
    expect(s).not.toMatch(/ens_cap_permyriad/)
  })
})

describe('MarginLoopPanel run controls', () => {
  // ★ [S12] Bite: POST the raw h/yr field — the same unit trap the cap loop
  // has, and the same 52x on a representative week.
  it('converts the typed h/yr target through the MC horizon before POSTing', async () => {
    vi.mocked(resultsApi.getMc).mockResolvedValue(MC_WEEK)
    const { user } = await openPanel()
    const input = await screen.findByLabelText(/target/i) as HTMLInputElement
    await user.clear(input)
    await user.type(input, '3')
    await waitFor(() => expect(
      screen.getByTestId('margin-loop-target-echo').textContent,
    ).toBe('3 h/yr = 0.058 h / 168 h horizon'))
    await user.click(screen.getByRole('button', { name: /run loop/i }))
    await waitFor(() => expect(resultsApi.startMarginLoop).toHaveBeenCalled())
    const body = vi.mocked(resultsApi.startMarginLoop).mock.calls[0][0]
    expect(body.target_lole_h).toBeCloseTo(0.05753424, 7)
    expect(body.target_lole_h).not.toBe(3)
  })

  // ★ [S9] Bite: hardcode `restore: "base"` and ignore the toggle.
  it('sends restore:"final" when the user asks to keep the certified plan', async () => {
    const { user } = await openPanel()
    const input = await screen.findByLabelText(/target/i) as HTMLInputElement
    await user.clear(input)
    await user.type(input, '3')
    await user.click(screen.getByTestId('margin-loop-restore-toggle'))
    await user.click(screen.getByRole('button', { name: /run loop/i }))
    await waitFor(() => expect(resultsApi.startMarginLoop).toHaveBeenCalled())
    expect(vi.mocked(resultsApi.startMarginLoop).mock.calls[0][0].restore)
      .toBe('final')
  })

  it('defaults to "base" — the loop never applies an unasked-for margin', async () => {
    const { user } = await openPanel()
    const input = await screen.findByLabelText(/target/i) as HTMLInputElement
    await user.clear(input)
    await user.type(input, '3')
    await user.click(screen.getByRole('button', { name: /run loop/i }))
    await waitFor(() => expect(resultsApi.startMarginLoop).toHaveBeenCalled())
    expect(vi.mocked(resultsApi.startMarginLoop).mock.calls[0][0].restore)
      .toBe('base')
  })

  // ★ Bite: the 409 handler discards the server detail and stores 'busy'. The
  // mesh has SIX members now, and the user cannot act on the block without
  // knowing which one holds the surface.
  it('names the blocker when a start is refused with 409', async () => {
    vi.mocked(resultsApi.startMarginLoop).mockRejectedValue(
      httpError(409, 'a coupling-loop study is running — wait for it to finish'))
    const { user } = await openPanel()
    const input = await screen.findByLabelText(/target/i) as HTMLInputElement
    await user.clear(input)
    await user.type(input, '3')
    await user.click(screen.getByRole('button', { name: /run loop/i }))
    const msg = await screen.findByTestId('margin-loop-blocked')
    expect(msg.textContent).toMatch(/coupling-loop study is running/)
    expect(msg.textContent).not.toMatch(/^busy$/i)
  })

  // ★ Bite: no abort button while running.
  it('offers an abort while the loop runs, and calls the abort route', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(RUNNING_1)
    const { user } = await openPanel()
    await user.click(await screen.findByTestId('margin-loop-abort'))
    await waitFor(() => expect(resultsApi.abortMarginLoop).toHaveBeenCalled())
  })

  it('hides the abort once the study has finished', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(MET)
    await openPanel()
    await screen.findByTestId('margin-loop-verdict')
    expect(screen.queryByTestId('margin-loop-abort')).toBeNull()
  })

  it('disables the run button while a loop is running', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(RUNNING_1)
    await openPanel()
    expect((await screen.findByTestId('margin-loop-run') as HTMLButtonElement)
      .disabled).toBe(true)
  })

  it('refuses to start without a target rather than POSTing an empty body', async () => {
    const { user } = await openPanel()
    const btn = await screen.findByTestId('margin-loop-run')
    expect((btn as HTMLButtonElement).disabled).toBe(true)
    await user.click(btn)
    expect(resultsApi.startMarginLoop).not.toHaveBeenCalled()
  })

  it('keeps polling while the study reports running', async () => {
    vi.mocked(resultsApi.getMarginLoop).mockResolvedValue(RUNNING_1)
    await openPanel()
    await waitFor(
      () => expect(vi.mocked(resultsApi.getMarginLoop).mock.calls.length)
        .toBeGreaterThan(1),
      { timeout: 5000 },
    )
  }, 10_000)
})
