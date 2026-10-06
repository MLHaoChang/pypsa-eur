// Plan 1 A2: an individual asset node shows the type icon, the component name
// and the sizing figure; with the overlay on, the per-component dispatch
// (with direction), the SoC gauge for storage and the period-effective
// capacity in place of the installed figure. Rendered like the BusNode test:
// <ReactFlowProvider> for the Handles, the results context hand-built.
import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { ReactFlowProvider, type NodeProps } from '@xyflow/react'
import { AssetNode } from './TopologyCanvas'
import { CanvasResultsContext, type AssetOverlay, type OverlayData } from '../components/CanvasResultsContext'
import type { AssetNodeDescriptor } from './topologyAssets'

const BESS: AssetNodeDescriptor = {
  id: 'asset-StorageUnit:BESS', cls: 'StorageUnit', name: 'BESS', bus: 'B', buses: [{ bus: 'B', port: 'bus' }],
  typeId: 'bess', typeLabel: 'Battery storage', color: '#7c3aed', icon: 'battery', carrier: 'battery',
  sizing: { amount: 40, unit: 'MWh' }, maxHours: 4,
}

const overlay = (byAsset: Record<string, AssetOverlay>): OverlayData => ({
  enabled: true, idx: 0, iso: '', kind: 'p',
  byBus: new Map(), byLine: new Map(), byLink: new Map(), byAssetGroup: new Map(), byAssetGroupSoC: new Map(),
  byAssetGroupCapacity: new Map(), byAsset: new Map(Object.entries(byAsset)),
} as OverlayData)

function renderNode(d: AssetNodeDescriptor, results?: OverlayData) {
  const node = <AssetNode {...({ id: d.id, data: d, selected: false } as unknown as NodeProps)} />
  return render(
    <ReactFlowProvider>
      {results ? <CanvasResultsContext.Provider value={results}>{node}</CanvasResultsContext.Provider> : node}
    </ReactFlowProvider>,
  )
}

describe('AssetNode', () => {
  it('shows the name, the sizing figure and the type as its tooltip', () => {
    const { container, getByText } = renderNode(BESS)
    getByText('BESS')
    expect(container.querySelector('[data-testid="asset-figure"]')?.textContent).toBe('40 MWh')
    expect(container.querySelector('[data-asset-node]')?.getAttribute('title')).toContain('Battery storage')
    expect(container.querySelector('svg')).not.toBeNull()   // the type icon
  })

  it('with the overlay on: dispatch with direction, SoC gauge, effective capacity in the sizing unit', () => {
    const { container, getByText } = renderNode(BESS, overlay({
      'StorageUnit:BESS': { cls: 'StorageUnit', dispatchMW: -2.5, socPct: 75, capacity: 15 },
    }))
    getByText('▼ 2.5 MW')                      // charging
    getByText('SoC 75 %')
    expect(container.querySelector('[data-testid="asset-figure"]')?.textContent).toBe('60 MWh')   // 15 MW × 4 h
  })

  it('a generator injects (▲); a load consumes (▼)', () => {
    const pv: AssetNodeDescriptor = { ...BESS, id: 'asset-Generator:PV', cls: 'Generator', name: 'PV', typeId: 'pv', icon: 'sun', sizing: { amount: 12, unit: 'MW' }, maxHours: undefined }
    renderNode(pv, overlay({ 'Generator:PV': { cls: 'Generator', dispatchMW: 7.5, socPct: null, capacity: 12 } })).getByText('▲ 7.5 MW')
    const hall: AssetNodeDescriptor = { ...pv, id: 'asset-Load:Hall', cls: 'Load', name: 'Hall', typeId: 'load', icon: 'load_elec', sizing: { amount: 8, unit: 'MW' } }
    renderNode(hall, overlay({ 'Load:Hall': { cls: 'Load', dispatchMW: 4, socPct: null, capacity: null } })).getByText('▼ 4.0 MW')
  })
})
