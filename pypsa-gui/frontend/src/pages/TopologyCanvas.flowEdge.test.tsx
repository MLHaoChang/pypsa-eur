// Plan 1 A3: with the overlay on, an edge whose component has flow carries
// the moving-dash path with its speed-bucket class and the direction variable;
// under prefers-reduced-motion it carries neither, and keeps the loading-band
// colour. Rendered like the BusNode test: <ReactFlowProvider> for
// useReactFlow, the results context hand-built. (The arrow chip renders
// through React Flow's EdgeLabelRenderer portal, which has no target outside
// a mounted <ReactFlow>, so the chip is not asserted here.)
import { afterEach, describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { ReactFlowProvider, type EdgeProps } from '@xyflow/react'
import { EditableEdge } from './TopologyCanvas'
import { CanvasResultsContext, loadingColor, type OverlayData } from '../components/CanvasResultsContext'

type Flow = { p0: number; loadingPct: number; sNom: number }
const results = (byLine: Record<string, Flow>, byTransformer: Record<string, Flow> = {}): OverlayData => ({
  enabled: true, idx: 0, iso: '', kind: 'p',
  byBus: new Map(), byLink: new Map(), byAssetGroup: new Map(), byAssetGroupSoC: new Map(), byAssetGroupCapacity: new Map(), byAsset: new Map(),
  byLine: new Map(Object.entries(byLine).map(([k, v]) => [k, { ...v, q0: null }])),
  byTransformer: new Map(Object.entries(byTransformer).map(([k, v]) => [k, { ...v, q0: null }])),
} as OverlayData)

function renderEdge(id: string, type: 'line' | 'transformer' | 'asset', value: OverlayData | null) {
  const props = {
    id, sourceX: 0, sourceY: 0, targetX: 100, targetY: 0, selected: false,
    data: { type, color: '#374151', s_nom: 100, waypoints: [], history: [[]] },
  } as unknown as EdgeProps
  const edge = <svg><EditableEdge {...props} /></svg>
  return render(
    <ReactFlowProvider>
      {value ? <CanvasResultsContext.Provider value={value}>{edge}</CanvasResultsContext.Provider> : edge}
    </ReactFlowProvider>,
  )
}

const movingDash = (c: HTMLElement) => c.querySelector('path.canvas-flow') as SVGPathElement | null

const realMatchMedia = window.matchMedia
function prefersReducedMotion(on: boolean) {
  window.matchMedia = (q: string) => ({
    matches: on && q.includes('prefers-reduced-motion'), media: q, onchange: null,
    addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent() { return false },
  }) as unknown as MediaQueryList
}
afterEach(() => { window.matchMedia = realMatchMedia })

describe('EditableEdge flow animation', () => {
  it('an edge with flow carries the moving dash, its speed bucket and the forward direction variable', () => {
    const { container } = renderEdge('line-L1', 'line', results({ L1: { p0: 60, loadingPct: 60, sNom: 100 } }))
    const dash = movingDash(container)
    expect(dash).not.toBeNull()
    expect(dash!.classList.contains('flow-b3')).toBe(true)                       // 60 % of rating → bucket 3
    expect(dash!.style.getPropertyValue('--flow-cycle')).toBe('-16px')          // source → target
    expect(dash!.getAttribute('data-flow-bucket')).toBe('3')
  })

  it('a reverse flow moves the other way, at the speed of its own share', () => {
    const { container } = renderEdge('line-L1', 'line', results({ L1: { p0: -95, loadingPct: 95, sNom: 100 } }))
    const dash = movingDash(container)!
    expect(dash.classList.contains('flow-b5')).toBe(true)
    expect(dash.style.getPropertyValue('--flow-cycle')).toBe('16px')
  })

  it('no flow, no overlay, or an asset edge: no moving dash', () => {
    expect(movingDash(renderEdge('line-L1', 'line', results({ L1: { p0: 0, loadingPct: 0, sNom: 100 } })).container)).toBeNull()
    expect(movingDash(renderEdge('line-L1', 'line', null).container)).toBeNull()
    expect(movingDash(renderEdge('assetedge-x', 'asset', results({ x: { p0: 50, loadingPct: 50, sNom: 100 } })).container)).toBeNull()
  })

  it('under prefers-reduced-motion the dash does not move, and the colour band stays', () => {
    prefersReducedMotion(true)
    const { container } = renderEdge('line-L1', 'line', results({ L1: { p0: 60, loadingPct: 60, sNom: 100 } }))
    expect(movingDash(container)).toBeNull()
    const paths = Array.from(container.querySelectorAll('path'))
    expect(paths.some(p => (p.getAttribute('stroke') ?? '').startsWith(loadingColor(60)))).toBe(true)
  })

  it('a transformer edge takes its flow from the transformer map, not the Lines map (plan 1, the known bug)', () => {
    const { container } = renderEdge('tr-T1', 'transformer', results({ T1: { p0: 999, loadingPct: 99, sNom: 100 } }, { T1: { p0: 30, loadingPct: 30, sNom: 100 } }))
    const dash = movingDash(container)!
    expect(dash).not.toBeNull()
    expect(dash.classList.contains('flow-b2')).toBe(true)
    const paths = Array.from(container.querySelectorAll('path'))
    expect(paths.some(p => (p.getAttribute('stroke') ?? '').startsWith(loadingColor(30)))).toBe(true)
  })

  it('a transformer with no row in the transformer map shows no flow, whatever a same-named line carries', () => {
    const { container } = renderEdge('tr-T1', 'transformer', results({ T1: { p0: 999, loadingPct: 99, sNom: 100 } }))
    expect(movingDash(container)).toBeNull()
  })
})
