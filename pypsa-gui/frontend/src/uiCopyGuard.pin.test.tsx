// RAW-NAMES-1 pin (expert-catalogue spec §7.2, 02c): a model name reaches the
// user in two places only, the Parameter guide's "In model files" line
// (data-testid guide-model-name) and the import mapping dialog's column table
// (data-testid table-import-columns), which shows both names by design. A
// fixture model name rendered anywhere else in either component fails.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import GuideDrawer from './catalogue/GuideDrawer'
import { useGuideStore } from './catalogue/guideStore'
import * as catalogueApi from './catalogue/api'
import { CATALOGUE } from './catalogue/guideFixture'
import EquipmentTableImport from './components/EquipmentTableImport'
import { useUIStore } from './store/uiStore'

vi.mock('./api/equipmentTable', () => ({
  equipmentTableApi: {
    preview: vi.fn(async () => ({
      table: { source: { upload_id: 'u1', filename: 'lines.csv', sha256: 'a'.repeat(64), sheet: '(csv)', sheets: ['(csv)'] },
        headers: ['name', 'rating'], row_count: 1, rows: [{ row: 2, cells: { name: 'L1', rating: '500' } }] },
      fields: [{ field: 's_nom', label: 'Rated power', units: ['MVA'], currency: null }],
    })),
    apply: vi.fn(),
  },
}))
vi.mock('./api/uploads', () => ({
  uploadFile: vi.fn(),
  listUploads: vi.fn(async () => [{ file_id: 'u1', filename: 'lines.csv', kind: 'user_upload' }]),
}))

/** Every text node and copy attribute holding `name` that sits outside `allowed`. */
function strays(root: HTMLElement, name: string, allowed: string): string[] {
  const re = new RegExp(`(?<![A-Za-z0-9_])${name}(?![A-Za-z0-9_])`)
  const out: string[] = []
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (re.test(n.textContent ?? '') && !n.parentElement?.closest(`[data-testid="${allowed}"]`)) out.push(n.textContent!)
  }
  for (const el of Array.from(root.querySelectorAll('*'))) {
    for (const a of ['title', 'aria-label', 'aria-description', 'placeholder', 'alt']) {
      const v = el.getAttribute(a)
      if (v && re.test(v)) out.push(`${a}="${v}"`)
    }
  }
  return out
}

beforeEach(() => {
  vi.spyOn(catalogueApi, 'fetchCatalogue').mockResolvedValue(CATALOGUE)
  useGuideStore.setState({ view: null, history: [], opener: null })
  useUIStore.setState({ currentProject: 'P', uiMode: 'expert', assistantDockOpen: false })
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); useGuideStore.setState({ view: null, history: [], opener: null }) })

describe('the two places a model name shows', () => {
  it('the guide shows it only on its "In model files" line', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={qc}><GuideDrawer /></QueryClientProvider>)
    act(() => useGuideStore.getState().openGuide('generator.capacity'))
    await screen.findByTestId('guide-entry')
    expect(screen.getByTestId('guide-model-name').textContent).toBe('p_nom')
    expect(strays(document.body, 'p_nom', 'guide-model-name')).toEqual([])
  })

  it('the import mapping dialog shows it only in its column table', async () => {
    render(<EquipmentTableImport componentClass="Line" assetsLabel="lines" onClose={() => {}} onApplied={() => {}} />)
    await userEvent.selectOptions(await screen.findByTestId('table-import-upload'), 'u1')
    const table = await screen.findByTestId('table-import-columns')
    expect(table.textContent).toContain('s_nom')
    expect(strays(document.body, 's_nom', 'table-import-columns')).toEqual([])
  })
})
