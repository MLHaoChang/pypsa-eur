import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, act } from '@testing-library/react'
import ProjectOpeningBanner, { OPENING_STRIP_DELAY_MS } from './ProjectOpeningBanner'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'

const qcTest = new QueryClient()
const renderBanner = () => render(<QueryClientProvider client={qcTest}><ProjectOpeningBanner /></QueryClientProvider>)

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { cleanup(); useUIStore.getState().setOpeningProject(null); vi.useRealTimers() })

function open(target: string) {
  renderBanner()
  act(() => { useUIStore.getState().setOpeningProject(target) })
}

describe('ProjectOpeningBanner', () => {
  it('renders nothing when no project is opening', () => {
    renderBanner()
    expect(screen.queryByTestId('project-opening')).toBeNull()
  })
  it('stays hidden for a quick open', () => {
    open('concentrator')
    act(() => { vi.advanceTimersByTime(OPENING_STRIP_DELAY_MS - 100) })
    expect(screen.queryByTestId('project-opening')).toBeNull()
  })
  it('shows a progress bar naming the project once the open is slow', () => {
    open('concentrator')
    act(() => { vi.advanceTimersByTime(OPENING_STRIP_DELAY_MS + 10) })
    expect(screen.getByRole('progressbar', { name: 'Opening concentrator' })).toBeTruthy()
  })
  it('never shows a raw UUID', () => {
    open('3f2b8c1e-9d4a-4e7b-8a61-0c5d2e9f7a13')
    act(() => { vi.advanceTimersByTime(OPENING_STRIP_DELAY_MS + 10) })
    expect(screen.getByRole('progressbar', { name: 'Opening project' })).toBeTruthy()
    expect(screen.queryByText(/3f2b8c1e/)).toBeNull()
  })
  it('shows the failure with a Retry after a failed deep-link open', () => {
    renderBanner()
    act(() => { useUIStore.getState().setOpenFailure({ target: 'grid', message: 'Backend down.' }) })
    expect(screen.getByTestId('project-open-failed').textContent).toContain('Could not open grid')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
    act(() => { useUIStore.getState().setOpenFailure(null) })
  })
})
