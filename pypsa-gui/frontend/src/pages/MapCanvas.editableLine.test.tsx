// EditableLine (plan M5 coverage): inserting, moving and removing a
// waypoint, the right-click reset, and the rule that a route edit never
// writes a length unless "Derive lengths from geometry" is on. react-leaflet
// is mocked (MapCanvas.busicon.test.tsx's approach, one level up): each
// Polyline / Marker records its event handlers so the test drives Leaflet's
// gestures directly — there is no map in jsdom to drag on.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'

type Handlers = Record<string, (e: unknown) => void>
interface MarkerRecord { cls: string; position: [number, number]; handlers: Handlers }
const polylines: { handlers: Handlers; positions: unknown[] }[] = []
const markers: MarkerRecord[] = []

vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  TileLayer: () => null,
  Polygon: () => null,
  Tooltip: ({ children }: { children?: ReactNode }) => <div data-testid="tooltip">{children}</div>,
  useMap: () => ({}),
  useMapEvents: () => ({}),
  Polyline: ({ children, eventHandlers, positions }: { children?: ReactNode; eventHandlers: Handlers; positions: unknown[] }) => {
    polylines.push({ handlers: eventHandlers, positions })
    return <div data-testid="polyline">{children}</div>
  },
  Marker: ({ eventHandlers, position, icon }: { eventHandlers: Handlers; position: [number, number]; icon: { options: { className: string } } }) => {
    markers.push({ cls: icon.options.className, position, handlers: eventHandlers })
    return <div data-testid={icon.options.className} />
  },
}))
vi.mock('../api/network', () => ({ networkApi: { lengthsFromGeometry: vi.fn() } }))
vi.mock('../api/mapLayout', async importOriginal => {
  const real = await importOriginal<typeof import('../api/mapLayout')>()
  return { ...real, mapLayoutApi: { getMapLayout: vi.fn(), putMapLayout: vi.fn() } }
})

import { EditableLine } from './MapCanvas'
import { afterRouteEdit, queuedLengthKeys, useMapLayoutStore } from './mapLayoutStore'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'

const A: [number, number] = [53.0, 6.0]
const B: [number, number] = [53.0, 7.0]
const latLng = (lat: number, lng: number) => ({ getLatLng: () => ({ lat, lng }) })
const last = (cls: string) => markers.filter(m => m.cls === cls).at(-1)!
const byCls = (cls: string) => markers.filter(m => m.cls === cls)
const domEvent = () => ({ preventDefault: vi.fn(), stopPropagation: vi.fn(), clientX: 40, clientY: 50 })

function mount(waypoints: [number, number][], onUpdate = vi.fn()) {
  polylines.length = 0; markers.length = 0
  const utils = render(
    <EditableLine id="line:L1" source={A} target={B} waypoints={waypoints} onUpdate={onUpdate} onSelect={() => {}} color="#000" weight={3} />,
  )
  return { ...utils, onUpdate }
}
const hover = () => act(() => { polylines.at(-1)!.handlers.mouseover({}) })

beforeEach(() => {
  useMapLayoutStore.getState().resetForTests()
  useUIStore.setState({ readOnly: false, readOnlyReason: 'writable', currentProject: 'p' })
  vi.mocked(networkApi.lengthsFromGeometry).mockReset()
})
afterEach(() => { vi.clearAllMocks() })

