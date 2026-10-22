// The note under an asset's lifetime field: a blank lifetime inherits the project
// default and says where it is edited; an own value offers the way back.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { useUIStore } from '../../store/uiStore'
import { LifetimeInherit, defaultLifetime, type FS } from './cardKit'

vi.mock('../../api/simulation', async (orig) => {
  const actual = await orig<typeof import('../../api/simulation')>()
  return { ...actual, simulationApi: { ...actual.simulationApi, getSolverConfig: vi.fn(async () => ({ default_lifetime: 25 })) } }
})

afterEach(cleanup)

function Harness({ start }: { start: string }) {
  const [fs, set] = useState<FS>({ lifetime: start })
  return <><LifetimeInherit fs={fs} set={set} /><output data-testid="lt">{fs.lifetime}</output></>
}
const mount = (start: string) => render(<QueryClientProvider client={new QueryClient()}><Harness start={start} /></QueryClientProvider>)

describe('defaultLifetime', () => {
  it('keeps an own lifetime and leaves a blank, infinite or non-positive one blank', () => {
    expect(defaultLifetime(30)).toBe('30')
    expect(defaultLifetime(null)).toBe('')
    expect(defaultLifetime(Infinity)).toBe('')
    expect(defaultLifetime(0)).toBe('')
  })
})

describe('LifetimeInherit', () => {
  it('names the project default for a blank lifetime and links to the Economic assumptions block', async () => {
    mount('')
    await waitFor(() => expect(screen.getByTestId('asset-lifetime-inherit').textContent).toContain('25 years, project default'))
    expect(screen.getByTestId('asset-lifetime-change').getAttribute('title')).toBe('Opens Solver Settings in Expert')
    fireEvent.click(screen.getByTestId('asset-lifetime-change'))
    expect(useUIStore.getState().solverBlockRequest).toBe('economic')
    expect(useUIStore.getState().activeSlidePanel).toBe('simparams')
  })

  it('offers a reset that names its target and blanks the field', async () => {
    mount('30')
    const reset = screen.getByTestId('asset-lifetime-reset')
    await waitFor(() => expect(reset.textContent).toBe('Reset to project default (25 years)'))
    fireEvent.click(reset)
    expect(screen.getByTestId('lt').textContent).toBe('')
  })

  it('still says "Project default" with the link while the default is unknown', () => {
    mount('')
    expect(screen.getByTestId('asset-lifetime-inherit').textContent).toContain('Project default')
    expect(screen.getByTestId('asset-lifetime-change')).toBeTruthy()
  })
})
