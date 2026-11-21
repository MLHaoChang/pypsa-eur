// Gap 03: the recipe card, Guided section 2's drawer and Expert Properties alike: it shows the
// server's recipe, saves the Link's efficiencies through the asset-write chokepoint, and refuses a
// value that is not above 0 with the field's label, keeping what was typed.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { FlowRecipe } from '../../api/flow'
import { updateAsset } from '../../utils/assetWrite'
import RecipeCard from './RecipeCard'

const HB: FlowRecipe = {
  label: 'Haber-Bosch', product: { carrier: 'NH3', label: 'NH3' }, per: 't', product_per: 5.166,
  electricity: { value: 1277.5518, unit: 'kWh' },
  inputs: [{ port: 2, carrier: 'H2', label: 'Hydrogen', value: 5.93, unit: 'MWh' }],
}
let holds: unknown[] = []
vi.mock('../../hooks/useProjectFlow', () => ({
  useProjectFlow: () => ({ section: (id: string) => (id === 'carriers' ? { holds } : null) }),
}))
vi.mock('../../utils/assetWrite', () => ({ updateAsset: vi.fn(async () => ({})) }))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn() } }))

function renderCard(name = 'haber_bosch') {
  return render(<QueryClientProvider client={new QueryClient()}><RecipeCard name={name} /></QueryClientProvider>)
}

beforeEach(() => {
  holds = [{ component: 'Link', entity: 'haber_bosch', recipe: HB }]
  vi.mocked(updateAsset).mockClear()
})
afterEach(cleanup)

describe('RecipeCard', () => {
  it('shows each input and the electricity per unit of product, in plain words', () => {
    renderCard()
    expect(screen.getByText('Recipe, per tonne of NH3')).toBeTruthy()
    expect(screen.getByText('Hydrogen per tonne of NH3')).toBeTruthy()
    expect(screen.getByText('5.93 MWh')).toBeTruthy()
    expect(screen.getByText('Electricity per tonne of NH3')).toBeTruthy()
    expect(screen.getByText('1,277.55 kWh')).toBeTruthy()
    expect(screen.queryByText(/haber_bosch|efficiency/)).toBeNull()
  })

  it('is absent for a Link with no recipe', () => {
    renderCard('grid_import')
    expect(screen.queryByTestId('recipe-card')).toBeNull()
  })

  it('saves the efficiencies the typed rates make', async () => {
    renderCard()
    fireEvent.click(screen.getByTestId('recipe-edit'))
    fireEvent.change(screen.getByTestId('recipe-input-2'), { target: { value: '6' } })
    fireEvent.click(screen.getByTestId('recipe-save'))
    await waitFor(() => expect(updateAsset).toHaveBeenCalled())
    const [, , cls, name, patch] = vi.mocked(updateAsset).mock.calls[0] as unknown as
      [unknown, unknown, string, string, Record<string, number>]
    expect([cls, name]).toEqual(['links', 'haber_bosch'])
    expect(patch.efficiency).toBeCloseTo(5.166 / 1.2775518, 6)
    expect(patch.efficiency2).toBeCloseTo(-6 / 1.2775518, 6)
  })

  it('refuses a value not above 0 with its label, keeps the entry and writes nothing', async () => {
    renderCard()
    fireEvent.click(screen.getByTestId('recipe-edit'))
    fireEvent.change(screen.getByTestId('recipe-input-2'), { target: { value: '0' } })
    fireEvent.click(screen.getByTestId('recipe-save'))
    expect((await screen.findByTestId('recipe-refusal')).textContent).toBe('Hydrogen per tonne of NH3: must be above 0')
    expect((screen.getByTestId('recipe-input-2') as HTMLInputElement).value).toBe('0')
    expect(updateAsset).not.toHaveBeenCalled()
  })
})
