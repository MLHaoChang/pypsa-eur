// Guided section 7: the same editors Expert's Economics panel mounts, so a
// Guided user can still set the tariff, contracts and finance (checklist 12).
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../../../store/uiStore'
import { CommercialCard } from './CommercialCard'

vi.mock('../../results/investment/TariffSection', () => ({ default: () => <div data-testid="ic-tariff" /> }))
vi.mock('../../results/investment/ContractsEditor', () => ({ default: () => <div data-testid="ic-contracts" /> }))
vi.mock('../../results/investment/FinanceInputsEditor', () => ({ default: () => <div data-testid="ic-finance-inputs" /> }))
vi.mock('../../results/investment/ParticipantsDesigner', () => ({ default: () => <div data-testid="ic-participants" /> }))
vi.mock('../../economics/RatesForm', () => ({ default: () => <div data-testid="economics-rates" /> }))

afterEach(() => { cleanup(); useUIStore.setState({ uiMode: 'guided', activeSlidePanel: null }) })

describe('CommercialCard', () => {
  it('shows tariff, contracts and finance up front, the rest under More settings', () => {
    render(<QueryClientProvider client={new QueryClient()}><CommercialCard /></QueryClientProvider>)
    for (const id of ['ic-tariff', 'ic-contracts', 'ic-finance-inputs']) expect(screen.getByTestId(id)).toBeTruthy()
    const more = screen.getByTestId('commercial-more')
    expect(more.contains(screen.getByTestId('ic-participants'))).toBe(true)
    expect(more.contains(screen.getByTestId('economics-rates'))).toBe(true)
  })

  it('opens the section in Expert on the one button', () => {
    useUIStore.setState({ uiMode: 'guided' })
    render(<QueryClientProvider client={new QueryClient()}><CommercialCard /></QueryClientProvider>)
    fireEvent.click(screen.getByTestId('section-open-expert-commercial'))
    expect(useUIStore.getState().uiMode).toBe('expert')
    expect(useUIStore.getState().activeSlidePanel).toBe('economics')
  })
})
