// control-strategy 02: the Controls block of a converter-coupled asset's form.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import ControlsBlock, {
  _resetDrafts, droppedFields, parseValue, presetDefaults, refusalByField, shownFields, STALE_WORDS, unitPatch,
} from './ControlsBlock'
import type { IslandControlsState, UnitControl } from '../../api/islandControls'
import { useUIStore } from '../../store/uiStore'

const api = vi.hoisted(() => ({ island: vi.fn(), patchIsland: vi.fn() }))
vi.mock('../../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../../api/campusElectrical')>('../../api/campusElectrical')
  return { ...real, campusApi: { ...real.campusApi, ...api } }
})

// The schema's objects as GET /island serves them (gridspine CONTROLS, CONTROL_FIELDS, PRESETS).
const FIELDS = {
  gfl_pq: [], gfl_qu: ['qu_points'], gfl_pf: [],
  gfl_fw: ['fw_k_mw_per_hz', 'fw_deadband_hz', 'fw_delay_s', 'fw_ramp_mw_per_s', 'fw_p_limit_mw'],
  gfm_droop: ['droop_pct', 'q_droop_pct', 'tau_f_s', 'i_max_pu', 'k_gfm_min'],
  gfm_vsm: ['droop_pct', 'q_droop_pct', 'H_v_s', 'i_max_pu', 'k_gfm_min'],
}
const PRESETS = {
  grid_following: { kinds: ['bess', 'pv', 'wind', 'fuel_cell'], converter: { control: 'gfl_pq', pf_rated: 0.95 }, ups: {} },
  grid_forming_support: { kinds: ['bess'], converter: { control: 'gfm_vsm', pf_rated: 0.95, droop_pct: 4, q_droop_pct: 4,
    H_v_s: 4, i_max_pu: 1.2, k_gfm_min: 1 }, ups: {} },
}
const BESS: UnitControl = {
  kind: 'bess', control: 'gfm_droop', preset_scope: null,
  values: { pf_rated: 0.95, droop_pct: 4, q_droop_pct: 4, tau_f_s: 0.1, i_max_pu: 1.2, k_gfm_min: 1, neg_seq: 0.2 },
  sources: { pf_rated: 'assumed', droop_pct: 'datasheet', q_droop_pct: 'assumed', tau_f_s: 'assumed', i_max_pu: 'assumed',
    k_gfm_min: 'assumed', neg_seq: 'assumed' },
}
const UPS: UnitControl = { kind: 'ups', preset_scope: null, transfers_on_islanding: false,
  values: { walk_in_s: 10, f_window_hz: 2 }, sources: { walk_in_s: 'assumed', f_window_hz: 'datasheet' } }
const STATE: IslandControlsState = {
  config: null, study: null, revision: 'r1', controls: Object.keys(FIELDS), control_fields: FIELDS,
  pf_choice: ['cos_phi', 'cosphi_p_points'], presets: PRESETS, unit_controls: { bess: BESS, ups: UPS },
}

function renderBlock(name = 'bess') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><ControlsBlock name={name} /></QueryClientProvider>)
}

beforeEach(() => {
  vi.clearAllMocks()
  _resetDrafts()
  useUIStore.setState({ currentProject: 'Hub A', uiMode: 'expert' })
  api.island.mockResolvedValue(STATE)
  api.patchIsland.mockResolvedValue(STATE)
})
afterEach(() => cleanup())

