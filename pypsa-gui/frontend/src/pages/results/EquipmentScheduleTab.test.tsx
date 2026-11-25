import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import type { EquipmentFlag, EquipmentSchedule, EquipmentScheduleRow } from '../../api/campusElectrical'
import EquipmentScheduleTab from './EquipmentScheduleTab'
import { FLAG_WORDS, FlagCell, OTHER_FLAG } from './equipmentSchedule'

const api = vi.hoisted(() => ({ equipmentSchedule: vi.fn(), equipmentScheduleXlsx: vi.fn() }))
vi.mock('../../api/campusElectrical', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/campusElectrical')>()
  return { ...actual, campusApi: { ...actual.campusApi, ...api } }
})
const nav = vi.hoisted(() => ({ withReturnFrom: vi.fn((jump: () => void) => jump()), openSectionInGuided: vi.fn() }))
vi.mock('../../layout/flowNavigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../layout/flowNavigation')>()), ...nav,
}))

const row = (over: Partial<EquipmentScheduleRow>): EquipmentScheduleRow => ({
  kind: 'switchgear', kind_label: 'Switchgear', asset: 'IT_BUS switchgear', location: 'IT_BUS (11 kV)',
  rating: 31.5, unit: 'kA', quantity: 4, governing: 'Thermal short-circuit current 21.4 kA; fault duration 1 s, rated duration 3 s',
  source: 'computed', declared_rating: null, declared_unit: null, flag: null, library_id: 'sg-31.5', capex_eur: 480000,
  ...over,
})

const SCHEDULE: EquipmentSchedule = {
  rows: [
    row({ kind: 'transformer', kind_label: 'Transformer', asset: 'GRID_IMPORT', location: 'GRID - IT_BUS', rating: 63, unit: 'MVA',
          quantity: 2, governing: 'N-1 loading 51.8 MVA + 20% margin', library_id: 'tr-63', capex_eur: 2_400_000 }),
    row({ kind: 'filter', kind_label: 'Harmonic filter', asset: 'harmonic filters', location: null, rating: null, unit: null,
          quantity: 0, governing: 'not sized yet: filter sizing is not part of this study', source: 'not sized',
          library_id: null, capex_eur: null }),
    row({ declared_rating: 25, declared_unit: 'kA', flag: 'declared_below_chosen', rated_peak_ka: 78.75 }),
    row({ asset: 'MV_BUS switchgear', location: 'MV_BUS (not evaluated)', rating: null, quantity: 2,
          governing: 'no fault level was computed at this bus', flag: 'not_evaluated', library_id: null, capex_eur: null }),
    row({ kind: 'genset', kind_label: 'Genset', asset: 'G1', location: 'IT_BUS', rating: 2.5, unit: 'MVA', quantity: 1,
          governing: 'declared in the campus file', source: 'declared', library_id: null, capex_eur: null }),
  ],
  assumptions: [
    { applies_to: 'switchgear', name: 'mu', label: 'Breaking-current factor', value: 1, unit: '', source: 'assumed' },
    { applies_to: 'switchgear', name: 'tk_s', label: 'Fault duration', value: 1, unit: 's', source: 'assumed' },
  ],
  missing: [],
  stale: false,
}

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><EquipmentScheduleTab /></QueryClientProvider>)
}
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('Hand-off › Equipment schedule', () => {
  it('renders every row, the chip key, the warning in words and each missing value as a reason', async () => {
    useUIStore.setState({ currentProject: 'dc-a', uiMode: 'expert' })
    api.equipmentSchedule.mockResolvedValue(SCHEDULE)
    renderTab()
    const table = await screen.findByTestId('schedule-table')
    expect(within(table).getAllByRole('row')).toHaveLength(1 + SCHEDULE.rows.length)
    expect(screen.getByTestId('schedule-chip-key').textContent).toMatch(/Computed.*Declared.*Assumed.*Not sized.*Warning/)
    const warn = within(screen.getByTestId('schedule-row-2')).getByTestId('schedule-warn-chip')
    expect(warn.textContent).toBe('Declared 25 kA is below the chosen 31.5 kA')
    expect(warn.className).toContain('text-warn')
    expect(warn.className).not.toContain('danger')
    // the declared value stays in its own column
    expect(screen.getByTestId('schedule-row-2').textContent).toContain('25 kA')
    expect(screen.getByTestId('schedule-row-2').textContent).toContain('31.5 kA · peak 78.75 kA')
    expect(screen.getByTestId('schedule-row-1').textContent).toContain('Not sized yet')
    expect(screen.getByTestId('schedule-row-3').textContent).toContain('Not evaluated')
    expect(screen.getByTestId('schedule-row-3').textContent).toContain('Not declared')
    expect(table.textContent).not.toMatch(/—/)
    expect(screen.getByTestId('schedule-total').textContent).toContain('Capex total of the 2 priced rows: €')
    const assumed = screen.getByTestId('schedule-assumptions').textContent!
    expect(assumed).toContain('Breaking-current factor: 1')
    expect(assumed).toContain('Fault duration: 1 s')
    expect(assumed).not.toMatch(/\bmu\b/)
    expect(screen.queryByTestId('schedule-stale')).toBeNull()
  })

  it('says in words when the schedule is out of date', async () => {
    useUIStore.setState({ currentProject: 'dc-a' })
    api.equipmentSchedule.mockResolvedValue({ ...SCHEDULE, stale: true })
    renderTab()
    const stale = (await screen.findByTestId('schedule-stale')).textContent
    expect(stale).toContain('These ratings are out of date:')
    expect(stale).toContain('Run the DC Grid study again')
  })

  it('a flag it has no words for reads as a plain note, never its key', async () => {
    useUIStore.setState({ currentProject: 'dc-a' })
    api.equipmentSchedule.mockResolvedValue({ ...SCHEDULE, rows: [{ ...SCHEDULE.rows[0], flag: 'some_new_check' }] })
    renderTab()
    const row = await screen.findByTestId('schedule-row-0')
    expect(row.textContent).toContain(OTHER_FLAG)
    expect(row.textContent).not.toMatch(/some.new.check/i)
  })

  it('the one download button fetches the workbook', async () => {
    useUIStore.setState({ currentProject: 'dc-a' })
    api.equipmentSchedule.mockResolvedValue(SCHEDULE)
    api.equipmentScheduleXlsx.mockResolvedValue(new Blob(['x']))
    const create = vi.fn(() => 'blob:x'); const revoke = vi.fn()
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke })
    renderTab()
    expect(await screen.findAllByRole('button', { name: /Download as Excel/ })).toHaveLength(1)
    fireEvent.click(screen.getByTestId('schedule-download'))
    await waitFor(() => expect(api.equipmentScheduleXlsx).toHaveBeenCalledWith('dc-a'))
    await waitFor(() => expect(create).toHaveBeenCalled())
  })

  it.each(['expert', 'guided'] as const)('empty: one sentence and one link to the Grid study, Back naming Hand-off (%s)', async (mode) => {
    useUIStore.setState({ currentProject: 'dc-a', uiMode: mode, activeSlidePanel: 'results' })
    api.equipmentSchedule.mockResolvedValue({ rows: [], assumptions: [], missing: ['no DC Grid study has run: run it in Campus electrical'], stale: false })
    renderTab()
    const empty = await screen.findByTestId('schedule-empty')
    expect(empty.textContent).toMatch(/^The equipment schedule lists the assets of a finished DC Grid study, and this project has none yet\. Open the Grid study ›$/)
    expect(within(empty).getAllByRole('button')).toHaveLength(1)
    fireEvent.click(screen.getByTestId('schedule-empty-open'))
    expect(nav.withReturnFrom).toHaveBeenCalledWith(expect.any(Function), 'Hand-off')
    if (mode === 'guided') {
      expect(nav.openSectionInGuided).toHaveBeenCalledWith('scenarios')
      expect(useUIStore.getState().uiMode).toBe('guided')
    } else {
      expect(nav.openSectionInGuided).not.toHaveBeenCalled()
      expect(useUIStore.getState().activeSlidePanel).toBe('campusStudy')
    }
  })
})

