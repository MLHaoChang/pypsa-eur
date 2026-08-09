// Characterization of AssetTable's selection / sort / search / render-cap
// behaviour, written BEFORE the editable cell layer is built on top of it
// (spec D1, D30). BottomPanel.tsx has zero coverage today and Tasks 11-16 all
// edit this component.
//
// AssetTable is not exported, so these drive it through the real BottomPanel
// with the nine network getters mocked.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getBuses: vi.fn(), getLines: vi.fn(), getLinks: vi.fn(),
      getTransformers: vi.fn(), getGenerators: vi.fn(), getLoads: vi.fn(),
      getStorageUnits: vi.fn(), getStores: vi.fn(), getCarriers: vi.fn(),
      bulkUpdate: vi.fn(), getCatalog: vi.fn(), listTimeseries: vi.fn(),
    },
  }
})

import { networkApi } from '../api/network'
import type { CatalogAttribute } from '../api/types'
import BottomPanel from './BottomPanel'

function catalogAttr(
  over: Partial<CatalogAttribute> & { name: string },
): CatalogAttribute {
  return {
    status: 'Input (optional)', varying: false, dtype: 'float64', unit: null,
    description: null, type: 'float', default: 0, default_text: '0.0', ...over,
  }
}

/** The Bus catalog, matching what the real endpoint reports for these columns. */
const BUS_CATALOG: CatalogAttribute[] = [
  catalogAttr({ name: 'name', dtype: 'object', status: 'Input (required)' }),
  catalogAttr({ name: 'v_nom', unit: 'kV' }),
  catalogAttr({ name: 'carrier', dtype: 'object', type: 'string' }),
  // Output in PyPSA, made editable by D13's override list.
  catalogAttr({ name: 'control', dtype: 'object', status: 'Output', type: 'string' }),
  catalogAttr({ name: 'x', unit: 'deg' }),
  catalogAttr({ name: 'y', unit: 'deg' }),
  catalogAttr({ name: 'sub_network', dtype: 'object', status: 'Output' }),
  catalogAttr({ name: 'country', dtype: 'object', type: 'string' }),
  catalogAttr({ name: 'unit', dtype: 'object', type: 'string' }),
]

/** n buses named "B0".."B(n-1)" with a descending v_nom so sort is observable. */
function buses(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    name: `B${i}`, v_nom: 380 - i, carrier: 'AC', x: 0, y: 0,
    country: '', unit: '', control: 'PQ', sub_network: '',
  }))
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <BottomPanel />
    </QueryClientProvider>,
  )
}

/** Every data row's checkbox, in render order. The header's lives in <thead>. */
function rowCheckboxes(): HTMLInputElement[] {
  return Array.from(
    document.querySelectorAll<HTMLInputElement>('tbody input[type="checkbox"]'),
  )
}

/** The header select-all checkbox. */
function headerCheckbox(): HTMLInputElement {
  return document.querySelector<HTMLInputElement>('thead input[type="checkbox"]')!
}

/** Row names in render order, read from the first data column. */
function renderedNames(): string[] {
  return Array.from(document.querySelectorAll('tbody tr'))
    .map(tr => tr.querySelectorAll('td')[1]?.textContent ?? '')
}

beforeEach(() => {
  const api = vi.mocked(networkApi)
  api.getBuses.mockReset().mockResolvedValue(buses(5) as never)
  for (const fn of [api.getLines, api.getLinks, api.getTransformers,
    api.getGenerators, api.getLoads, api.getStorageUnits, api.getStores,
    api.getCarriers]) {
    fn.mockReset().mockResolvedValue([] as never)
  }
  api.bulkUpdate.mockReset()
  api.listTimeseries.mockReset().mockResolvedValue([] as never)
  api.getCatalog.mockReset().mockImplementation(async (component: string) =>
    ({ component, attributes: component === 'Bus' ? BUS_CATALOG : [] }) as never)
  useUIStore.setState({ currentProject: 'Demo', selectedComponent: null })
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null, selectedComponent: null })
})

