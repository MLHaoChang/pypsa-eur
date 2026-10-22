import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useUIStore } from '../../store/uiStore'
import CommercialLibraryPage from './CommercialLibraryPage'

let onOpenInputs: (() => void) | undefined
vi.mock('../results/investment/LibraryBrowser', () => ({
  default: (p: { onOpenInputs?: () => void }) => {
    onOpenInputs = p.onOpenInputs
    return <div data-testid="ic-library" />
  },
}))

afterEach(() => { cleanup(); useUIStore.setState({ activeSlidePanel: null, economicsTabRequest: null }) })

describe('CommercialLibraryPage', () => {
  it('names what the library holds and mounts the browser', () => {
    render(<CommercialLibraryPage />)
    expect(screen.getByText('Tariffs, contracts, connection agreements and price series.')).toBeTruthy()
    expect(screen.getByTestId('ic-library')).toBeTruthy()
  })

  it('links back to Economics › Tariffs & contracts after an attach', () => {
    render(<CommercialLibraryPage />)
    onOpenInputs?.()
    expect(useUIStore.getState().activeSlidePanel).toBe('economics')
    expect(useUIStore.getState().economicsTabRequest).toBe('tariffs')
  })
})
