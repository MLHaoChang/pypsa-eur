import { beforeEach, describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { useUIStore } from '../store/uiStore'
import { FlowReturnButton } from './FlowReturnButton'
import { openFlowTarget, openFlowTargetInPlace, openSectionInExpert, returnToOrigin, showGridReadiness, withReturnFrom,
  openEconomicAssumptions, openProfileStudio } from './flowNavigation'

const prop = { section: 'connection', panel: null, component: 'Link', entity: 'grid_import', field: 'p_nom' } as const
const issues = { section: 'readiness', panel: 'issues', component: null, entity: null, field: null } as const

const reset = (over: object) => useUIStore.setState({
  fieldDrawer: null, flowReturn: null, selectedComponent: null, activeSlidePanel: null, activeSection: null, ...over })

describe('fix link return to origin', () => {
  it('Expert: the jump closes the Issues panel; Back reopens it', () => {
    reset({ uiMode: 'expert', activeSlidePanel: 'issues', activeSection: 'readiness' })
    render(<FlowReturnButton />)
    expect(screen.queryByTestId('flow-return')).toBeNull()
    act(() => openFlowTarget(prop))
    expect(useUIStore.getState().activeSlidePanel).toBeNull()
    expect(screen.getByTestId('flow-return').textContent).toContain('Back to Readiness and run')
    fireEvent.click(screen.getByTestId('flow-return'))
    expect(useUIStore.getState().activeSlidePanel).toBe('issues')
    expect(useUIStore.getState().uiMode).toBe('expert')
    expect(useUIStore.getState().activeSection).toBe('readiness')
    expect(screen.queryByTestId('flow-return')).toBeNull()
  })

  it('Expert: restores the scroll offset of the container holding the link', async () => {
    reset({ uiMode: 'expert', activeSlidePanel: 'issues', activeSection: 'readiness' })
    const box = document.createElement('div')
    const btn = document.createElement('button')
    box.appendChild(btn); document.body.appendChild(box)
    Object.defineProperty(box, 'scrollHeight', { value: 1000, configurable: true })
    Object.defineProperty(box, 'clientHeight', { value: 100, configurable: true })
    box.scrollTop = 0
    let top = 240
    Object.defineProperty(box, 'scrollTop', { get: () => top, set: v => { top = v }, configurable: true })
    btn.focus()
    act(() => openFlowTarget(prop))
    top = 0
    act(() => returnToOrigin())
    await new Promise(r => setTimeout(r, 50))
    expect(top).toBe(240)
    box.remove()
  })

  it('Guided: Back from an Expert Add form drops the form, so a later drawer shows its own editor', () => {
    reset({ uiMode: 'guided', activeSlidePanel: 'hubDesign', activeSection: 'carriers', creationItem: null })
    act(() => withReturnFrom(() => {
      openSectionInExpert('carriers')
      useUIStore.getState().setCreationItem({ id: 'process_unit', label: 'Process unit' })
    }))
    expect(useUIStore.getState().uiMode).toBe('expert')
    expect(useUIStore.getState().creationItem).not.toBeNull()
    act(() => returnToOrigin())
    expect(useUIStore.getState().uiMode).toBe('guided')
    expect(useUIStore.getState().creationItem).toBeNull()
  })

  it('Back within one mode keeps an Add form the jump opened beside the view', () => {
    reset({ uiMode: 'expert', activeSlidePanel: 'issues', activeSection: 'readiness', creationItem: null })
    act(() => withReturnFrom(() => useUIStore.getState().setCreationItem({ id: 'bus', label: 'Bus (Node)' })))
    act(() => returnToOrigin())
    expect(useUIStore.getState().creationItem).toEqual({ id: 'bus', label: 'Bus (Node)' })
  })

  it('Guided: the jump replaces the drawer; Back reinstates the previous one', () => {
    reset({ uiMode: 'guided', activeSlidePanel: 'hubDesign', activeSection: 'readiness', fieldDrawer: issues })
    render(<FlowReturnButton />)
    act(() => openFlowTargetInPlace(prop))
    expect(useUIStore.getState().fieldDrawer).toEqual(prop)
    expect(screen.getByTestId('flow-return').textContent).toContain('Back to Readiness and run')
    fireEvent.click(screen.getByTestId('flow-return'))
    expect(useUIStore.getState().fieldDrawer).toEqual(issues)
    expect(useUIStore.getState().uiMode).toBe('guided')
  })

  it('Guided: from a card with no drawer, Back closes the drawer onto the card', () => {
    reset({ uiMode: 'guided', activeSlidePanel: 'hubDesign', activeSection: 'connection' })
    render(<FlowReturnButton />)
    act(() => openFlowTargetInPlace(prop))
    expect(screen.getByTestId('flow-return').textContent).toContain('Back to Connection & grid')
    fireEvent.click(screen.getByTestId('flow-return'))
    expect(useUIStore.getState().fieldDrawer).toBeNull()
    expect(useUIStore.getState().activeSlidePanel).toBe('hubDesign')
  })

  it('Guided: a jump from a non-hub panel returns to that panel, not the hub', () => {
    reset({ uiMode: 'guided', activeSlidePanel: 'results', activeSection: 'results' })
    render(<FlowReturnButton />)
    act(() => openFlowTargetInPlace(prop))
    expect(useUIStore.getState().fieldDrawer).toEqual(prop)
    fireEvent.click(screen.getByTestId('flow-return'))
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().fieldDrawer).toBeNull()
  })

  it('a jump that switches the mode records the mode it left; Back switches back', () => {
    reset({ uiMode: 'guided', activeSlidePanel: 'hubDesign', activeSection: 'connection' })
    render(<FlowReturnButton />)
    act(() => openFlowTarget(prop))
    expect(useUIStore.getState().uiMode).toBe('expert')
    expect(useUIStore.getState().flowReturn?.mode).toBe('guided')
    fireEvent.click(screen.getByTestId('flow-return'))
    expect(useUIStore.getState().uiMode).toBe('guided')
    expect(useUIStore.getState().activeSlidePanel).toBe('hubDesign')
    expect(useUIStore.getState().activeSection).toBe('connection')
    // And the other way round.
    reset({ uiMode: 'expert', activeSlidePanel: 'issues', activeSection: 'readiness' })
    act(() => openFlowTarget(prop))
    expect(useUIStore.getState().flowReturn?.mode).toBe('expert')
    fireEvent.click(screen.getByTestId('flow-return'))
    expect(useUIStore.getState().uiMode).toBe('expert')
  })

  it('other navigation clears the return; opening the view already shown adds none', () => {
    reset({ uiMode: 'expert', activeSlidePanel: 'issues', activeSection: 'readiness' })
    act(() => openFlowTarget(issues))
    expect(useUIStore.getState().flowReturn).toBeNull()
    act(() => openFlowTarget(prop))
    expect(useUIStore.getState().flowReturn).not.toBeNull()
    act(() => useUIStore.getState().setSlidePanel('results'))
    expect(useUIStore.getState().flowReturn).toBeNull()
  })
})

