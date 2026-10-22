// Guided section 7 shows the commercial card, and the Guided link lands on it.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../store/uiStore'
import { openSectionInGuided } from '../../layout/flowNavigation'
import { GenericSectionCard } from './SectionView'

vi.mock('../results/investment/TariffSection', () => ({ default: () => <div /> }))
vi.mock('../results/investment/ContractsEditor', () => ({ default: () => <div /> }))
vi.mock('../results/investment/FinanceInputsEditor', () => ({ default: () => <div /> }))
vi.mock('../results/investment/ParticipantsDesigner', () => ({ default: () => <div /> }))
vi.mock('../economics/RatesForm', () => ({ default: () => <div /> }))

afterEach(cleanup)

describe('Guided section 7', () => {
  it('openSectionInGuided shows the hub on that section, and its card renders', () => {
    useUIStore.setState({ uiMode: 'guided', activeSlidePanel: 'results', activeSection: null })
    openSectionInGuided('commercial')
    expect(useUIStore.getState().activeSlidePanel).toBe('hubDesign')
    expect(useUIStore.getState().activeSection).toBe('commercial')
    render(<QueryClientProvider client={new QueryClient()}><GenericSectionCard id="commercial" /></QueryClientProvider>)
    expect(screen.getByTestId('hub-card-commercial')).toBeTruthy()
  })
})
