// template-e2e 02, owner decision (a): the connection limit is the import links' capacity. The pack form
// the Site card's readiness and the Goal card's study send takes the import cap the server reads from
// the network (`flow_service.import_capacity`, the flow's connection row), never a client-side sum and
// never the template's value (GK B C3, C6; backend/tests/test_flow_connection_limit.py).
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { getProjectFlow, type FlowConnectionLimit, type ProjectFlow } from '../../api/flow'
import { useUIStore } from '../../store/uiStore'
import { buildEhStudyBody } from '../results/EhReferenceDesignPanel'
import { DC_TEMPLATE } from './testFixtures'
import { importBlock, templateForm, useTemplateForm } from './useHubData'

vi.mock('../../api/flow', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/flow')>()
  return { ...actual, getProjectFlow: vi.fn() }
})

afterEach(() => { cleanup(); vi.clearAllMocks() })
function wrap({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}
function flowWith(limit: FlowConnectionLimit) {
  return { sections: [{ section: 'connection', connection_limit: limit }] } as unknown as ProjectFlow
}
function render() {
  useUIStore.setState({ currentProject: 'Demo' })
  return renderHook(() => useTemplateForm(DC_TEMPLATE), { wrapper: wrap }).result
}

describe('useTemplateForm', () => {
  it("takes the weak-grid import cap from the server's connection limit, not the template", async () => {
    vi.mocked(getProjectFlow).mockResolvedValue(flowWith({ mw: 45, refusal: null, links: ['grid_import'] }))
    const result = render()
    expect(templateForm(DC_TEMPLATE).importMw).toBe('40')
    await waitFor(() => expect(result.current.importMw).toBe('45'))
    expect(importBlock('weak_flexible', result.current)).toBeNull()
    const body = buildEhStudyBody('weak_flexible', result.current).body
    expect(body?.pack_overrides?.import_p_nom_mw).toBe(45)
  })

  it('waits, sending no cap, until the flow is read', () => {
    vi.mocked(getProjectFlow).mockReturnValue(new Promise(() => {}))
    const result = render()
    expect(result.current.importMw).toBe('')
    expect(importBlock('weak_flexible', result.current)).toBe('')
    expect(importBlock('strong_grid', result.current)).toBeNull()
  })

  it("refuses with the server's words where the network fixes no limit, never the template's value", async () => {
    const refusal = 'The connection limit is 0 MW: set one or choose another site type.'
    vi.mocked(getProjectFlow).mockResolvedValue(flowWith({ mw: null, refusal, links: [] }))
    const result = render()
    await waitFor(() => expect(result.current.importRefusal).toBe(refusal))
    expect(result.current.importMw).toBe('')
    expect(importBlock('weak_flexible', result.current)).toBe(refusal)
  })
})
