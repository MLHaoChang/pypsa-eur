import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import type { EquipmentScheduleRow } from '../api/campusElectrical'
import SwitchgearRatingTable from './SwitchgearRatingTable'

const api = vi.hoisted(() => ({ equipmentSchedule: vi.fn() }))
vi.mock('../api/campusElectrical', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/campusElectrical')>()
  return { ...actual, campusApi: { ...actual.campusApi, ...api } }
})
const nav = vi.hoisted(() => ({ showResultsTab: vi.fn() }))
vi.mock('../layout/flowNavigation', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../layout/flowNavigation')>()), ...nav,
}))

const sg = (over: Partial<EquipmentScheduleRow>): EquipmentScheduleRow => ({
  kind: 'switchgear', kind_label: 'Switchgear', asset: 'IT_BUS switchgear', location: 'IT_BUS (11 kV)',
  rating: 31.5, unit: 'kA', quantity: 4, governing: 'Thermal short-circuit current 21.4 kA; fault duration 1 s, rated duration 3 s',
  source: 'computed', declared_rating: 25, declared_unit: 'kA', flag: 'declared_below_chosen', library_id: 'sg', capex_eur: 1,
  ...over,
})

function renderTable() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><SwitchgearRatingTable /></QueryClientProvider>)
}
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('Short circuit › Switchgear rating chosen', () => {
  it('one row per bus from the schedule, reasons in words, and one link to the schedule', async () => {
    useUIStore.setState({ currentProject: 'dc-a' })
    api.equipmentSchedule.mockResolvedValue({
      rows: [
        { ...sg({ kind: 'transformer', asset: 'TR1' }) },
        sg({ rated_peak_ka: 78.75 }),
        sg({ asset: 'MV_BUS switchgear', location: 'MV_BUS (not evaluated)', rating: null, declared_rating: null,
             declared_unit: null, governing: 'no fault level was computed at this bus', flag: 'not_evaluated' }),
        sg({ asset: 'HV_BUS switchgear', location: 'HV_BUS (132 kV)', rating: null, declared_rating: null, declared_unit: null,
             governing: 'Above the largest standard rating: peak short-circuit current 180 kA with the 20 % margin, largest rated peak 160 kA at 132 kV',
             flag: 'beyond_ladder' }),
      ],
      assumptions: [], missing: [], stale: false,
    })
    renderTable()
    const itBus = await screen.findByTestId('switchgear-IT_BUS')
    expect(screen.queryByTestId('switchgear-TR1')).toBeNull()
    expect(itBus.textContent).toContain('11')
    expect(itBus.textContent).toContain('31.5 kA · peak 78.75 kA')
    expect(itBus.textContent).toContain('Declared 25 kA is below the chosen 31.5 kA')
    const mv = screen.getByTestId('switchgear-MV_BUS')
    expect(mv.textContent).toContain('Not evaluated')
    expect(within(mv).getAllByRole('cell')[1].textContent).toBe('Not stated')   // a bus voltage is an input
    expect(mv.textContent).toContain('Not declared')
    expect(screen.getByTestId('switchgear-HV_BUS').textContent).toContain('Above the largest standard rating:')
    expect(screen.getByTestId('switchgear-HV_BUS').textContent).toContain('None large enough')
    expect(screen.getByTestId('switchgear-HV_BUS').querySelector('[data-testid="schedule-warn-chip"]')!.textContent)
      .toBe('Above the largest standard rating')
    expect(screen.getByTestId('switchgear-chosen').textContent).not.toMatch(/—/)
    expect(screen.getByTestId('schedule-chip-key')).toBeTruthy()
    expect(screen.getAllByRole('columnheader').map(h => h.textContent)).toContain('Flag')
    expect(screen.queryByRole('columnheader', { name: 'Check' })).toBeNull()
    expect(screen.getAllByRole('button', { name: /equipment schedule/ })).toHaveLength(1)
    fireEvent.click(screen.getByTestId('switchgear-open-schedule'))
    expect(nav.showResultsTab).toHaveBeenCalledWith('equipment_schedule',
      { from: 'Campus electrical', back: { tab: 'electrical_design' } })
  })

  it('the link opens Hand-off › Equipment schedule with a Back pill naming Campus electrical', async () => {
    const real = await vi.importActual<typeof import('../layout/flowNavigation')>('../layout/flowNavigation')
    nav.showResultsTab.mockImplementation(real.showResultsTab)
    useUIStore.setState({ currentProject: 'dc-a', uiMode: 'expert', activeSlidePanel: 'results', flowReturn: null })
    api.equipmentSchedule.mockResolvedValue({ rows: [sg({})], assumptions: [], missing: [], stale: false })
    renderTable()
    fireEvent.click(await screen.findByTestId('switchgear-open-schedule'))
    const ui = useUIStore.getState()
    expect(ui.activeSlidePanel).toBe('results')
    expect(ui.flowReturn?.label).toBe('Campus electrical')
    expect(ui.flowReturn?.panel).toBe('results')
    // Back shows the Electrical design tab again (the scroll to Short circuit is restored with it).
    real.returnToOrigin()
    expect(useUIStore.getState().resultsTabRequest).toBe('electrical_design')
  })
})
