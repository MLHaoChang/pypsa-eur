// expert-catalogue §6: the Parameter guide drawer. Entry rows from the catalogue
// (missing ones left out), search and browse, Back, Esc first, focus return, the
// dock left uncovered, and the Properties form underneath left mounted.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import GuideDrawer, { DOCK_RAIL_WIDTH, GUIDE_WIDTH, UNKNOWN_NOTICE, guideRightOffset } from './GuideDrawer'
import { useGuideStore } from './guideStore'
import * as catalogueApi from './api'
import { useUIStore } from '../store/uiStore'
import { CATALOGUE } from './guideFixture'

function renderWith(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}
const open = (id?: string, opener?: HTMLElement) => act(() => useGuideStore.getState().openGuide(id, opener))
const drawer = () => screen.getByRole('dialog', { name: 'Parameter guide' })

beforeEach(() => {
  vi.spyOn(catalogueApi, 'fetchCatalogue').mockResolvedValue(CATALOGUE)
  useGuideStore.setState({ view: null, history: [], opener: null })
  useUIStore.setState({
    uiMode: 'expert', fieldDrawer: null, selectedComponent: null, activeSlidePanel: null, rightPanelOpen: true,
    paletteMode: null, propertiesEditRequest: null, assistantDockOpen: false, assistantDockWidth: 400,
  })
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); useGuideStore.setState({ view: null, history: [], opener: null }) })