describe('EditableLine', () => {
  it('draws the chord through its waypoints and shows waypoint handles only when it has some or is hovered', () => {
    mount([])
    expect(polylines.at(-1)!.positions).toEqual([A, B])
    expect(screen.queryByTestId('pypsa-line-waypoint')).toBeNull()
    expect(screen.queryByTestId('pypsa-line-add-handle')).toBeNull()
    hover()
    expect(byCls('pypsa-line-add-handle').length).toBeGreaterThan(0)
    polylines.length = 0; markers.length = 0
    mount([[53.5, 6.5]])
    expect(polylines.at(-1)!.positions).toEqual([A, [53.5, 6.5], B])
    expect(screen.getAllByTestId('pypsa-line-waypoint')).toHaveLength(1)
  })

  it('inserting: dragging a mid-segment handle splices a waypoint where it is dropped', () => {
    const { onUpdate } = mount([])
    hover()
    const mid = last('pypsa-line-add-handle')
    expect(mid.position).toEqual([53.0, 6.5])
    act(() => {
      mid.handlers.dragstart({})
      mid.handlers.drag({ target: latLng(53.4, 6.4) })
      mid.handlers.dragend({})
    })
    expect(onUpdate).toHaveBeenCalledWith([[53.4, 6.4]])
  })

  it('inserting on the second segment keeps the existing waypoint first', () => {
    const { onUpdate } = mount([[53.5, 6.5]])
    hover()
    const handles = byCls('pypsa-line-add-handle')
    expect(handles).toHaveLength(2)
    act(() => {
      handles[1].handlers.dragstart({})
      handles[1].handlers.drag({ target: latLng(53.2, 6.8) })
      handles[1].handlers.dragend({})
    })
    expect(onUpdate).toHaveBeenCalledWith([[53.5, 6.5], [53.2, 6.8]])
  })

  it('moving: dragging a waypoint commits its new position on dragend only', () => {
    const { onUpdate } = mount([[53.5, 6.5]])
    const wp = last('pypsa-line-waypoint')
    act(() => { wp.handlers.drag({ target: latLng(53.6, 6.6) }) })
    expect(onUpdate).not.toHaveBeenCalled()
    act(() => { wp.handlers.dragend({}) })
    expect(onUpdate).toHaveBeenCalledWith([[53.6, 6.6]])
  })

  it('removing: double-clicking a waypoint drops it and keeps the map from zooming', () => {
    const { onUpdate } = mount([[53.5, 6.5], [53.2, 6.8]])
    const first = byCls('pypsa-line-waypoint')[0]
    const oe = domEvent()
    act(() => { first.handlers.dblclick({ originalEvent: oe }) })
    expect(onUpdate).toHaveBeenCalledWith([[53.2, 6.8]])
    expect(oe.preventDefault).toHaveBeenCalled()
  })

  it('right-click clear: the context menu\'s "Reset waypoints" empties the route', () => {
    const { onUpdate } = mount([[53.5, 6.5]])
    const oe = domEvent()
    act(() => { polylines.at(-1)!.handlers.contextmenu({ originalEvent: oe }) })
    expect(oe.preventDefault).toHaveBeenCalled()
    expect(screen.getByText('line:L1')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Reset waypoints' }))
    expect(onUpdate).toHaveBeenCalledWith([])
  })

  it('the reset is disabled for a chord with nothing to clear', () => {
    mount([])
    act(() => { polylines.at(-1)!.handlers.contextmenu({ originalEvent: domEvent() }) })
    expect((screen.getByRole('button', { name: 'Reset waypoints' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('the discrepancy badge offers "Use geometry" only when the caller allows it', () => {
    const onUse = vi.fn()
    mount([])
    polylines.length = 0; markers.length = 0
    render(<EditableLine id="line:L2" source={A} target={B} waypoints={[]} onUpdate={() => {}} onSelect={() => {}} color="#000" weight={3} lengthBadge={{ stored: 1, geometry: 66.5, onUseGeometry: onUse }} />)
    fireEvent.click(screen.getByRole('button', { name: 'Use geometry' }))
    expect(onUse).toHaveBeenCalledTimes(1)
    expect(screen.getByText(/1\.0 km stored · 66\.5 km geometry/)).toBeTruthy()
  })
})

describe('a route edit writes no length unless the project setting is on', () => {
  it('EditableLine itself never calls the network: every gesture ends in onUpdate', () => {
    const { onUpdate } = mount([[53.5, 6.5]])
    hover()
    const mid = last('pypsa-line-add-handle')
    act(() => { mid.handlers.dragstart({}); mid.handlers.drag({ target: latLng(53.1, 6.1) }); mid.handlers.dragend({}) })
    act(() => { polylines.at(-1)!.handlers.contextmenu({ originalEvent: domEvent() }) })
    fireEvent.click(screen.getByRole('button', { name: 'Reset waypoints' }))
    expect(onUpdate).toHaveBeenCalledTimes(2)
    expect(networkApi.lengthsFromGeometry).not.toHaveBeenCalled()
  })

  it('afterRouteEdit queues a length only with the setting on, for a project, for a Line or Link', () => {
    afterRouteEdit('p', 'line:L1', false)
    expect(queuedLengthKeys('p')).toEqual([])
    afterRouteEdit(null, 'line:L1', true)
    expect(queuedLengthKeys(null)).toEqual([])
    afterRouteEdit('p', 'tr:T1', true)
    expect(queuedLengthKeys('p')).toEqual([])
    afterRouteEdit('p', 'line:L1', true)
    afterRouteEdit('p', 'link:K1', true)
    expect(queuedLengthKeys('p').sort()).toEqual(['line:L1', 'link:K1'])
    expect(networkApi.lengthsFromGeometry).not.toHaveBeenCalled()   // only after the PUT lands (store test)
  })
})