describe('AssetTable selection — behaviour as of e8614a35', () => {
  it('a checkbox click selects exactly that row', async () => {
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click(rowCheckboxes()[1])
    expect(screen.getByText(/1 selected/)).toBeTruthy()
  })

  it('shift-click selects the inclusive range from the previous anchor', async () => {
    renderPanel()
    await screen.findByText('B0')
    const boxes = rowCheckboxes()
    await userEvent.click(boxes[0])
    fireEvent.click(boxes[3], { shiftKey: true })
    // Rows 0,1,2,3 inclusive.
    expect(screen.getByText(/4 selected/)).toBeTruthy()
  })

  it('select-all covers every row', async () => {
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click(headerCheckbox())
    expect(screen.getByText(/5 selected/)).toBeTruthy()
  })

  it('a second select-all click clears the selection', async () => {
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click(headerCheckbox())
    await userEvent.click(headerCheckbox())
    expect(screen.queryByText(/\d+ selected/)).toBeNull()
  })
})

describe('AssetTable search and sort — behaviour as of e8614a35', () => {
  it('search filters rows by a case-insensitive name substring', async () => {
    renderPanel()
    await screen.findByText('B0')
    const search = document.querySelector(
      'input[placeholder*="earch" i]',
    ) as HTMLInputElement
    await userEvent.type(search, 'b3')
    expect(screen.getByText('B3')).toBeTruthy()
    expect(screen.queryByText('B0')).toBeNull()
  })

  it('clicking a column header sorts, and clicking again reverses', async () => {
    renderPanel()
    await screen.findByText('B0')
    // The header renders COL_LABELS['v_nom'], not the raw column name.
    const header = screen.getByText('V nom (kV)')
    await userEvent.click(header)
    const asc = renderedNames()
    await userEvent.click(header)
    const desc = renderedNames()
    expect(desc).toEqual([...asc].reverse())
  })
})

describe('AssetTable render cap — behaviour as of e8614a35', () => {
  it('renders every row when the count is at or below the 1000 cap', async () => {
    vi.mocked(networkApi).getBuses.mockResolvedValue(buses(50) as never)
    renderPanel()
    await screen.findByText('B0')
    expect(document.querySelectorAll('tbody tr').length).toBe(50)
    expect(screen.queryByText(/use search\/sort to drill down/)).toBeNull()
  })

  it('caps the DOM at 1000 rows and shows the truncation notice', async () => {
    vi.mocked(networkApi).getBuses.mockResolvedValue(buses(1200) as never)
    renderPanel()
    await screen.findByText('B0')
    expect(document.querySelectorAll('tbody tr').length).toBe(1000)
    expect(screen.getByText(/Showing 1000 of 1200/)).toBeTruthy()
  })

  it('select-all past the cap selects the UNCAPPED row count', async () => {
    // This is the behaviour decision 5 leans on: paste must reach rows the DOM
    // never rendered. If this ever reports 1000, the cap has leaked into
    // selection and the paste path is silently truncated.
    vi.mocked(networkApi).getBuses.mockResolvedValue(buses(1200) as never)
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click(headerCheckbox())
    expect(screen.getByText(/1200 selected/)).toBeTruthy()
  })
})

describe('AssetTable active cell — added by Task 11', () => {
  it('clicking a cell makes it the only tabbable one (D19 roving tabindex)', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = document.querySelector('tbody td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    expect(document.querySelectorAll('tbody td[tabindex="0"]').length).toBe(1)
  })

  it('ArrowDown moves the active cell one row down', async () => {
    renderPanel()
    await screen.findByText('B0')
    const first = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(first)
    fireEvent.keyDown(first, { key: 'ArrowDown' })
    const active = document.querySelector('td[tabindex="0"]') as HTMLElement
    expect(active.dataset.row).toBe('B1')
    expect(active.dataset.col).toBe('v_nom')
  })

  it('ArrowRight moves to the next visible column', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = document.querySelector('tbody tr td[data-col="name"]') as HTMLElement
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'ArrowRight' })
    const active = document.querySelector('td[tabindex="0"]') as HTMLElement
    expect(active.dataset.col).not.toBe('name')
  })

  it('does not move past the last row', async () => {
    renderPanel()
    await screen.findByText('B4')
    const last = document.querySelector(
      'tbody tr:last-child td[data-col="v_nom"]',
    ) as HTMLElement
    await userEvent.click(last)
    fireEvent.keyDown(last, { key: 'ArrowDown' })
    expect((document.querySelector('td[tabindex="0"]') as HTMLElement).dataset.row).toBe('B4')
  })

  it('Escape clears the active cell', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Escape' })
    expect(document.querySelectorAll('td[tabindex="0"]').length).toBe(0)
  })
})

