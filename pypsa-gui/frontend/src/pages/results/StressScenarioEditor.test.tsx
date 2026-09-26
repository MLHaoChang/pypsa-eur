// P15 — Class-C authoring. Pins: the round-trip PUT carries the whole list,
// client bounds mirror stress.py, the backend's 422 is shown verbatim, the
// pack picker lists shipped packs (unreadable ones disabled), and fields the
// editor does not own (inline series) survive an edit.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import StressScenarioEditor, {
  EMPTY_DRAFT,
  scenarioFrom,
  validateDraft,
} from './StressScenarioEditor'
import { resultsApi, type StressScenario } from '../../api/simulation'

vi.mock('../../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/simulation')>()
  return {
    ...actual,
    resultsApi: {
      ...actual.resultsApi,
      getStressScenarios: vi.fn(),
      putStressScenarios: vi.fn(),
      getStressProfilePacks: vi.fn(),
    },
  }
})

const COLD: StressScenario = {
  id: 'cold_snap', name: '1-in-20', kind: 'parametric', frequency_per_year: 0.05,
  electrical_load_multiplier: 1.3, renewable_availability_multiplier: 0.5,
}
const INLINE: StressScenario = {
  id: 'inline_df', kind: 'profiles', frequency_per_year: 0.1,
  loads_p_set: { l: [130, 130] }, provenance: 'hand-made',
}
const PACKS = [
  { id: 'synth_dunkelflaute', name: 'Synthetic dunkelflaute', snapshots: 2,
    loads: ['l'], generators: ['wind1'], provenance: 'synthetic_fixture_v1' },
  { id: 'broken', error: "profile_pack 'broken' unreadable: ..." },
]

function renderEditor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <StressScenarioEditor project="Demo" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(resultsApi.getStressScenarios).mockReset()
    .mockResolvedValue({ scenarios: [COLD, INLINE] })
  vi.mocked(resultsApi.putStressScenarios).mockReset()
    .mockImplementation(async (_p, scenarios) => ({ scenarios }))
  vi.mocked(resultsApi.getStressProfilePacks).mockReset()
    .mockResolvedValue({ packs: PACKS } as never)
})

afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('validateDraft mirrors stress.py', () => {
  const ok = { ...EMPTY_DRAFT, id: 'a', frequency: '0.1' }
  it.each([
    [{ id: 'Bad Id' }, /Id must be/],
    [{ id: 'taken' }, /already used/],
    [{ frequency: '0' }, /Frequency/],
    [{ frequency: '366' }, /Frequency/],
    [{ frequency: '' }, /Frequency/],
    [{ loadMult: '0' }, /Load multiplier/],
    [{ loadMult: '10.5' }, /Load multiplier/],
    [{ availMult: '-0.1' }, /availability/],
    [{ availMult: '1.6' }, /availability/],
    [{ kind: 'profiles' as const }, /profile pack/],
  ])('%o is refused', (patch, re) => {
    expect(validateDraft({ ...ok, ...patch }, ['taken'])).toMatch(re)
  })

  it('accepts the edges, including a zero availability multiplier', () => {
    expect(validateDraft({ ...ok, frequency: '365', loadMult: '10',
      availMult: '0' }, [])).toBeNull()
    expect(validateDraft({ ...ok, kind: 'profiles', pack: 'p' }, [])).toBeNull()
    expect(validateDraft({ ...ok, kind: 'profiles' }, [], INLINE)).toBeNull()
  })

  it('keeps fields it does not own and drops the other kind\'s fields', () => {
    const edited = scenarioFrom(
      { ...EMPTY_DRAFT, id: 'inline_df', kind: 'profiles', frequency: '0.2' },
      INLINE)
    expect(edited).toEqual({ ...INLINE, frequency_per_year: 0.2 })
    const toParam = scenarioFrom(
      { ...EMPTY_DRAFT, id: 'inline_df', frequency: '0.2', availMult: '0' },
      INLINE)
    expect(toParam.loads_p_set).toBeUndefined()
    expect(toParam.renewable_availability_multiplier).toBe(0)
  })
})

