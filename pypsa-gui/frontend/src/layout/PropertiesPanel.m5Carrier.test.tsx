// M5b (plan 2026-10-14-m5-non-asset-forms.md): a Carrier has a Properties
// card, rendered from the asset schema like the Bus panel. A Carriers-tab row
// opens it; it reads out the display name, colour, unit and CO₂ intensity with
// the descriptors' help, and saves the descriptors' patch (a cleared text
// column as "", a rename followed by the selection).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import type { Carrier } from '../api/types'
import PropertiesPanel from './PropertiesPanel'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getCarriers: vi.fn(),
      updateCarrier: vi.fn(async () => ({})),
      getCatalog: vi.fn(async (component: string) => ({ component, attributes: [] })),
    },
  }
})

const GAS: Carrier = { name: 'gas', co2_emissions: 0.2, color: '#d35050', nice_name: 'Natural gas', unit: '' }

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={client}><PropertiesPanel /></QueryClientProvider>)
}

async function tipOf(label: string): Promise<string> {
  const text = await screen.findByText(label, { selector: 'span' })
  const trigger = text.parentElement?.querySelector('[tabindex="0"]')
  if (!trigger) throw new Error(`row ${label} has no tooltip`)
  fireEvent.focus(trigger)
  const tip = (await screen.findByRole('tooltip')).textContent ?? ''
  fireEvent.blur(trigger)
  return tip
}

async function save(): Promise<[string, Record<string, unknown>]> {
  await userEvent.click(await screen.findByRole('button', { name: /^save$/i }))
  await waitFor(() => expect(networkApi.updateCarrier).toHaveBeenCalled())
  const [name, body] = vi.mocked(networkApi.updateCarrier).mock.calls[0]
  return [name, body as unknown as Record<string, unknown>]
}

beforeEach(() => {
  vi.mocked(networkApi.getCarriers).mockReset().mockResolvedValue([GAS])
  vi.mocked(networkApi.updateCarrier).mockClear()
  localStorage.clear()
  useUIStore.setState({ currentProject: 'Demo', rightPanelOpen: true, selectedComponent: { type: 'Carrier', name: 'gas' } })
})
afterEach(() => { useUIStore.setState({ currentProject: null, selectedComponent: null }); localStorage.clear() })

describe('the Carrier card from the asset schema (M5b)', () => {
  it('reads out the carrier under the descriptor labels, with their help', async () => {
    renderPanel()
    expect(await screen.findByText('Natural gas')).toBeTruthy()
    expect(screen.getByText('CO₂ emissions')).toBeTruthy()
    expect(screen.getByTestId('carrier-swatch').getAttribute('style')).toContain('rgb(211, 80, 80)')
    expect(await tipOf('CO₂ emissions')).toContain('divided by its efficiency')
    expect(await tipOf('Display name')).toContain('charts and tables')
  })

  it('edits through the descriptors: t/MWh, the text columns as stored', async () => {
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: /edit carrier/i }))
    expect((await screen.findByLabelText('CO₂ emissions (t/MWh)') as HTMLInputElement).value).toBe('0.2')
    expect((screen.getByLabelText('Display name') as HTMLInputElement).value).toBe('Natural gas')
    expect((screen.getByLabelText('Colour') as HTMLInputElement).value).toBe('#d35050')
  })

  it('saves the descriptors’ patch: the intensity as typed, a cleared display name as ""', async () => {
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: /edit carrier/i }))
    fireEvent.change(await screen.findByLabelText('CO₂ emissions (t/MWh)'), { target: { value: '0.37' } })
    fireEvent.change(screen.getByLabelText('Display name'), { target: { value: '' } })
    const [name, body] = await save()
    expect(name).toBe('gas')
    expect(body.co2_emissions).toBe(0.37)
    expect(body.nice_name).toBe('')
    expect(body.unit).toBe('')
    expect(body.color).toBe('#d35050')
    expect(body.name).toBe('gas')
  })

  it('a rename moves the selection to the new name', async () => {
    renderPanel()
    await userEvent.click(await screen.findByRole('button', { name: /edit carrier/i }))
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'natgas' } })
    const [, body] = await save()
    expect(body.name).toBe('natgas')
    await waitFor(() => expect(useUIStore.getState().selectedComponent).toEqual({ type: 'Carrier', name: 'natgas' }))
  })
})