describe('the flag word map', () => {
  const FLAGS: EquipmentFlag[] = ['declared_below_chosen', 'beyond_ladder', 'not_evaluated', 'not_rated',
    'no_library_cable', 'withstand_not_evaluated', 'partly_evaluated']
  it('covers every flag the backend sends, each in words', () => {
    expect(Object.keys(FLAG_WORDS).sort()).toEqual([...FLAGS].sort())
    for (const f of FLAGS) {
      const words = FLAG_WORDS[f].words(row({ flag: f, declared_rating: 25, declared_unit: 'kA' }))
      expect(words).toMatch(/^[A-Z][a-z]/)
      expect(words).not.toContain('_')
    }
    expect(FLAG_WORDS.declared_below_chosen.words(row({ declared_rating: 25, declared_unit: 'kA' })))
      .toBe('Declared 25 kA is below the chosen 31.5 kA')
  })

  it('several flags joined by "; " show one chip each, most severe first', () => {
    render(<FlagCell row={row({ declared_rating: 25, declared_unit: 'kA', flag: 'declared_below_chosen; partly_evaluated' })} />)
    expect(screen.getByTestId('schedule-warn-chip').textContent).toBe('Declared 25 kA is below the chosen 31.5 kA')
    expect(screen.getByTestId('schedule-note-chip').textContent).toBe('Rated on the periods with a fault level only')
    cleanup()
    render(<FlagCell row={row({ declared_rating: 25, declared_unit: 'kA', flag: 'declared_below_chosen; withstand_not_evaluated' })} />)
    expect(screen.getByTestId('schedule-note-chip').textContent).toBe('Short-circuit withstand not evaluated')
  })
})