describe('a guide entry', () => {
  it('shows every row the catalogue has, with "Used by" split, and the model name only on its own line', async () => {
    renderWith(<GuideDrawer />)
    open('generator.capacity')
    const entry = await screen.findByTestId('guide-entry')
    const text = entry.textContent!
    expect(within(entry).getByRole('heading', { name: 'Capacity' })).toBeTruthy()
    expect(text).toContain('Generator · Size and limits')
    expect(text).toContain('The largest output the asset can deliver.')
    expect(text).toContain('What it meansThe largest output, in MW AC.')
    expect(text).toContain('Needed forDispatch')
    expect(text).toContain('Also used byLoad flow, Finance')
    expect(text).toContain('DefaultBuilt-in default 0 MW')
    expect(text).toContain('ExampleA 3 MWp roof: 2.5 MW.')
    expect(within(entry).getByRole('button', { name: 'Largest size ›' })).toBeTruthy()
    expect(text).not.toContain('Typical values')
    // RAW-NAMES-1: the model name is on the "In model files" line and nowhere else.
    const model = screen.getByTestId('guide-model-name')
    expect(model.textContent).toBe('p_nom')
    expect(model.className).toMatch(/font-mono/)
    expect(model.className).toMatch(/truncate/)
    model.remove()
    expect(drawer().textContent).not.toMatch(/p_nom/)
    for (const el of Array.from(drawer().querySelectorAll('*'))) {
      for (const a of Array.from(el.attributes)) expect(a.value).not.toMatch(/p_nom/)
    }
  })

  it('leaves out rows with nothing to say', async () => {
    renderWith(<GuideDrawer />)
    open('line.length')
    const entry = await screen.findByTestId('guide-entry')
    for (const label of ['What it means', 'Needed for', 'Also used by', 'Typical values', 'Default', 'Related', 'Example']) {
      expect(within(entry).queryByText(label)).toBeNull()
    }
    expect(within(entry).getByText('In model files')).toBeTruthy()
  })

  it('opens a former id on its current entry', async () => {
    renderWith(<GuideDrawer />)
    open('generator.p_nom')
    expect(await screen.findByRole('heading', { name: 'Capacity' })).toBeTruthy()
  })

  it('"Open on <asset> ›" jumps to the selected asset when it has the field', async () => {
    useUIStore.setState({ selectedComponent: { type: 'Generator', name: 'PV roof' } })
    renderWith(<GuideDrawer />)
    open('generator.capacity')
    fireEvent.click(await screen.findByRole('button', { name: 'Open on PV roof ›' }))
    expect(useGuideStore.getState().view).toBeNull()
    expect(useUIStore.getState().propertiesEditRequest).toEqual({ type: 'Generator', name: 'PV roof', field: 'p_nom' })
  })

  it('"Choose an asset ›" when the asset in hand is of another class; nothing for an entry with no field', async () => {
    useUIStore.setState({ selectedComponent: { type: 'Line', name: 'L1' } })
    renderWith(<GuideDrawer />)
    open('globalconstraint.constant')
    await screen.findByTestId('guide-entry')
    expect(screen.queryByTestId('guide-open-on')).toBeNull()
    expect(screen.queryByTestId('guide-choose-asset')).toBeNull()
    open('generator.capacity')
    fireEvent.click(await screen.findByRole('button', { name: 'Choose an asset ›' }))
    expect(useGuideStore.getState().view).toBeNull()
    expect(useUIStore.getState().paletteMode).toBe('all')
  })

  it('Back retraces the guide, then returns to the asset; Back is the first focusable control', async () => {
    useUIStore.setState({ selectedComponent: { type: 'Generator', name: 'PV roof' } })
    renderWith(<GuideDrawer />)
    open('generator.capacity')
    await screen.findByTestId('guide-entry')
    const first = () => drawer().querySelector<HTMLElement>('button, input, [href]')
    expect(first()?.textContent).toBe('Back to PV roof')
    fireEvent.click(screen.getByRole('button', { name: 'Largest size ›' }))
    expect(await screen.findByRole('heading', { name: 'Largest size' })).toBeTruthy()
    expect(first()?.textContent).toBe('Back to Capacity')
    fireEvent.click(first()!)
    expect(await screen.findByRole('heading', { name: 'Capacity' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Back to PV roof' }))
    expect(useGuideStore.getState().view).toBeNull()
  })
})

describe('search and browse', () => {
  it('finds entries with the palette index and opens one', async () => {
    renderWith(<GuideDrawer />)
    open()
    const search = await screen.findByRole('searchbox', { name: 'Search the parameter guide' })
    fireEvent.change(search, { target: { value: 'p_nom' } })
    const hit = await screen.findByRole('button', { name: /Capacity.*matches the model-file name/ })
    expect(hit.textContent).not.toMatch(/p_nom/)
    fireEvent.click(hit)
    expect(await screen.findByRole('heading', { name: 'Capacity' })).toBeTruthy()
    // Back returns to the results, query kept.
    fireEvent.click(screen.getByRole('button', { name: 'Back to the results' }))
    expect(((await screen.findByRole('searchbox')) as HTMLInputElement).value).toBe('p_nom')
  })

  it('browse tabs are a real tablist: arrows move the selection and focus', async () => {
    renderWith(<GuideDrawer />)
    open()
    const tabs = await screen.findAllByRole('tab')
    expect(tabs.map(t => t.textContent)).toEqual(['Asset type', 'Group', 'Function'])
    expect(tabs[0].getAttribute('aria-selected')).toBe('true')
    expect(tabs.map(t => t.tabIndex)).toEqual([0, -1, -1])
    tabs[0].focus()
    fireEvent.keyDown(tabs[0], { key: 'ArrowLeft' })
    const fn = screen.getByRole('tab', { name: 'Function' })
    expect(fn.getAttribute('aria-selected')).toBe('true')
    expect(document.activeElement).toBe(fn)
    fireEvent.keyDown(fn, { key: 'Home' })
    expect(screen.getByRole('tab', { name: 'Asset type' }).getAttribute('aria-selected')).toBe('true')
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowRight' })
    expect(screen.getByRole('tab', { name: 'Group' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tabpanel').getAttribute('aria-labelledby')).toBe('guide-tab-group')
    // Internal asset types are not listed.
    fireEvent.click(screen.getByRole('tab', { name: 'Asset type' }))
    expect(screen.queryByText('Shortfall')).toBeNull()
  })

  it('browse by function: what it needs, in plain names', async () => {
    renderWith(<GuideDrawer />)
    open('function.dispatch')
    expect(await screen.findByRole('heading', { name: 'What does dispatch need?' })).toBeTruthy()
    expect(screen.getByText('Needed')).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Capacity/ })).toBeTruthy()
  })

  it('an unknown id says so as a status and puts the caret in the search field', async () => {
    renderWith(<GuideDrawer />)
    open('nothing.here')
    const status = await screen.findByRole('status')
    await waitFor(() => expect(status.textContent).toBe(UNKNOWN_NOTICE))
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('searchbox')))
  })
})

describe('stacking, Esc and focus', () => {
  it('Esc closes the guide before anything else sees it, and only while it is open', async () => {
    const later = vi.fn()
    window.addEventListener('keydown', later)
    renderWith(<GuideDrawer />)
    open('generator.capacity')
    await screen.findByTestId('guide-entry')
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(useGuideStore.getState().view).toBeNull()
    expect(later).not.toHaveBeenCalled()
    // Closed: Esc reaches the field drawer, the Properties panel, the dock.
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(later).toHaveBeenCalledTimes(1)
    window.removeEventListener('keydown', later)
  })

  it('the palette on top takes Esc first', async () => {
    renderWith(<GuideDrawer />)
    open('generator.capacity')
    await screen.findByTestId('guide-entry')
    act(() => useUIStore.setState({ paletteMode: 'all' }))
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(useGuideStore.getState().view).not.toBeNull()
  })

  it('moves focus in on open and returns it to the opener on close', async () => {
    const opener = document.createElement('button')
    document.body.append(opener)
    opener.focus()
    renderWith(<GuideDrawer />)
    open('generator.capacity')
    await screen.findByTestId('guide-entry')
    await waitFor(() => expect(drawer().contains(document.activeElement)).toBe(true))
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' })
    await waitFor(() => expect(document.activeElement).toBe(opener))
    opener.remove()
  })

  it('an opener that is gone hands focus to the palette launcher', async () => {
    const header = document.createElement('header')
    const trigger = document.createElement('button')
    trigger.title = 'Command palette — switch projects (Ctrl+K)'
    header.append(trigger)
    const opener = document.createElement('button')
    document.body.append(header, opener)
    renderWith(<GuideDrawer />)
    open('generator.capacity', opener)
    await screen.findByTestId('guide-entry')
    opener.remove()
    fireEvent.click(screen.getByRole('button', { name: 'Close the guide' }))
    await waitFor(() => expect(document.activeElement).toBe(trigger))
    header.remove()
  })

  it('Expert: over the Properties panel, whose form stays mounted with its unsaved input', async () => {
    useUIStore.setState({ selectedComponent: { type: 'Generator', name: 'PV roof' } })
    renderWith(<><input aria-label="Capacity input" defaultValue="2.4" /><GuideDrawer /></>)
    const input = screen.getByLabelText('Capacity input') as HTMLInputElement
    fireEvent.change(input, { target: { value: '7' } })
    open('generator.capacity')
    await screen.findByTestId('guide-entry')
    expect(input.isConnected).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Back to PV roof' }))
    expect(useGuideStore.getState().view).toBeNull()
    expect((screen.getByLabelText('Capacity input') as HTMLInputElement).value).toBe('7')
  })

  it('Guided with the field drawer open: renders nothing (the field drawer hosts the guide)', async () => {
    useUIStore.setState({ uiMode: 'guided', fieldDrawer: { section: 'assets', panel: null, component: 'Generator', entity: 'PV roof', field: 'p_nom' } })
    renderWith(<GuideDrawer />)
    open('generator.capacity')
    expect(screen.queryByRole('dialog', { name: 'Parameter guide' })).toBeNull()
  })

  it('is non-modal and the Properties panel\'s width', async () => {
    renderWith(<GuideDrawer />)
    open('generator.capacity')
    expect(drawer().getAttribute('aria-modal')).toBe('false')
    expect(drawer().style.width).toBe(`${GUIDE_WIDTH}px`)
  })
})

describe('beside the assistant dock', () => {
  const setWidth = (w: number) => act(() => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: w })
    window.dispatchEvent(new Event('resize'))
  })
  afterEach(() => setWidth(1024))

  it('sits left of the open dock, never over it, when both fit', async () => {
    setWidth(1600)
    useUIStore.setState({ assistantDockOpen: true, assistantDockWidth: 420 })
    renderWith(<GuideDrawer />)
    open('generator.capacity')
    expect(drawer().style.right).toBe('420px')
    act(() => useUIStore.setState({ assistantDockWidth: 500 }))
    expect(drawer().style.right).toBe('500px')
  })

  it('leaves the collapsed rail uncovered', async () => {
    renderWith(<GuideDrawer />)
    open('generator.capacity')
    expect(drawer().style.right).toBe(`${DOCK_RAIL_WIDTH}px`)
  })

  it('narrow: takes the dock\'s slot, and the dock is back when the guide closes', async () => {
    setWidth(900)
    useUIStore.setState({ assistantDockOpen: true, assistantDockWidth: 420 })
    renderWith(<GuideDrawer />)
    open('generator.capacity')
    expect(drawer().style.right).toBe('0px')
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Parameter guide' })).toBeNull()
    expect(useUIStore.getState().assistantDockOpen).toBe(true)
  })

  it('the rule', () => {
    expect(guideRightOffset(1600, true, 420)).toBe(420)
    expect(guideRightOffset(420 + GUIDE_WIDTH + 320, true, 420)).toBe(420)
    expect(guideRightOffset(420 + GUIDE_WIDTH + 319, true, 420)).toBe(0)
    expect(guideRightOffset(500, false, 420)).toBe(DOCK_RAIL_WIDTH)
  })
})
