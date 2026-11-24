// UX ruling for 03d: one "Grid extension vs island" component, two homes with
// the same words and no second copy of state: Guided section 10 (the Results
// card) and Expert Results › Investment case (its fifth section, for an island
// site only). Both homes render the same card, and it reads the same.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { DecisionStudy, SupplyArchitecturesRecord } from '../../api/decisionStudies'
import { useUIStore } from '../../store/uiStore'
import { HUB_DESIGN_INITIAL, useHubDesignStore } from '../hubDesign/hubDesignStore'

const authMode = vi.hoisted(() => ({ authEnabled: false, ready: true, enableAuth: () => {} }))
vi.mock('../../auth/AuthModeProvider', () => ({ useAuthMode: () => authMode }))
const goal = vi.hoisted(() => ({ archetype: 'off_grid', isPending: false, goal: null, target: null }))
vi.mock('../../hooks/useReliabilityGoal', async (orig) => ({
  ...(await orig<typeof import('../../hooks/useReliabilityGoal')>()), useReliabilityGoal: () => goal,
}))
const api = vi.hoisted(() => ({ list: vi.fn(), supplyArchitectures: vi.fn() }))
vi.mock('../../api/decisionStudies', async (orig) => ({
  ...(await orig<typeof import('../../api/decisionStudies')>()), decisionStudiesApi: api,
}))
// Guided's Results card before its study ran: the not-run branch, which mounts the card too.
vi.mock('../hubDesign/useHubData', () => ({
  useHubStudy: () => ({ study: null, running: false }),
  useHubReview: () => ({ review: null }),
  useHubReport: () => null,
  useHubLevers: () => null,
  useDispatchSolved: () => undefined,
  useHubTemplate: () => ({ template: null }),
}))
vi.mock('../hubDesign/ChaptersSection', () => ({ ChaptersSection: () => null }))
vi.mock('../../hooks/useProjectFlow', () => ({ useProjectFlow: () => ({ flow: null, isPending: false }) }))
vi.mock('../../api/commercial', async (orig) => {
  const actual = await orig<typeof import('../../api/commercial')>()
  return { ...actual, commercialApi: { ...actual.commercialApi,
    getBilling: vi.fn().mockResolvedValue(null), getValueFlowsResult: vi.fn().mockResolvedValue(null),
    getCommercial: vi.fn().mockResolvedValue(null), getPocState: vi.fn().mockResolvedValue({ poc_link: null, open: true }) } }
})

import { ResultsCard } from '../hubDesign/cards/ResultsCard'
import InvestmentTab from './InvestmentTab'
import { GRID_EXTENSION_WORDS } from './GridExtensionCard'

const STUDY = {
  study_id: 'ge1', name: 'Grid extension vs island', question_id: 'grid_extension', currency_year: 2025,
  ledger: { ledger_version: 'v', honesty_notes: [], rows: [
    { key: 'discount_rate', value: 0.07 }, { key: 'battery_storage_lifetime_years', value: 25 }] },
} as unknown as DecisionStudy
const DONE: SupplyArchitecturesRecord = {
  status: 'done', stale: false, best_architecture_id: 'pv+genset',
  rows: [{ architecture_id: 'pv+genset', label: 'PV + Genset', groups: ['pv', 'genset'], status: 'ok',
    total_cost_eur_per_year: 14_000, capex_eur: 0, renewable_fraction: 0.3, sizes: [] }],
}

function mount(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

async function cardText(node: React.ReactNode): Promise<string> {
  mount(node)
  const card = await screen.findByTestId('grid-extension-card')
  await within(card).findByTestId('grid-ext-table')
  const text = card.textContent ?? ''
  cleanup()
  return text
}

beforeEach(() => {
  goal.archetype = 'off_grid'
  api.list.mockResolvedValue([STUDY])
  api.supplyArchitectures.mockResolvedValue(DONE)
  useUIStore.setState({ currentProject: 'Island A', resultsSectionRequest: null })
  useHubDesignStore.setState({ ...HUB_DESIGN_INITIAL, project: 'Island A', ready: true, step: 'results' })
})
afterEach(() => cleanup())

describe('Grid extension vs island: two homes, one card', () => {
  it('Guided Results and Expert Investment case render the same card with the same words', async () => {
    const guided = await cardText(<ResultsCard />)
    useUIStore.setState({ resultsSectionRequest: 'grid_extension' })
    const expert = await cardText(<InvestmentTab />)
    expect(guided).toContain(GRID_EXTENSION_WORDS.title)
    expect(guided).toContain('PV + Genset')
    expect(expert).toBe(guided)
  })

  it('the Expert section is named with the card’s own title, and only for an island site', async () => {
    mount(<InvestmentTab />)
    expect((await screen.findByRole('tab', { name: GRID_EXTENSION_WORDS.title }))).toBeTruthy()
    cleanup()
    goal.archetype = 'strong_grid'
    mount(<InvestmentTab />)
    await screen.findAllByRole('tab')
    expect(screen.queryByRole('tab', { name: GRID_EXTENSION_WORDS.title })).toBeNull()
    expect(screen.getAllByRole('tab')).toHaveLength(4)
  })
})