describe('AssetTable headers — D15', () => {
  it('renders the curated COL_LABELS entry where one exists', async () => {
    renderPanel()
    // COL_LABELS maps v_nom → 'V nom (kV)' (BottomPanel.tsx:46-60).
    expect(await screen.findByText('V nom (kV)')).toBeTruthy()
  })
})

describe('availableCols stays derived from the data — D17', () => {
  it('does not offer a catalog attribute that no row carries', async () => {
    // The catalog ANNOTATES columns; it does not add them. A column absent
    // from the DataFrame is 400-rejected by _bulk ("has no column(s)"), so
    // offering it would produce a guaranteed failure. This is the test that
    // fails if someone later drives the column list from the catalog instead
    // of from the data.
    renderPanel()
    await screen.findByText('B0')
    // `v_mag_pu_set` is a real PyPSA Bus attribute; the mocked rows omit it.
    expect(screen.queryByText('v_mag_pu_set')).toBeNull()
  })

  it('keeps `name` pinned visible', async () => {
    renderPanel()
    await screen.findByText('B0')
    expect(document.querySelector('tbody td[data-col="name"]')).toBeTruthy()
  })
})

describe('AssetTable cell editors — D4', () => {
  /** Click the cell then press Enter to open its editor. */
  async function openEditor(col: string) {
    const cell = document.querySelector(`tbody tr td[data-col="${col}"]`) as HTMLElement
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    return cell
  }

  it('Enter on an editable numeric cell opens a type="text" input', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = await openEditor('v_nom')
    const input = cell.querySelector('input') as HTMLInputElement
    // type="text", not "number": <input type="number"> cannot hold `inf`.
    expect(input.getAttribute('type')).toBe('text')
  })

  it('mounts at most one editor at a time', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cells = document.querySelectorAll('tbody td[data-col="v_nom"]')
    await userEvent.click(cells[0] as HTMLElement)
    fireEvent.keyDown(cells[0] as HTMLElement, { key: 'Enter' })
    await userEvent.click(cells[1] as HTMLElement)
    fireEvent.keyDown(cells[1] as HTMLElement, { key: 'Enter' })
    expect(document.querySelectorAll('tbody input[type="text"]').length).toBe(1)
  })

  it('Escape discards the draft and closes the editor', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = await openEditor('v_nom')
    const input = cell.querySelector('input') as HTMLInputElement
    // fireEvent.change rather than userEvent.type: typing round-trips through
    // focus, and this input commits on blur, so user-event's focus juggling
    // would close and reopen the editor and leave `input` detached. The
    // behaviour under test is Escape, not typing.
    fireEvent.change(input, { target: { value: '999' } })
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(cell.querySelector('input')).toBeNull()
    expect(vi.mocked(networkApi).bulkUpdate).not.toHaveBeenCalled()
  })

  it('committing unchanged text issues no request (criterion 2)', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = await openEditor('v_nom')
    const input = cell.querySelector('input') as HTMLInputElement
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(vi.mocked(networkApi).bulkUpdate).not.toHaveBeenCalled()
  })

  it('a read-only Output cell does not open an editor', async () => {
    renderPanel()
    await screen.findByText('B0')
    // sub_network is Output on Bus, so the cell must refuse to open.
    const cell = document.querySelector('tbody tr td[data-col="sub_network"]') as HTMLElement
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    expect(cell.querySelector('input')).toBeNull()
  })

  it('the `name` cell never opens an editor', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = document.querySelector('tbody tr td[data-col="name"]') as HTMLElement
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    expect(cell.querySelector('input')).toBeNull()
  })

  it('a closed-set cell offers exactly PQ, PV and Slack (criterion 22)', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = await openEditor('control')
    const select = cell.querySelector('select') as HTMLSelectElement
    expect(Array.from(select.options).map(o => o.value)).toEqual(['PQ', 'PV', 'Slack'])
  })
})