describe('Grid readiness link return to origin', () => {
  it('Expert: from Results, shows Issues; Back reopens Results', () => {
    reset({ uiMode: 'expert', activeSlidePanel: 'results', activeSection: 'results' })
    render(<FlowReturnButton />)
    act(() => showGridReadiness())
    expect(useUIStore.getState().activeSlidePanel).toBe('issues')
    expect(screen.getByTestId('flow-return')).toBeTruthy()
    fireEvent.click(screen.getByTestId('flow-return'))
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().uiMode).toBe('expert')
  })

  it('Guided: from Results, shows the Site card in place; Back restores Results', () => {
    reset({ uiMode: 'guided', activeSlidePanel: 'results', activeSection: 'results' })
    render(<FlowReturnButton />)
    act(() => showGridReadiness())
    expect(useUIStore.getState().activeSlidePanel).toBe('hubDesign')
    expect(useUIStore.getState().uiMode).toBe('guided')
    expect(useUIStore.getState().gridReadinessRequest).toBe(true)
    fireEvent.click(screen.getByTestId('flow-return'))
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
    expect(useUIStore.getState().uiMode).toBe('guided')
  })
})

describe('Economic assumptions and Profile Studio links', () => {
  it('Guided: Economic assumptions opens in place and stays Guided', () => {
    reset({ uiMode: 'guided', activeSlidePanel: null, activeSection: 'results' })
    act(() => openEconomicAssumptions())
    expect(useUIStore.getState().uiMode).toBe('guided')
  })

  it('Expert: Profile Studio opens with Back to the view it left', () => {
    reset({ uiMode: 'expert', activeSlidePanel: 'results', activeSection: 'results' })
    render(<FlowReturnButton />)
    act(() => openProfileStudio())
    expect(useUIStore.getState().activeSlidePanel).toBe('profiles')
    fireEvent.click(screen.getByTestId('flow-return'))
    expect(useUIStore.getState().activeSlidePanel).toBe('results')
  })
})
