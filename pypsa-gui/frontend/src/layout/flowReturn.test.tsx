import { beforeEach, describe, expect, it } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { useUIStore } from '../store/uiStore'
import { FlowReturnButton } from './FlowReturnButton'
import { openFlowTarget, openFlowTargetInPlace, returnToOrigin } from './flowNavigation'

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
    expect(screen.getByTestId('flow-return').textContent).toContain('Back to Readiness → run')
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

  it('Guided: the jump replaces the drawer; Back reinstates the previous one', () => {
    reset({ uiMode: 'guided', activeSlidePanel: 'hubDesign', activeSection: 'readiness', fieldDrawer: issues })
    render(<FlowReturnButton />)
    act(() => openFlowTargetInPlace(prop))
    expect(useUIStore.getState().fieldDrawer).toEqual(prop)
    expect(screen.getByTestId('flow-return').textContent).toContain('Back to Readiness → run')
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
