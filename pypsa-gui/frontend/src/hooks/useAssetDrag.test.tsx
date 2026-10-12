// The drop resolver's four outcomes (spec D25), plus the gesture's threshold.
//
// jsdom has no document.elementFromPoint, so every test installs one that
// returns the element it wants the pointer to have been over.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render } from '@testing-library/react'
import { useAssetDrag, resolveDrop } from './useAssetDrag'
import { useUIStore } from '../store/uiStore'
import { registerSiteDropTarget, unregisterSiteDropTarget, siteDropTarget, type SiteDropTarget } from '../site3d/dropRegistry'

function stubElementFromPoint(el: Element | null) {
  Object.defineProperty(document, 'elementFromPoint', {
    value: () => el,
    configurable: true,
    writable: true,
  })
}

function mount(className: string, busName?: string): HTMLElement {
  const el = document.createElement('div')
  if (className) el.className = className
  if (busName !== undefined) el.setAttribute('data-bus-name', busName)
  document.body.appendChild(el)
  return el
}

beforeEach(() => {
  document.body.innerHTML = ''
  useUIStore.setState({ creationItem: null })
  ;(window as unknown as { rfInstance?: unknown }).rfInstance = {
    screenToFlowPosition: ({ x, y }: { x: number; y: number }) => ({ x: x + 1, y: y + 1 }),
  }
})

afterEach(() => {
  vi.restoreAllMocks()
  delete (window as unknown as { rfInstance?: unknown }).rfInstance
  useUIStore.setState({ creationItem: null })
})

describe('resolveDrop — the four outcomes', () => {
  it('a bus marker inside the schematic canvas is a bus drop with a position', () => {
    const canvas = mount('react-flow')
    const node = document.createElement('div')
    node.setAttribute('data-bus-name', 'Bus A')
    canvas.appendChild(node)
    stubElementFromPoint(node)

    expect(resolveDrop(10, 20)).toEqual({
      canvas: 'schematic',
      busName: 'Bus A',
      position: { x: 11, y: 21 },
      site: null,
    })
  })

  it('a bus marker inside the map canvas is a bus drop with NO position', () => {
    const canvas = mount('leaflet-container')
    const marker = document.createElement('div')
    marker.setAttribute('data-bus-name', 'Bus B')
    canvas.appendChild(marker)
    stubElementFromPoint(marker)

    expect(resolveDrop(10, 20)).toEqual({
      canvas: 'map',
      busName: 'Bus B',
      position: null,
      site: null,
    })
  })

  it('empty schematic canvas is a schematic drop with no bus', () => {
    stubElementFromPoint(mount('react-flow'))
    expect(resolveDrop(10, 20)).toEqual({
      canvas: 'schematic',
      busName: null,
      position: { x: 11, y: 21 },
      site: null,
    })
  })

  it('empty map canvas is a map drop with no bus and no position', () => {
    stubElementFromPoint(mount('leaflet-container'))
    expect(resolveDrop(10, 20)).toEqual({
      canvas: 'map',
      busName: null,
      position: null,
      site: null,
    })
  })

  it('anything else cancels', () => {
    stubElementFromPoint(mount('some-other-panel'))
    expect(resolveDrop(10, 20)).toEqual({ canvas: null, busName: null, position: null, site: null })
  })

  it('cancels when the pointer is over nothing at all', () => {
    stubElementFromPoint(null)
    expect(resolveDrop(10, 20)).toEqual({ canvas: null, busName: null, position: null, site: null })
  })

  it('a schematic drop with no rfInstance still resolves, with a null position', () => {
    delete (window as unknown as { rfInstance?: unknown }).rfInstance
    stubElementFromPoint(mount('react-flow'))
    expect(resolveDrop(10, 20)).toEqual({
      canvas: 'schematic',
      busName: null,
      position: null,
      site: null,
    })
  })
})

function Harness() {
  const { ghost, beginDrag } = useAssetDrag()
  return (
    <div>
      <div
        data-testid="item"
        role="button"
        onPointerDown={(e) => beginDrag(e, { id: 'thermal', label: 'Thermal' })}
      />
      <span data-testid="ghost">{ghost ? `${ghost.label}@${ghost.x},${ghost.y}` : 'none'}</span>
    </div>
  )
}

