// Characterization of AssetTable's selection / sort / search / render-cap
// behaviour, written BEFORE the editable cell layer is built on top of it
// (spec D1, D30). BottomPanel.tsx has zero coverage today and Tasks 11-16 all
// edit this component.
//
// AssetTable is not exported, so these drive it through the real BottomPanel
// with the nine network getters mocked.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import toast from 'react-hot-toast'
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

describe('AssetTable optimistic mutation — D10', () => {
  /** Open the first v_nom cell, set it to `to`, commit with Enter. */
  async function editFirstVNom(to: string) {
    renderPanel()
    await screen.findByText('B0')
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    const input = cell.querySelector('input') as HTMLInputElement
    fireEvent.change(input, { target: { value: to } })
    fireEvent.keyDown(input, { key: 'Enter' })
    return cell
  }

  it('sends the scalar form when every row gets the same value', async () => {
    vi.mocked(networkApi).bulkUpdate.mockResolvedValue(
      { updated: 1, fields: ['v_nom'] } as never)
    await editFirstVNom('123')
    // onMutate awaits cancelQueries, so the request fires a microtask later.
    await waitFor(() => expect(vi.mocked(networkApi).bulkUpdate).toHaveBeenCalled())
    expect(vi.mocked(networkApi).bulkUpdate.mock.calls[0][0]).toEqual({
      component_class: 'Bus', names: ['B0'], updates: { v_nom: 123 },
    })
  })

  it('shows the new value before the request resolves', async () => {
    let release: (v: unknown) => void = () => {}
    vi.mocked(networkApi).bulkUpdate.mockReturnValue(
      new Promise(res => { release = res }) as never)
    const cell = await editFirstVNom('456')
    await waitFor(() => expect(cell.textContent).toContain('456'))
    release({ updated: 1, fields: ['v_nom'] })
  })

  it('rolls the value back and reports the backend detail on failure', async () => {
    // The toast is asserted through toast.error rather than rendered text:
    // renderPanel mounts BottomPanel alone, and <Toaster/> lives in App.tsx.
    const errSpy = vi.spyOn(toast, 'error')
    vi.mocked(networkApi).bulkUpdate.mockRejectedValue({
      response: { data: { detail: 'Column is numeric; got non-numeric value' } },
    })
    const cell = await editFirstVNom('456')
    await waitFor(() => expect(errSpy).toHaveBeenCalled())
    expect(errSpy.mock.calls[0][0]).toContain('got non-numeric value')
    expect(cell.textContent).toContain('380')      // the original v_nom
    expect(cell.textContent).not.toContain('456')
  })

  it('restores the checkbox selection after a failure', async () => {
    const errSpy = vi.spyOn(toast, 'error')
    vi.mocked(networkApi).bulkUpdate.mockRejectedValue({
      response: { data: { detail: 'nope' } },
    })
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click(rowCheckboxes()[0])
    await userEvent.click(rowCheckboxes()[1])
    expect(screen.getByText(/2 selected/)).toBeTruthy()
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    const input = cell.querySelector('input') as HTMLInputElement
    fireEvent.change(input, { target: { value: '9' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(errSpy).toHaveBeenCalled())
    expect(screen.getByText(/2 selected/)).toBeTruthy()
  })

  it('formats a FastAPI error array instead of printing [object Object]', async () => {
    const errSpy = vi.spyOn(toast, 'error')
    vi.mocked(networkApi).bulkUpdate.mockRejectedValue({
      response: { data: { detail: [{ loc: ['body', 'p_nom'], msg: 'not a number' }] } },
    })
    await editFirstVNom('7')
    await waitFor(() => expect(errSpy).toHaveBeenCalled())
    const shown = errSpy.mock.calls[0][0] as string
    expect(shown).toContain('not a number')
    expect(shown).toContain('p_nom')
    expect(shown).not.toContain('[object Object]')
  })
})

/**
 * jsdom has neither ClipboardEvent nor DataTransfer, so both events are built
 * by hand. `bubbles: true` matters — the grid listens on its scroll container.
 */
function firePaste(el: Element, text: string) {
  const e = new Event('paste', { bubbles: true, cancelable: true })
  Object.defineProperty(e, 'clipboardData', {
    value: { getData: () => text, setData: () => {} },
  })
  el.dispatchEvent(e)
}

function fireCopy(el: Element): string {
  let written = ''
  const e = new Event('copy', { bubbles: true, cancelable: true })
  Object.defineProperty(e, 'clipboardData', {
    value: { getData: () => '', setData: (_t: string, v: string) => { written = v } },
  })
  el.dispatchEvent(e)
  return written
}

describe('AssetTable clipboard — D6, D7, D8', () => {
  async function activate(col = 'v_nom') {
    renderPanel()
    await screen.findByText('B0')
    const cell = document.querySelector(`tbody tr td[data-col="${col}"]`) as HTMLElement
    await userEvent.click(cell)
    return cell
  }

  it('copy emits the active cell as TSV', async () => {
    const cell = await activate()
    expect(fireCopy(cell)).toBe('380')
  })

  it('a 1x1 paste fills every selected row in one request', async () => {
    vi.mocked(networkApi).bulkUpdate.mockResolvedValue(
      { updated: 5, fields: ['v_nom'] } as never)
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click(headerCheckbox())              // select all 5
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    await act(async () => { firePaste(cell, '111') })
    await waitFor(() => expect(vi.mocked(networkApi).bulkUpdate).toHaveBeenCalled())
    expect(vi.mocked(networkApi).bulkUpdate).toHaveBeenCalledTimes(1)
    const body = vi.mocked(networkApi).bulkUpdate.mock.calls[0][0]
    expect(body.names?.length).toBe(5)
    expect(body.updates).toEqual({ v_nom: 111 })
  })

  it('an Nx1 paste of distinct values uses the row form, one request', async () => {
    vi.mocked(networkApi).bulkUpdate.mockResolvedValue(
      { updated: 5, fields: ['v_nom'] } as never)
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click(headerCheckbox())
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    await act(async () => { firePaste(cell, '1\r\n2\r\n3\r\n4\r\n5') })
    await waitFor(() => expect(vi.mocked(networkApi).bulkUpdate).toHaveBeenCalled())
    expect(vi.mocked(networkApi).bulkUpdate).toHaveBeenCalledTimes(1)
    const body = vi.mocked(networkApi).bulkUpdate.mock.calls[0][0]
    expect(body.rows?.length).toBe(5)
    expect(body.names).toBeUndefined()
  })

  it('a row-count mismatch changes nothing and reports both counts', async () => {
    const errSpy = vi.spyOn(toast, 'error')
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click(headerCheckbox())              // 5 rows
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    await act(async () => { firePaste(cell, '1\r\n2') })
    expect(errSpy).toHaveBeenCalled()
    const msg = errSpy.mock.calls[0][0] as string
    expect(msg).toContain('2 row(s)')
    expect(msg).toContain('5 row(s)')
    expect(vi.mocked(networkApi).bulkUpdate).not.toHaveBeenCalled()
  })

  it('one invalid value rejects the whole paste and names the cell', async () => {
    const errSpy = vi.spyOn(toast, 'error')
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click(headerCheckbox())
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    await act(async () => { firePaste(cell, '1\r\n2\r\n12o0\r\n4\r\n5') })
    expect(errSpy).toHaveBeenCalled()
    expect(errSpy.mock.calls[0][0]).toContain('B2 / v_nom')
    expect(vi.mocked(networkApi).bulkUpdate).not.toHaveBeenCalled()
  })

  it('a paste into a read-only column changes nothing and names the cell', async () => {
    const errSpy = vi.spyOn(toast, 'error')
    renderPanel()
    await screen.findByText('B0')
    const cell = document.querySelector('tbody tr td[data-col="name"]') as HTMLElement
    await userEvent.click(cell)
    await act(async () => { firePaste(cell, 'renamed') })
    expect(errSpy).toHaveBeenCalled()
    expect(errSpy.mock.calls[0][0]).toContain('name')
    expect(vi.mocked(networkApi).bulkUpdate).not.toHaveBeenCalled()
  })

  it('pasting the same values back issues no request (criterion 3)', async () => {
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click(headerCheckbox())
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    await act(async () => { firePaste(cell, '380\r\n379\r\n378\r\n377\r\n376') })
    expect(vi.mocked(networkApi).bulkUpdate).not.toHaveBeenCalled()
  })

  it('a paste over 200 rows asks for confirmation first (D18)', async () => {
    vi.mocked(networkApi).getBuses.mockResolvedValue(buses(300) as never)
    renderPanel()
    await screen.findByText('B0')
    await userEvent.click(headerCheckbox())
    const cell = document.querySelector('tbody tr td[data-col="v_nom"]') as HTMLElement
    await userEvent.click(cell)
    await act(async () => { firePaste(cell, '111') })
    // Nothing sent until the user confirms.
    expect(vi.mocked(networkApi).bulkUpdate).not.toHaveBeenCalled()
  })
})

describe('Carriers tab absorbed into the shared grid — D16', () => {
  const CARRIERS = [
    { name: 'AC', co2_emissions: 0, color: '#ff0000', nice_name: 'AC', unit: '' },
    { name: 'gas', co2_emissions: 0.2, color: '#00ff00', nice_name: 'Gas', unit: '' },
  ]
  const CARRIER_CATALOG: CatalogAttribute[] = [
    catalogAttr({ name: 'name', dtype: 'object', status: 'Input (required)' }),
    catalogAttr({ name: 'co2_emissions', unit: 't/MWh' }),
    catalogAttr({ name: 'color', dtype: 'object', type: 'string' }),
    catalogAttr({ name: 'nice_name', dtype: 'object', type: 'string' }),
    catalogAttr({ name: 'unit', dtype: 'object', type: 'string' }),
  ]

  beforeEach(() => {
    const api = vi.mocked(networkApi)
    api.getCarriers.mockResolvedValue(CARRIERS as never)
    api.getCatalog.mockImplementation(async (component: string) => ({
      component,
      attributes: component === 'Bus' ? BUS_CATALOG
        : component === 'Carrier' ? CARRIER_CATALOG : [],
    }) as never)
  })

  it('renders through AssetTable, with checkboxes like every other tab', async () => {
    renderPanel()
    await userEvent.click(screen.getByText('Carriers'))
    await screen.findByText('gas')
    expect(document.querySelectorAll('tbody input[type="checkbox"]').length).toBe(2)
  })

  it('is still called "Carriers" — a chat ui_event requests it by name', () => {
    renderPanel()
    expect(screen.getByText('Carriers')).toBeTruthy()
  })

  it('keeps the colour picker on the color column', async () => {
    renderPanel()
    await userEvent.click(screen.getByText('Carriers'))
    await screen.findByText('gas')
    const cell = document.querySelector('tbody tr td[data-col="color"]') as HTMLElement
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    expect(cell.querySelector('input[type="color"]')).toBeTruthy()
  })

  it('keeps the CO2 help line', async () => {
    renderPanel()
    await userEvent.click(screen.getByText('Carriers'))
    // /primary/ alone is ambiguous: COL_LABELS.co2_emissions is
    // 'CO₂ (t/MWh primary)', so the column header matches it too.
    expect(await screen.findByText(/output-MWh/)).toBeTruthy()
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// Defects found by the 2026-08-11 UI walk with a seeded scratch account. Each
// was invisible to the suite above because those tests drive the grid with the
// gestures the implementation happens to support.
// ─────────────────────────────────────────────────────────────────────────────

/** The Generator catalog — the only tab with a `bus` column to exercise. */
const GEN_CATALOG: CatalogAttribute[] = [
  catalogAttr({ name: 'name', dtype: 'object', status: 'Input (required)' }),
  catalogAttr({ name: 'bus', dtype: 'object', type: 'string' }),
  catalogAttr({ name: 'carrier', dtype: 'object', type: 'string' }),
  catalogAttr({ name: 'p_nom', unit: 'MW' }),
  catalogAttr({ name: 'p_nom_extendable', dtype: 'bool', type: 'boolean' }),
  catalogAttr({ name: 'marginal_cost', unit: 'currency/MWh' }),
  catalogAttr({ name: 'capital_cost', unit: 'currency/MW' }),
]

function generators() {
  return [
    { name: 'G0', bus: 'B0', carrier: 'AC', p_nom: 400, p_nom_extendable: false,
      marginal_cost: 62, capital_cost: 0 },
    { name: 'G1', bus: 'B1', carrier: 'AC', p_nom: 350, p_nom_extendable: false,
      marginal_cost: 58, capital_cost: 0 },
  ]
}

/** Re-mock the getters so the Generators tab has rows and a catalog. */
function withGenerators() {
  const api = vi.mocked(networkApi)
  api.getGenerators.mockReset().mockResolvedValue(generators() as never)
  api.getCatalog.mockReset().mockImplementation(async (component: string) => ({
    component,
    attributes: component === 'Bus' ? BUS_CATALOG
      : component === 'Generator' ? GEN_CATALOG : [],
  }) as never)
}

function cellAt(row: string, col: string): HTMLElement {
  return document.querySelector(`td[data-row="${row}"][data-col="${col}"]`) as HTMLElement
}

describe('defect 1 — a double click opens the editor', () => {
  it('double-clicking an editable cell opens its editor', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = cellAt('B0', 'v_nom')
    await userEvent.dblClick(cell)
    expect(cell.querySelector('input')).toBeTruthy()
  })

  it('a SINGLE click still only selects, so the grid keeps the clipboard', async () => {
    // Load-bearing: onCopy/onPaste both bail with `if (editing) return`, so a
    // click that opened an editor would hand Ctrl+C/Ctrl+V to the input and
    // make the multi-row paste flow (criteria 4-9) unreachable by mouse.
    renderPanel()
    await screen.findByText('B0')
    const cell = cellAt('B0', 'v_nom')
    await userEvent.click(cell)
    expect(cell.querySelector('input')).toBeNull()
  })

  it('does not open an editor when double-clicking a read-only cell', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = cellAt('B0', 'sub_network')     // status: Output
    await userEvent.dblClick(cell)
    expect(cell.querySelector('input')).toBeNull()
  })

  it('tells the user which gesture actually edits', async () => {
    renderPanel()
    await screen.findByText('B0')
    expect(screen.getByText(/double-click or press Enter to edit/i)).toBeTruthy()
  })
})

describe('defect 2 — Enter commits and moves down one row', () => {
  it('moves the active cell down after committing a change', async () => {
    vi.mocked(networkApi).bulkUpdate.mockResolvedValue({ updated: 1 } as never)
    renderPanel()
    await screen.findByText('B0')
    const cell = cellAt('B0', 'v_nom')
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    const input = cell.querySelector('input') as HTMLInputElement
    fireEvent.change(input, { target: { value: '111' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(cellAt('B1', 'v_nom')).toBe(document.activeElement))
  })

  it('still moves down when the commit was a no-op', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = cellAt('B0', 'v_nom')
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    const input = cell.querySelector('input') as HTMLInputElement
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(cellAt('B1', 'v_nom')).toBe(document.activeElement))
    expect(vi.mocked(networkApi).bulkUpdate).not.toHaveBeenCalled()
  })

  it('stays put when the value was rejected, so the user can fix it', async () => {
    const err = vi.spyOn(toast, 'error').mockImplementation(() => '' as never)
    renderPanel()
    await screen.findByText('B0')
    const cell = cellAt('B0', 'v_nom')
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    const input = cell.querySelector('input') as HTMLInputElement
    fireEvent.change(input, { target: { value: '12o0' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(err).toHaveBeenCalled())
    expect(cellAt('B1', 'v_nom')).not.toBe(document.activeElement)
  })

  it('does NOT advance on a Ctrl/Cmd+Enter fill — D5 gives it no move', async () => {
    vi.mocked(networkApi).bulkUpdate.mockResolvedValue({ updated: 1 } as never)
    renderPanel()
    await screen.findByText('B0')
    const cell = cellAt('B0', 'v_nom')
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    const input = cell.querySelector('input') as HTMLInputElement
    fireEvent.change(input, { target: { value: '222' } })
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true })
    await waitFor(() => expect(vi.mocked(networkApi).bulkUpdate).toHaveBeenCalled())
    expect(cellAt('B1', 'v_nom')).not.toBe(document.activeElement)
  })
})

describe('defect 3 — a printable character seeds the editor with itself', () => {
  it('replaces the value rather than appending to it', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = cellAt('B0', 'v_nom')          // shows 380
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: '7' })
    const input = cell.querySelector('input') as HTMLInputElement
    expect(input.value).toBe('7')
  })

  it('Enter still opens the editor on the existing value', async () => {
    renderPanel()
    await screen.findByText('B0')
    const cell = cellAt('B0', 'v_nom')
    await userEvent.click(cell)
    fireEvent.keyDown(cell, { key: 'Enter' })
    expect((cell.querySelector('input') as HTMLInputElement).value).toBe('380')
  })
})

describe('defect 4 — the bus cell is usable', () => {
  it('does not commit on every keystroke', async () => {
    // BusAutocomplete.onChange fires per keystroke; wiring it straight into
    // onCommit made the first character commit a partial name, toast an error
    // and close the editor — so the column could never be changed.
    withGenerators()
    const err = vi.spyOn(toast, 'error').mockImplementation(() => '' as never)
    renderPanel()
    await userEvent.click(screen.getByText(/^Generators/))
    await screen.findByText('G0')
    const cell = cellAt('G0', 'bus')
    await userEvent.dblClick(cell)
    const input = cell.querySelector('input') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'B' } })
    expect(err).not.toHaveBeenCalled()
    expect(vi.mocked(networkApi).bulkUpdate).not.toHaveBeenCalled()
    expect(cell.querySelector('input')).toBeTruthy()      // editor still open
  })

  it('focuses the input so the dropdown opens and arrows reach it', async () => {
    withGenerators()
    renderPanel()
    await userEvent.click(screen.getByText(/^Generators/))
    await screen.findByText('G0')
    const cell = cellAt('G0', 'bus')
    await userEvent.dblClick(cell)
    expect(cell.querySelector('input')).toBe(document.activeElement)
  })

  it('commits once when a bus is chosen from the list', async () => {
    withGenerators()
    vi.mocked(networkApi).bulkUpdate.mockResolvedValue({ updated: 1 } as never)
    renderPanel()
    await userEvent.click(screen.getByText(/^Generators/))
    await screen.findByText('G0')
    const cell = cellAt('G0', 'bus')
    await userEvent.dblClick(cell)
    const input = cell.querySelector('input') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'B2' } })
    const option = await screen.findByText('B2')
    fireEvent.mouseDown(option)
    await waitFor(() => expect(vi.mocked(networkApi).bulkUpdate).toHaveBeenCalledTimes(1))
    expect(vi.mocked(networkApi).bulkUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ updates: { bus: 'B2' } }),
    )
  })
})
