// W10 R2: the Grid card's two study tabs. They read what the grid studies hold and
// say plainly when the project is the wrong kind or the study has not run.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import type { CampusState } from '../../api/campusElectrical'

const api = vi.hoisted(() => ({
  campusState: vi.fn(), gsStatus: vi.fn(), gsCapacity: vi.fn(), gsConnection: vi.fn(),
}))
vi.mock('../../api/campusElectrical', async () => {
  const real = await vi.importActual<typeof import('../../api/campusElectrical')>('../../api/campusElectrical')
  return { ...real, campusApi: { ...real.campusApi, state: api.campusState } }
})
vi.mock('../../api/gridspine', async () => {
  const real = await vi.importActual<typeof import('../../api/gridspine')>('../../api/gridspine')
  return { ...real, gridspineApi: { ...real.gridspineApi, status: api.gsStatus, capacity: api.gsCapacity, connection: api.gsConnection } }
})
vi.mock('../../components/flowScope/useFlowScope', () => ({
  useFlowScope: () => ({ scope: { version: 1, groups: ['grid'], options: {}, source: 'project', preset_id: 'p' }, isSuccess: true }),
}))

import { GridCodeTab, ElectricalDesignTab } from './GridStudyTabs'

const hubState = (results: boolean): CampusState => ({
  campus_yaml: 'campus:\n', skipped: [], profiles: {}, settings: null, stale: false, hub_cost: null, hub_cost_reason: null,
  results: results ? {
    selection: [{ period: 2030, hour: 158, reasons: ['max_consumption_mw'] }],
    transformers: [{ group: 'GRID_IMPORT', units: 1, unit_rating_mva: 50, max_s_intact_mva: 43, max_s_n1_mva: 0,
      required_unit_mva: 51, recommended_unit_mva: 63, adequate: false, worst_period: 2030, worst_hour: 158,
      unconverged_hours: 0, margin: 0.2, n_minus_1: false }],
    compensation: [{ direction: 'capacitive', required_mvar: 3.4, recommended_mvar: 5, worst_period: 2030, worst_hour: 112, margin: 0.2 }],
    short_circuit: [],
    compliance: [{ check: 'pcc_voltage', status_as_is: 'pass', status_with_measures: 'not_rechecked', value: 1, limit: 0.9,
      unit: 'pu', worst_period: 2030, worst_hour: 0, clause: 'DCC', source: 'code', detail: 'x' }],
    requirement: { q_limit_mvar: 13, clause: 'c', source: 'assumed', profile: 'p', pf: 0.95, p_ref_mw: 40, p_ref_from: 'peak' },
    investment: null, cost: null, compliance_invested: null, history: null, unresolved: null, scope: null,
  } : null,
}) as unknown as CampusState

const notStudy = () => Object.assign(new Error('x'), { response: { status: 409 } })

function mount(el: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{el}</QueryClientProvider>)
}

beforeEach(() => {
  useUIStore.setState({ currentProject: 'Hub A', activeSlidePanel: null } as never)
  Object.values(api).forEach(f => f.mockReset())
  api.gsStatus.mockRejectedValue(notStudy())
})
afterEach(cleanup)

describe('Grid code & connection', () => {
  it('shows the campus PCC compliance of a hub project, labelled as the Grid study', async () => {
    api.campusState.mockResolvedValue(hubState(true))
    mount(<GridCodeTab />)
    expect(await screen.findByTestId('compliance-pcc_voltage')).toBeTruthy()
    expect(screen.getByTestId('campus-tab-from').textContent).toBe('From the Grid study (latest run).')
    // The electrical design is the other tab's.
    expect(screen.queryByTestId('trafo-GRID_IMPORT')).toBeNull()
  })

  it('says not run yet, with one link to the Grid study, instead of an empty table', async () => {
    api.campusState.mockResolvedValue(hubState(false))
    mount(<GridCodeTab />)
    const link = await screen.findByText('Run the grid checks in Grid study ›')
    expect(screen.queryByTestId('compliance-pcc_voltage')).toBeNull()
    fireEvent.click(link)
    expect(useUIStore.getState().activeSlidePanel).toBe('campusStudy')
  })

  it('points to the scorecard, its one home', async () => {
    api.campusState.mockResolvedValue(hubState(false))
    mount(<GridCodeTab />)
    fireEvent.click(await screen.findByTestId('grid-code-scorecard-link'))
    expect(useUIStore.getState().activeSlidePanel).toBe('issues')
  })

  it('reads a planning → dynamics project through its capacity and assessment tables, without their actions', async () => {
    api.gsStatus.mockResolvedValue({ status: 'completed' })
    api.gsCapacity.mockResolvedValue({ hours: [3], buses: ['B1'], rows: [
      { bus: 'B1', hour: 3, kind: 'load', capacity_mw: 120, dc_estimate_mw: 130, binding_kind: 'thermal',
        binding_element: 'L1', binding_contingency: null, binding_preexisting: false, method: 'ac' }] })
    api.gsConnection.mockResolvedValue({ rows: [] })
    mount(<GridCodeTab />)
    expect(await screen.findByTestId('capacity-row-B1')).toBeTruthy()
    expect(screen.getByTestId('grid-tab-from').textContent).toContain('planning → dynamics study (latest run)')
    expect(screen.queryByLabelText('Compute AC capacity at B1')).toBeNull()
    expect(screen.queryByText('Assess')).toBeNull()
    expect(api.campusState).not.toHaveBeenCalled()
  })

  it('says a planning → dynamics study has not run, not an empty capacity table', async () => {
    api.gsStatus.mockResolvedValue({ status: 'pending' })
    mount(<GridCodeTab />)
    expect(await screen.findByText(/Not run yet: the planning → dynamics study/)).toBeTruthy()
    expect(screen.queryByTestId('capacity-section')).toBeNull()
  })

  it('says a running planning → dynamics study is running, without an action', async () => {
    api.gsStatus.mockResolvedValue({ status: 'running' })
    mount(<GridCodeTab />)
    expect(await screen.findByText(/Running: the planning → dynamics study/)).toBeTruthy()
    expect(screen.queryByText('Open Planning → dynamics ›')).toBeNull()
  })

  it('next step is Hand-off while Control is outside the scope', async () => {
    api.campusState.mockResolvedValue(hubState(false))
    mount(<GridCodeTab />)
    expect((await screen.findByTestId('grid-next')).textContent).toContain('Next: hand off')
  })
})

describe('Electrical design', () => {
  it('shows transformers and compensation but not the PCC compliance table', async () => {
    api.campusState.mockResolvedValue(hubState(true))
    mount(<ElectricalDesignTab />)
    expect(await screen.findByTestId('trafo-GRID_IMPORT')).toBeTruthy()
    expect(screen.getByTestId('comp-capacitive')).toBeTruthy()
    expect(screen.queryByTestId('compliance-pcc_voltage')).toBeNull()
  })

  it('names the project kind that has an electrical design', async () => {
    api.gsStatus.mockResolvedValue({ status: 'completed' })
    mount(<ElectricalDesignTab />)
    expect(await screen.findByText(/comes from the campus study of a capacity-expansion/)).toBeTruthy()
    expect(api.campusState).not.toHaveBeenCalled()
  })

  it('answers a non-hub project in words', async () => {
    api.campusState.mockRejectedValue(notStudy())
    mount(<ElectricalDesignTab />)
    await waitFor(() => expect(screen.getByTestId('campus-tab-state')).toBeTruthy())
    await act(async () => {})
  })
})