describe('StressScenarioEditor', () => {
  it('lists the registry', async () => {
    renderEditor()
    const list = await screen.findByTestId('stress-list')
    expect(list.textContent).toMatch(/cold_snap.*load ×1\.3, renewables ×0\.5/)
    expect(list.textContent).toMatch(/inline_df.*inline series/)
  })

  it('adds a parametric scenario and PUTs the whole list', async () => {
    const user = userEvent.setup()
    renderEditor()
    await screen.findByTestId('stress-list')
    await user.click(screen.getByTestId('stress-add'))
    await user.type(screen.getByLabelText('Scenario id'), 'drought')
    await user.type(screen.getByLabelText('Events per year'), '0.2')
    await user.clear(screen.getByLabelText('Renewable availability multiplier'))
    await user.type(screen.getByLabelText('Renewable availability multiplier'), '0')
    await user.click(screen.getByTestId('stress-save'))
    await waitFor(() => expect(resultsApi.putStressScenarios).toHaveBeenCalledWith(
      'Demo', [COLD, INLINE, {
        id: 'drought', kind: 'parametric', frequency_per_year: 0.2,
        electrical_load_multiplier: 1, renewable_availability_multiplier: 0,
      }]))
    await waitFor(() => expect(screen.queryByTestId('stress-form')).toBeNull())
  })

  it('blocks Save on a client bound and names it', async () => {
    const user = userEvent.setup()
    renderEditor()
    await screen.findByTestId('stress-list')
    await user.click(screen.getByLabelText('Edit cold_snap'))
    await user.clear(screen.getByLabelText('Load multiplier'))
    await user.type(screen.getByLabelText('Load multiplier'), '11')
    expect(screen.getByTestId('stress-client-error').textContent)
      .toMatch(/Load multiplier must be in \(0, 10\]/)
    expect((screen.getByTestId('stress-save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows the backend 422 verbatim and keeps the form open', async () => {
    vi.mocked(resultsApi.putStressScenarios).mockRejectedValue({
      response: { status: 422, data: { detail:
        "scenario 'cold_snap': profile series length mismatch (got lengths [2, 3])" } },
    })
    const user = userEvent.setup()
    renderEditor()
    await screen.findByTestId('stress-list')
    await user.click(screen.getByLabelText('Edit cold_snap'))
    await user.click(screen.getByTestId('stress-save'))
    expect((await screen.findByTestId('stress-server-error')).textContent).toBe(
      "scenario 'cold_snap': profile series length mismatch (got lengths [2, 3])")
    expect(screen.getByTestId('stress-form')).toBeTruthy()
  })

  it('picks a shipped pack; an unreadable pack is listed but disabled', async () => {
    const user = userEvent.setup()
    renderEditor()
    await screen.findByTestId('stress-list')
    await user.click(screen.getByTestId('stress-add'))
    await user.type(screen.getByLabelText('Scenario id'), 'df')
    await user.type(screen.getByLabelText('Events per year'), '0.05')
    await user.selectOptions(screen.getByLabelText('Scenario kind'), 'profiles')
    const picker = screen.getByLabelText('Profile pack') as HTMLSelectElement
    await waitFor(() => expect(picker.options.length).toBe(3))
    const broken = [...picker.options].find(o => o.value === 'broken')!
    expect(broken.disabled).toBe(true)
    expect((screen.getByTestId('stress-save') as HTMLButtonElement).disabled).toBe(true)     // no pack yet
    await user.selectOptions(picker, 'synth_dunkelflaute')
    await user.click(screen.getByTestId('stress-save'))
    await waitFor(() => expect(resultsApi.putStressScenarios).toHaveBeenCalledWith(
      'Demo', [COLD, INLINE, { id: 'df', kind: 'profiles',
        frequency_per_year: 0.05, profile_pack: 'synth_dunkelflaute' }]))
  })

  it('edits a scenario with inline series without losing them', async () => {
    const user = userEvent.setup()
    renderEditor()
    await screen.findByTestId('stress-list')
    await user.click(screen.getByLabelText('Edit inline_df'))
    await user.clear(screen.getByLabelText('Events per year'))
    await user.type(screen.getByLabelText('Events per year'), '0.3')
    await user.click(screen.getByTestId('stress-save'))
    await waitFor(() => expect(resultsApi.putStressScenarios).toHaveBeenCalledWith(
      'Demo', [COLD, { ...INLINE, frequency_per_year: 0.3 }]))
  })

  it('deletes a scenario', async () => {
    const user = userEvent.setup()
    renderEditor()
    await screen.findByTestId('stress-list')
    await user.click(screen.getByLabelText('Delete cold_snap'))
    await waitFor(() => expect(resultsApi.putStressScenarios)
      .toHaveBeenCalledWith('Demo', [INLINE]))
  })

  it('disables Add at the scenario cap', async () => {
    vi.mocked(resultsApi.getStressScenarios).mockResolvedValue({
      scenarios: Array.from({ length: 10 }, (_, i) => ({ ...COLD, id: `s${i}` })),
    })
    renderEditor()
    await screen.findByTestId('stress-list')
    expect((screen.getByTestId('stress-add') as HTMLButtonElement).disabled).toBe(true)
  })
})
