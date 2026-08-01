// Regression coverage for Part C's "offer, never rewrite" fix (C4) and the
// carrier_zero_co2 one-click button — added after a review found the
// property that matters most here ("nothing writes co2_emissions without
// the user pressing the button") had NO test guarding it. The logic was
// correct at review time; nothing stopped a future edit from adding an
// effect that fires the mutation on mount. Follows the render/mock/
// userEvent recipe in OverviewPanel.download.test.tsx.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { simulationApi } from '../api/simulation'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import type { PreflightResult, Carrier } from '../api/types'
import IssuesPanel from './IssuesPanel'

vi.mock('../api/simulation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulation')>()
  return { ...actual, simulationApi: { ...actual.simulationApi, preflight: vi.fn() } }
})

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: { ...actual.networkApi, getCarriers: vi.fn(), updateCarrier: vi.fn() },
  }
})

// The full cached row — color/nice_name/unit are deliberately NOT the
// Pydantic defaults, so a payload that lost them (the partial-PUT trap
// `updateBusPosMut` documents) is distinguishable from one that kept them.
const GAS_CARRIER: Carrier = {
  name: 'gas', co2_emissions: 0, color: '#112233', nice_name: 'Natural Gas', unit: 'MWh',
}

function carrierZeroCo2Result(message: string): PreflightResult {
  return {
    ok: false,
    errors: 0,
    warnings: 1,
    issues: [{
      severity: 'warning',
      code: 'carrier_zero_co2',
      component_class: 'Carrier',
      name: 'gas',
      message,
    }],
  }
}

const GAS_MESSAGE =
  "Carrier 'gas' looks like a fossil fuel but has co2_emissions = 0, so every " +
  "emissions figure for it is zero. The catalog value for 'gas' is 0.187 tCO2/MWh."

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <IssuesPanel />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.mocked(simulationApi.preflight).mockReset()
  vi.mocked(networkApi.getCarriers).mockReset()
  vi.mocked(networkApi.updateCarrier).mockReset()
  useUIStore.setState({ currentProject: 'Demo' })
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null })
})

describe('IssuesPanel — carrier_zero_co2 fix button', () => {
  it('does NOT call updateCarrier just from rendering the warning', async () => {
    // This is the regression guard for C4: "nothing may change
    // co2_emissions without the user pressing the button." A future effect
    // that fires the mutation on mount, or a mutation wired to the wrong
    // event, would pass every other assertion in this file and only this
    // one would catch it.
    vi.mocked(simulationApi.preflight).mockResolvedValue(carrierZeroCo2Result(GAS_MESSAGE))
    vi.mocked(networkApi.getCarriers).mockResolvedValue([GAS_CARRIER])

    renderPanel()

    await waitFor(() => expect(screen.getByText('carrier_zero_co2')).toBeDefined())
    await screen.findByRole('button', { name: /Set gas to 0\.187/ })

    expect(networkApi.updateCarrier).not.toHaveBeenCalled()
  })

  it('spreads the cached carrier row and overrides only co2_emissions on click', async () => {
    vi.mocked(simulationApi.preflight).mockResolvedValue(carrierZeroCo2Result(GAS_MESSAGE))
    vi.mocked(networkApi.getCarriers).mockResolvedValue([GAS_CARRIER])
    vi.mocked(networkApi.updateCarrier).mockResolvedValue({ name: 'gas' } as never)

    renderPanel()

    const button = await screen.findByRole('button', { name: /Set gas to 0\.187/ })
    await userEvent.click(button)

    await waitFor(() => expect(networkApi.updateCarrier).toHaveBeenCalledTimes(1))
    // The full spread, not bare {co2_emissions} — color/nice_name/unit must
    // survive the PUT. A partial payload here would silently reset them to
    // Pydantic defaults via the backend's remove+add _update_component.
    expect(networkApi.updateCarrier).toHaveBeenCalledWith('gas', {
      name: 'gas', co2_emissions: 0.187, color: '#112233', nice_name: 'Natural Gas', unit: 'MWh',
    })
  })

  it('never fires a second update from a double-click', async () => {
    let release: (v: unknown) => void = () => {}
    vi.mocked(simulationApi.preflight).mockResolvedValue(carrierZeroCo2Result(GAS_MESSAGE))
    vi.mocked(networkApi.getCarriers).mockResolvedValue([GAS_CARRIER])
    vi.mocked(networkApi.updateCarrier).mockReturnValue(
      new Promise((res) => { release = res }) as never,
    )

    renderPanel()
    const button = await screen.findByRole('button', { name: /Set gas to 0\.187/ })

    await userEvent.click(button)
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(true))
    await userEvent.click(button)

    expect(networkApi.updateCarrier).toHaveBeenCalledTimes(1)
    release({ name: 'gas' })
  })

  it('renders the warning but no fix button when the message carries no catalog value', async () => {
    // _check_carrier_emissions omits the "catalog value" sentence entirely
    // for a fossil-looking carrier the catalog doesn't cover — there is
    // nothing to pre-fill, and C4 forbids offering a button with no value
    // the user can see before pressing. The warning itself must still show.
    const noHintMessage =
      "Carrier 'coal' looks like a fossil fuel but has co2_emissions = 0, so " +
      "every emissions figure for it is zero."
    vi.mocked(simulationApi.preflight).mockResolvedValue({
      ok: false, errors: 0, warnings: 1,
      issues: [{
        severity: 'warning', code: 'carrier_zero_co2', component_class: 'Carrier',
        name: 'coal', message: noHintMessage,
      }],
    })
    vi.mocked(networkApi.getCarriers).mockResolvedValue([
      { name: 'coal', co2_emissions: 0, color: '', nice_name: 'coal', unit: 'MWh' },
    ])

    renderPanel()

    await waitFor(() => expect(screen.getByText(noHintMessage)).toBeDefined())
    expect(screen.queryByRole('button', { name: /Set coal/ })).toBeNull()
    expect(networkApi.updateCarrier).not.toHaveBeenCalled()
  })
})