describe('useAssetDrag — the gesture', () => {
  it('a click below the 3px threshold opens the form with no drop data', () => {
    const { getByTestId } = render(<Harness />)
    stubElementFromPoint(mount('react-flow'))

    fireEvent.pointerDown(getByTestId('item'), { button: 0, clientX: 5, clientY: 5 })
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 6, clientY: 6 }))
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 6, clientY: 6 }))

    expect(useUIStore.getState().creationItem).toEqual({ id: 'thermal', label: 'Thermal' })
  })

  it('a drag onto a bus sets dropBusName as well as dropPosition', () => {
    const { getByTestId } = render(<Harness />)
    const canvas = mount('react-flow')
    const node = document.createElement('div')
    node.setAttribute('data-bus-name', 'Bus A')
    canvas.appendChild(node)
    stubElementFromPoint(node)

    fireEvent.pointerDown(getByTestId('item'), { button: 0, clientX: 5, clientY: 5 })
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 60, clientY: 70 }))
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 60, clientY: 70 }))

    expect(useUIStore.getState().creationItem).toEqual({
      id: 'thermal',
      label: 'Thermal',
      dropPosition: { x: 61, y: 71 },
      dropBusName: 'Bus A',
    })
  })

  it('a drag onto a map bus sets dropBusName and no dropPosition', () => {
    const { getByTestId } = render(<Harness />)
    const canvas = mount('leaflet-container')
    const marker = document.createElement('div')
    marker.setAttribute('data-bus-name', 'Bus B')
    canvas.appendChild(marker)
    stubElementFromPoint(marker)

    fireEvent.pointerDown(getByTestId('item'), { button: 0, clientX: 5, clientY: 5 })
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 60, clientY: 70 }))
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 60, clientY: 70 }))

    expect(useUIStore.getState().creationItem).toEqual({
      id: 'thermal',
      label: 'Thermal',
      dropBusName: 'Bus B',
    })
  })

  it('a drag released outside both canvases changes nothing', () => {
    const { getByTestId } = render(<Harness />)
    stubElementFromPoint(mount('unrelated'))

    fireEvent.pointerDown(getByTestId('item'), { button: 0, clientX: 5, clientY: 5 })
    window.dispatchEvent(new MouseEvent('pointermove', { clientX: 60, clientY: 70 }))
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 60, clientY: 70 }))

    expect(useUIStore.getState().creationItem).toBe(null)
  })

  it('the ghost follows the pointer while dragging and clears on release', () => {
    const { getByTestId } = render(<Harness />)
    stubElementFromPoint(mount('unrelated'))

    // The ghost is the one assertion in this file against REACT state rather
    // than the zustand store. setGhost is called from a window listener, i.e.
    // outside React's synthetic event system, so the re-render is not flushed
    // by the bare dispatchEvent the other cases use — act() forces it. The
    // source is unchanged by this: the same pattern ran inline in Sidebar.tsx
    // and works in a real browser, where React schedules the commit itself.
    fireEvent.pointerDown(getByTestId('item'), { button: 0, clientX: 5, clientY: 5 })
    act(() => {
      window.dispatchEvent(new MouseEvent('pointermove', { clientX: 60, clientY: 70 }))
    })
    expect(getByTestId('ghost').textContent).toBe('Thermal@60,70')

    act(() => {
      window.dispatchEvent(new MouseEvent('pointerup', { clientX: 60, clientY: 70 }))
    })
    expect(getByTestId('ghost').textContent).toBe('none')
  })

  it('ignores a non-left button', () => {
    const { getByTestId } = render(<Harness />)
    fireEvent.pointerDown(getByTestId('item'), { button: 2, clientX: 5, clientY: 5 })
    window.dispatchEvent(new MouseEvent('pointerup', { clientX: 5, clientY: 5 }))
    expect(useUIStore.getState().creationItem).toBe(null)
  })
})


describe('resolveDrop — the 3D site view (WP4, D16)', () => {
  const target: SiteDropTarget = {
    siteId: 'site_a',
    screenToGround: (x, y) => (y < 0 ? null : { x: x * 2, y: -y }),
    groundToScreen: () => null,
  }
  afterEach(() => { const cur = siteDropTarget(); if (cur) unregisterSiteDropTarget(cur) })

  it('a drop on the registered 3D canvas resolves to the site and the ground point', () => {
    const canvas = mount('site3d-canvas')
    stubElementFromPoint(canvas)
    registerSiteDropTarget(target)
    expect(resolveDrop(10, 20)).toEqual({ canvas: 'site', busName: null, position: null, site: { siteId: 'site_a', ground: { x: 20, y: -20 } } })
  })

  it('cancels when no 3D view has registered, and when the ray misses the ground', () => {
    const canvas = mount('site3d-canvas')
    stubElementFromPoint(canvas)
    expect(resolveDrop(10, 20).canvas).toBeNull()
    registerSiteDropTarget(target)
    expect(resolveDrop(10, -5).canvas).toBeNull()
  })

  it('a site drop while read-only is cancelled with the reason, and nothing is created', () => {
    const canvas = mount('site3d-canvas')
    stubElementFromPoint(canvas)
    registerSiteDropTarget(target)
    useUIStore.setState({ readOnly: true, readOnlyReason: 'locked-by-user' })
    function Harness() {
      const { beginDrag } = useAssetDrag()
      return <button onPointerDown={e => beginDrag(e, { id: 'battery', label: 'Battery' })}>drag</button>
    }
    const { getByText } = render(<Harness />)
    act(() => { fireEvent.pointerDown(getByText('drag'), { button: 0, clientX: 0, clientY: 0 }) })
    act(() => { fireEvent.pointerMove(window, { clientX: 30, clientY: 40 }) })
    act(() => { fireEvent.pointerUp(window, { clientX: 30, clientY: 40 }) })
    expect(useUIStore.getState().creationItem).toBeNull()
    useUIStore.setState({ readOnly: false, readOnlyReason: 'writable' })
  })

  it('the gesture forwards dropSite into the creation request', () => {
    const canvas = mount('site3d-canvas')
    stubElementFromPoint(canvas)
    registerSiteDropTarget(target)
    function Harness() {
      const { beginDrag } = useAssetDrag()
      return <button onPointerDown={e => beginDrag(e, { id: 'battery', label: 'Battery' })}>drag</button>
    }
    const { getByText } = render(<Harness />)
    const btn = getByText('drag')
    act(() => { fireEvent.pointerDown(btn, { button: 0, clientX: 0, clientY: 0 }) })
    act(() => { fireEvent.pointerMove(window, { clientX: 30, clientY: 40 }) })
    act(() => { fireEvent.pointerUp(window, { clientX: 30, clientY: 40 }) })
    expect(useUIStore.getState().creationItem).toEqual({ id: 'battery', label: 'Battery', dropSite: { siteId: 'site_a', ground: { x: 60, y: -40 } } })
  })
})
