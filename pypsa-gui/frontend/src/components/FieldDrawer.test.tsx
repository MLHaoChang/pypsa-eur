import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render as rtlRender, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { useUIStore } from '../store/uiStore'
import { FieldDrawer } from './FieldDrawer'
import { openFlowTargetInPlace } from '../layout/flowNavigation'
import { useGuideStore } from '../catalogue/guideStore'
import * as catalogueApi from '../catalogue/api'
import { CATALOGUE } from '../catalogue/guideFixture'

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
    expect(screen.getByTestId('field-drawer').textContent).toContain("Connection & grid › Link “grid_import” › Capacity")
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

describe('FieldDrawer — the Parameter guide inside it (expert-catalogue §6.1)', () => {
  beforeEach(() => {
    vi.spyOn(catalogueApi, 'fetchCatalogue').mockResolvedValue(CATALOGUE)
    useGuideStore.setState({ view: null, history: [], opener: null })
  })
  afterEach(() => { vi.restoreAllMocks(); useGuideStore.setState({ view: null, history: [], opener: null }) })

  it('replaces the body with the guide and "Back to <field>", keeping the form and its unsaved input', async () => {
    render(<FieldDrawer>{() => <input aria-label="Capacity input" defaultValue="2.4" />}</FieldDrawer>)
    act(() => openFlowTargetInPlace(target))
    const input = screen.getByLabelText('Capacity input') as HTMLInputElement
    fireEvent.change(input, { target: { value: '7' } })
    input.focus()
    act(() => useGuideStore.getState().openGuide('generator.capacity'))
    expect(await screen.findByTestId('guide-entry')).toBeTruthy()
    // Hidden, not unmounted: one drawer, no second layer.
    expect(input.isConnected).toBe(true)
    expect(input.closest('[hidden]')).not.toBeNull()
    expect(screen.getAllByTestId('field-drawer')).toHaveLength(1)
    const back = screen.getByRole('button', { name: 'Back to Capacity' })
    expect(screen.getByTestId('guide-panel').querySelector('button')).toBe(back)
    fireEvent.click(back)
    expect(useGuideStore.getState().view).toBeNull()
    expect(input.closest('[hidden]')).toBeNull()
    expect((screen.getByLabelText('Capacity input') as HTMLInputElement).value).toBe('7')
    // Focus returns to the field it was opened from.
    await waitFor(() => expect(document.activeElement).toBe(input))
  })

  it('Esc closes the guide first and leaves the field drawer open', async () => {
    render(<FieldDrawer>{() => <p>editor</p>}</FieldDrawer>)
    act(() => openFlowTargetInPlace(target))
    act(() => useGuideStore.getState().openGuide('generator.capacity'))
    await screen.findByTestId('guide-entry')
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(useGuideStore.getState().view).toBeNull()
    expect(useUIStore.getState().fieldDrawer).not.toBeNull()
    expect(screen.getByText('editor')).toBeTruthy()
  })
})
