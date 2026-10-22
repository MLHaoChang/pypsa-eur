import { beforeEach, describe, expect, it } from 'vitest'
import { act, fireEvent, render as rtlRender, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { useUIStore } from '../store/uiStore'
import { FieldDrawer } from './FieldDrawer'
import { openFlowTargetInPlace } from '../layout/flowNavigation'

const render = (ui: ReactElement) => rtlRender(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>)

const target = { section: 'connection', panel: null, component: 'Link', entity: 'grid_import', field: 'p_nom' } as const

beforeEach(() => useUIStore.setState({ fieldDrawer: null, uiMode: 'guided', selectedComponent: null, activeSlidePanel: null }))

describe('FieldDrawer', () => {
  it('renders nothing until an item opens a field', () => {
    render(<FieldDrawer>{() => <p>editor</p>}</FieldDrawer>)
    expect(screen.queryByTestId('field-drawer')).toBeNull()
  })

  it('opens a component target in place, names it in words, and closes', () => {
    render(<FieldDrawer>{t => <p>editor for {t.entity}</p>}</FieldDrawer>)
    act(() => openFlowTargetInPlace(target))
    expect(screen.getByTestId('field-drawer').textContent).toContain("Connection & grid › Link 'grid_import' › Capacity")
    expect(screen.getByText('editor for grid_import')).toBeTruthy()
    expect(useUIStore.getState().uiMode).toBe('guided')
    // The item's section stays active, not the Link class's own (Assets).
    expect(useUIStore.getState().activeSection).toBe('connection')
    fireEvent.click(screen.getByTestId('field-drawer-close'))
    expect(screen.queryByTestId('field-drawer')).toBeNull()
  })

  it('"Open in Expert" switches mode, keeps the section and closes the drawer', () => {
    render(<FieldDrawer>{() => <p>editor</p>}</FieldDrawer>)
    act(() => openFlowTargetInPlace(target))
    fireEvent.click(screen.getByTestId('field-drawer-expert'))
    expect(useUIStore.getState().uiMode).toBe('expert')
    expect(useUIStore.getState().activeSection).toBe('connection')
    expect(useUIStore.getState().fieldDrawer).toBeNull()
  })

  it('a target with no field opens its section in Expert (the only way to reach it)', () => {
    openFlowTargetInPlace({ section: 'loads', panel: null, component: null, entity: null, field: null })
    expect(useUIStore.getState().uiMode).toBe('expert')
    expect(useUIStore.getState().fieldDrawer).toBeNull()
  })
})
