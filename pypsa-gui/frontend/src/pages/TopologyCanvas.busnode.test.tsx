// BusNode must publish the bus name as a DOM attribute so useAssetDrag's
// resolveDrop can recover it with closest('[data-bus-name]').
//
// BusNode renders <Handle> from @xyflow/react, which reads React Flow's
// zustand store — rendering it bare throws. <ReactFlowProvider> supplies the
// store, which is enough to render one node in isolation (measured).
import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { ReactFlowProvider, type NodeProps } from '@xyflow/react'
import { BusNode } from './TopologyCanvas'
import type { Bus } from '../api/types'

const BUS: Bus = {
  name: 'DE0 0', v_nom: 380, carrier: 'AC', x: 6.9, y: 50.9,
  country: 'DE', unit: '', control: 'PQ', sub_network: '',
}

function renderNode(bus: Bus) {
  return render(
    <ReactFlowProvider>
      {/* NodeProps is wider than what BusNode reads; it reads id, data and
          selected only. The cast keeps the test to the real call shape —
          `as unknown as NodeProps` rather than `as never`, which TS refuses
          to spread (TS2698). */}
      <BusNode
        {...({ id: bus.name, data: { bus }, selected: false } as unknown as NodeProps)}
      />
    </ReactFlowProvider>,
  )
}

describe('BusNode DOM identity', () => {
  it('carries the bus name in data-bus-name', () => {
    const { container } = renderNode(BUS)
    const el = container.querySelector('[data-bus-name]')
    expect(el?.getAttribute('data-bus-name')).toBe('DE0 0')
  })

  it('the attribute is findable from a descendant with closest()', () => {
    const { container } = renderNode(BUS)
    const label = Array.from(container.querySelectorAll('span'))
      .find(s => s.textContent === 'DE0 0')
    expect(label?.closest('[data-bus-name]')?.getAttribute('data-bus-name')).toBe('DE0 0')
  })
})