describe('the pure parts', () => {
  it('fills a new mode from the first preset that runs it on this kind, and from nothing otherwise', () => {
    expect(presetDefaults(STATE, 'bess', 'gfm_vsm').H_v_s).toBe(4)
    expect(presetDefaults(STATE, 'pv', 'gfm_vsm')).toEqual({})          // grid_forming_support sets a bess only
  })
  it('shows kept values, preset defaults and required empties on a switch', () => {
    const vsm = Object.fromEntries(shownFields(STATE, BESS, 'gfm_vsm', { values: {} }).map(s => [s.field, s.origin]))
    expect(vsm).toEqual({ pf_rated: 'stored', droop_pct: 'stored', q_droop_pct: 'stored', H_v_s: 'default',
      i_max_pu: 'stored', k_gfm_min: 'stored' })
    const fw = shownFields(STATE, BESS, 'gfl_fw', { values: {} })
    expect(fw.filter(s => s.origin === 'empty').map(s => s.field)).toEqual(FIELDS.gfl_fw)
  })
  it('names the settings a switch drops, and never neg_seq', () => {
    expect(droppedFields(STATE, BESS, 'gfl_pq')).toEqual(['droop_pct', 'q_droop_pct', 'tau_f_s', 'i_max_pu', 'k_gfm_min'])
    expect(droppedFields(STATE, BESS, 'gfm_droop')).toEqual([])
  })
  it('sends the mode when it changed and only the typed settings, as plain values', () => {
    expect(unitPatch(BESS, { values: {} })).toEqual({})
    expect(unitPatch(BESS, { control: 'gfm_vsm', values: { H_v_s: '6' } })).toEqual({ control: 'gfm_vsm', H_v_s: 6 })
    expect(unitPatch(BESS, { control: 'gfm_droop', values: {} })).toEqual({})
    expect(unitPatch(UPS, { values: {}, transfers: true })).toEqual({ transfers_on_islanding: true })
  })
  it('reads a curve as pairs and refuses a bad entry in words', () => {
    expect(parseValue('qu_points', '0.9, 0.33\n1.1 -0.33')).toEqual({ value: [[0.9, 0.33], [1.1, -0.33]] })
    expect(parseValue('qu_points', '0.9')).toEqual({ error: 'Enter at least two lines, each with two numbers.' })
    expect(parseValue('droop_pct', 'four')).toEqual({ error: 'Enter a number.' })
  })
  it('puts a 422 on the field it names, in words', () => {
    const f = ['pf_rated', 'H_v_s', 'i_max_pu']
    expect(refusalByField("island_config.units.bess (control gfm_vsm): missing ['H_v_s']", f))
      .toEqual({ field: 'H_v_s', words: 'Virtual inertia is needed for this mode.' })
    expect(refusalByField('island_config.units.bess (control gfm_vsm).i_max_pu must be positive, got -1', f))
      .toEqual({ field: 'i_max_pu', words: 'Current limit must be positive.' })
    expect(refusalByField('something else', f).field).toBeNull()
  })
})

