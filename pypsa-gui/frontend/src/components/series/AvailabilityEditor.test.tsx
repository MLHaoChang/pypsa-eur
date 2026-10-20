// The availability editor (TS-02) held to the backend's contract:
// - Guided maintenance posts a season rule; Advanced dated windows post
//   MM-DD windows with their available share;
// - must-run posts one share, or winter and summer shares;
// - derating posts its floor (and the per-kelvin loss as a fraction);
// - a link posts component_type "links";
// - forced outages are read-only: no Preview, a way to the reliability study.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AvailabilityEditor from './AvailabilityEditor'
import { useUIStore } from '../../store/uiStore'

const api = vi.hoisted(() => ({
  loadArchetypes: vi.fn(), generateLoad: vi.fn(), uploadLoad: vi.fn(), refine: vi.fn(),
  apply: vi.fn(), generateAvailability: vi.fn(), generateSeries: vi.fn(),
}))
vi.mock('../../api/profiles', async () => {
  const real = await vi.importActual<typeof import('../../api/profiles')>('../../api/profiles')
  return { ...real, seriesApi: api }
})

const PENDING = {
  job_id: 'job-a', family: 'availability', points: ['gt1'], provenance: 'synthetic/assumed',
  stats: { gt1: { min: 0, mean: 0.96, max: 1 } }, warnings: [],
}

function renderEditor(mode: 'guided' | 'expert' = 'guided', componentType: 'generators' | 'links' = 'generators') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <AvailabilityEditor project="Site A" component="gt1" componentType={componentType} mode={mode} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  api.generateAvailability.mockResolvedValue(PENDING)
})
afterEach(() => cleanup())

describe('AvailabilityEditor', () => {
  it('previews a seasonal maintenance rule in Guided and shows the share stats', async () => {
    renderEditor()
    expect(screen.queryByTestId('availability-advanced')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Winter' }))
    await userEvent.clear(screen.getByLabelText('Weeks off'))
    await userEvent.type(screen.getByLabelText('Weeks off'), '3')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(api.generateAvailability).toHaveBeenCalledWith('Site A', {
      kind: 'maintenance', components: ['gt1'], component_type: 'generators',
      params: { rule: { season: 'winter', weeks: 3 } },
    }))
    expect(await screen.findByTestId('pending-card')).toBeTruthy()
    expect(screen.getByText('0.96')).toBeTruthy()
  })

  it('posts dated windows from Advanced', async () => {
    renderEditor('expert')
    await userEvent.click(screen.getByLabelText(/Dated windows/))
    await userEvent.type(screen.getByLabelText('Window 1 from'), '2027-06-01')
    await userEvent.type(screen.getByLabelText('Window 1 to'), '2027-06-14')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(api.generateAvailability).toHaveBeenCalledWith('Site A', expect.objectContaining({
      params: { windows: [{ from: '2027-06-01', to: '2027-06-14', available: 0 }] },
    })))
  })

  it('posts a must-run floor for a link', async () => {
    renderEditor('guided', 'links')
    await userEvent.click(screen.getByRole('button', { name: 'Must-run' }))
    await userEvent.type(screen.getByLabelText('Floor %'), '40')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(api.generateAvailability).toHaveBeenCalledWith('Site A', {
      kind: 'must_run', components: ['gt1'], component_type: 'links', params: { share: 0.4 },
    }))
  })

  it('posts heating-led winter and summer floors', async () => {
    renderEditor('expert')
    await userEvent.click(screen.getByRole('button', { name: 'Must-run' }))
    await userEvent.click(screen.getByLabelText(/Winter and summer floors/))
    await userEvent.type(screen.getByLabelText('Winter floor %'), '60')
    await userEvent.type(screen.getByLabelText('Summer floor %'), '20')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(api.generateAvailability).toHaveBeenCalledWith('Site A', expect.objectContaining({
      params: { winter_share: 0.6, summer_share: 0.2 },
    })))
  })

  it('posts derating with the loss per kelvin as a fraction', async () => {
    renderEditor('expert')
    await userEvent.click(screen.getByRole('button', { name: 'Derating' }))
    await userEvent.type(screen.getByLabelText('Loss % per K'), '0.5')
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    await waitFor(() => expect(api.generateAvailability).toHaveBeenCalledWith('Site A', expect.objectContaining({
      kind: 'derating', params: { floor: 0.7, per_k: 0.005 },
    })))
  })

  it('shows forced outages read-only, leading to the asset\'s outage inputs', async () => {
    useUIStore.getState().setSlidePanel('timeseries')
    renderEditor('expert')
    await userEvent.click(screen.getByRole('button', { name: 'Forced outages' }))
    expect(screen.getByTestId('forced-outages-note').textContent).toContain('not in the dispatch')
    expect(screen.queryByRole('button', { name: /Preview/ })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /outage rate and repair time/ }))
    const ui = useUIStore.getState()
    expect(ui.activeSlidePanel).toBeNull()
    expect(ui.selectedComponent).toEqual({ type: 'Generator', name: 'gt1' })
    expect(ui.propertiesEditRequest).toEqual({ type: 'Generator', name: 'gt1' })
    await userEvent.click(screen.getByRole('button', { name: /reliability study/ }))
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().resultsTabRequest).toBe('adequacy')
    expect(api.generateAvailability).not.toHaveBeenCalled()
  })

  it('shows a refusal inline', async () => {
    api.generateAvailability.mockRejectedValue({ response: { status: 422, data: { detail: {
      error_kind: 'weather_driven_availability', message: 'solar availability comes from the weather' } } } })
    renderEditor()
    await userEvent.click(screen.getByRole('button', { name: /Preview/ }))
    expect((await screen.findByTestId('editor-error')).textContent).toContain('from the weather')
  })
})