describe('the block', () => {
  it('renders for a converter with its mode in words and nothing for a unit the sidecar does not hold', async () => {
    renderBlock()
    const mode = await screen.findByLabelText('Control mode') as HTMLSelectElement
    expect(mode.value).toBe('gfm_droop')
    expect([...mode.options].map(o => o.textContent)).toContain('Grid-forming, droop')
    cleanup()
    renderBlock('genset_1')
    await waitFor(() => expect(api.island).toHaveBeenCalled())
    expect(screen.queryByTestId('controls-block')).toBeNull()
  })

  it('tags assumed values with the shared chip and shows the key once', async () => {
    renderBlock()
    await screen.findByTestId('controls-assumed-pf_rated')
    expect(screen.queryByTestId('controls-assumed-droop_pct')).toBeNull()   // datasheet: stated
    expect(screen.getAllByTestId('controls-assumed-key')).toHaveLength(1)
  })

  it('a switch previews what it drops and fills, then saves one unit patch with the revision it read', async () => {
    renderBlock()
    const user = userEvent.setup()
    await user.selectOptions(await screen.findByLabelText('Control mode'), 'gfm_vsm')
    expect(screen.getByTestId('controls-switch-note').textContent).toBe('Saving drops Frequency filter time.')
    expect(screen.getByTestId('controls-assumed-H_v_s')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /Save controls/ }))
    await waitFor(() => expect(api.patchIsland).toHaveBeenCalledTimes(1))
    expect(api.patchIsland.mock.calls[0]).toEqual(['Hub A', { units: { bess: { control: 'gfm_vsm' } } }, 'r1'])
  })

  it('a required empty setting opens the fold in Guided and blocks Save with a reason', async () => {
    useUIStore.setState({ uiMode: 'guided' })
    renderBlock()
    const user = userEvent.setup()
    const fold = await screen.findByTestId('controls-fold') as HTMLDetailsElement
    expect(fold.open).toBe(false)
    await user.selectOptions(screen.getByLabelText('Control mode'), 'gfl_fw')
    expect((screen.getByTestId('controls-fold') as HTMLDetailsElement).open).toBe(true)
    expect(screen.getByTestId('controls-blocked').textContent).toContain('Frequency response gain')
    expect((screen.getByRole('button', { name: /Save controls/ }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows a 422 on its field by label, never the raw text', async () => {
    api.patchIsland.mockRejectedValue({ response: { status: 422, data: { detail:
      "island_config.units.bess (control gfm_vsm): missing ['H_v_s']" } } })
    renderBlock()
    const user = userEvent.setup()
    await user.selectOptions(await screen.findByLabelText('Control mode'), 'gfm_vsm')
    await user.click(screen.getByRole('button', { name: /Save controls/ }))
    expect((await screen.findByTestId('controls-error-H_v_s')).textContent).toBe('Virtual inertia is needed for this mode.')
    expect(document.body.textContent).not.toContain('island_config.units')
  })

  it('a 409 says so in a status region and Reload with local edits asks first', async () => {
    api.patchIsland.mockRejectedValue({ response: { status: 409, data: { detail: { error_kind: 'island_config_changed' } } } })
    renderBlock()
    const user = userEvent.setup()
    const droop = await screen.findByLabelText('Frequency droop (%)')
    await user.clear(droop)
    await user.type(droop, '5')
    await user.click(screen.getByRole('button', { name: /Save controls/ }))
    const stale = await screen.findByTestId('controls-stale')
    expect(stale.getAttribute('role')).toBe('status')
    expect(stale.textContent).toContain(STALE_WORDS)
    await user.click(screen.getByRole('button', { name: 'Reload' }))
    expect(screen.getByTestId('controls-reload-confirm')).toBeTruthy()
    const calls = api.island.mock.calls.length
    await user.click(screen.getByRole('button', { name: 'Reload and discard' }))
    await waitFor(() => expect(api.island.mock.calls.length).toBeGreaterThan(calls))
  })

  it('keeps unsaved edits across a close and reopen, and Discard drops them', async () => {
    renderBlock()
    const droop = await screen.findByLabelText('Frequency droop (%)')
    fireEvent.change(droop, { target: { value: '5' } })
    cleanup()
    renderBlock()
    expect(((await screen.findByLabelText('Frequency droop (%)')) as HTMLInputElement).value).toBe('5')
    expect(screen.getByTestId('controls-unsaved')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Discard' }))
    expect((screen.getByLabelText('Frequency droop (%)') as HTMLInputElement).value).toBe('4')
  })

  it('a UPS shows its ride-through settings and the transfer flag, with no mode', async () => {
    renderBlock('ups')
    await screen.findByLabelText('Walk-in time (s)')
    expect(screen.queryByLabelText('Control mode')).toBeNull()
    await userEvent.click(screen.getByLabelText('Transfers to its battery on islanding'))
    await userEvent.click(screen.getByRole('button', { name: /Save controls/ }))
    await waitFor(() => expect(api.patchIsland.mock.calls[0][1]).toEqual({ units: { ups: { transfers_on_islanding: true } } }))
  })

  it('names the project preset and links to the island panel with Back', async () => {
    api.island.mockResolvedValue({ ...STATE, unit_controls: { bess: { ...BESS, control: 'gfm_vsm',
      preset: 'grid_forming_support', preset_scope: 'project' } } })
    renderBlock()
    const link = await screen.findByTestId('controls-preset-link')
    expect(screen.getByTestId('controls-preset').textContent).toBe('From the project preset: Grid forming support ›')
    await userEvent.click(link)
    expect(useUIStore.getState().activeSlidePanel).toBe('campusElectrical')
    expect(useUIStore.getState().panelSectionRequest).toEqual({ panel: 'campusElectrical', section: 'island' })
  })
})
