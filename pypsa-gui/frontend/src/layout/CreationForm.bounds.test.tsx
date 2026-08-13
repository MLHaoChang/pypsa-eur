// Criterion 34 (the extendable bounds) and criterion 30 (the picker's
// description), both found unmet by the 2026-08-11 UI walk.
//
// The payload guard here is the important one: CreationForm's submit loop
// enumerates `fields`, not the REVEALED fields, so adding a hidden bound to the
// field list would have posted `p_nom_max: 0` on every non-extendable generator
// — capping its capacity at zero. Nothing is hidden today, so the hazard is
// latent until criterion 34 is implemented.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'
import CreationForm from './CreationForm'

vi.mock('../api/network', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/network')>()
  return {
    ...actual,
    networkApi: {
      ...actual.networkApi,
      getCarriers: vi.fn(async () => []),
      getCatalog: vi.fn(),
      createGenerator: vi.fn(async () => ({})),
    },
  }
})

import { networkApi } from '../api/network'
import type { CatalogAttribute } from '../api/types'

const BUSES = [{ name: 'Elec A', carrier: 'AC' }]

function attr(over: Partial<CatalogAttribute> & { name: string }): CatalogAttribute {
  return {
    status: 'Input (optional)', varying: false, dtype: 'float64', unit: null,
    description: null, type: 'float', default: 0, default_text: '0.0', ...over,
  }
}

const GEN_CATALOG: CatalogAttribute[] = [
  attr({
    name: 'p_nom_max', unit: 'MW', default: null, default_text: 'inf',
    description: 'If `p_nom` is extendable in optimization, set its maximum '
      + 'value (e.g. limited by technical potential).',
  }),
  attr({ name: 'weight', default: 1, default_text: '1.0', description: 'Weighting of the generator.' }),
  // Unbounded and NOT a curated field, so it is what the picker must render
  // with `inf` rather than a blank. p_nom_max can no longer serve as that
  // example: criterion 34 makes it a curated field, and the picker correctly
  // stops offering fields the form already shows.
  attr({
    name: 'e_sum_max', unit: 'MWh', default: null, default_text: 'inf',
    description: 'Maximum total energy production over the whole snapshot range.',
  }),
]

function renderForm() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  client.setQueryData(nk('Demo', 'buses'), BUSES)
  return render(
    <QueryClientProvider client={client}>
      <CreationForm item={{ id: 'thermal', label: 'Conventional' }} />
    </QueryClientProvider>,
  )
}

/** Tick the Extendable checkbox. */
function toggleExtendable() {
  const label = screen.getByText('Extendable').closest('label') as HTMLElement
  const box = label.querySelector('input[type="checkbox"]') as HTMLInputElement
  fireEvent.click(box)
  return box
}

/**
 * The input under the <label> whose text matches. Required fields render as
 * "Name *", so an exact-text lookup misses them.
 */
function fieldInput(labelRe: RegExp): HTMLInputElement {
  const label = Array.from(document.querySelectorAll('label'))
    .find(l => labelRe.test(l.textContent ?? ''))
  if (!label) throw new Error(`no field labelled ${labelRe}`)
  const scope = (label.querySelector('input') ? label : label.parentElement) as HTMLElement
  return scope.querySelector('input') as HTMLInputElement
}

async function submit(name: string) {
  fireEvent.change(fieldInput(/^Name/), { target: { value: name } })
  fireEvent.change(fieldInput(/^Attach to Bus/), { target: { value: 'Elec A' } })
  await userEvent.click(screen.getByText('Add to Network'))
}

beforeEach(() => {
  vi.mocked(networkApi).getCatalog.mockReset().mockResolvedValue(
    { component: 'Generator', attributes: GEN_CATALOG } as never)
  vi.mocked(networkApi).createGenerator.mockReset().mockResolvedValue({} as never)
  useUIStore.setState({ currentProject: 'Demo', creationItem: null })
})

afterEach(() => {
  vi.restoreAllMocks()
  useUIStore.setState({ currentProject: null, creationItem: null })
})

describe('criterion 34 — the extendable bounds are revealed, not absent', () => {
  it('hides P nom min / P nom max until Extendable is ticked', () => {
    renderForm()
    expect(screen.queryByText(/P nom min/i)).toBeNull()
    expect(screen.queryByText(/P nom max/i)).toBeNull()
  })

  it('shows both once Extendable is ticked', () => {
    renderForm()
    toggleExtendable()
    expect(screen.getByText(/P nom min/i)).toBeTruthy()
    expect(screen.getByText(/P nom max/i)).toBeTruthy()
  })

  it('hides them again when Extendable is unticked', () => {
    renderForm()
    toggleExtendable()
    toggleExtendable()
    expect(screen.queryByText(/P nom max/i)).toBeNull()
  })
})

describe('the submit payload only carries fields the user could see', () => {
  it('does NOT post p_nom_max when the bounds are hidden', async () => {
    // Without this, a non-extendable generator would be created with
    // p_nom_max = 0 — a silent capacity cap of zero on every asset.
    renderForm()
    await submit('G_hidden')
    await waitFor(() => expect(vi.mocked(networkApi).createGenerator).toHaveBeenCalled())
    const payload = vi.mocked(networkApi).createGenerator.mock.calls[0][0] as Record<string, unknown>
    expect(payload).not.toHaveProperty('p_nom_max')
    expect(payload).not.toHaveProperty('p_nom_min')
  })

  it('omits a revealed bound the user left blank, so PyPSA’s own default applies', async () => {
    renderForm()
    toggleExtendable()
    await submit('G_blank')
    await waitFor(() => expect(vi.mocked(networkApi).createGenerator).toHaveBeenCalled())
    const payload = vi.mocked(networkApi).createGenerator.mock.calls[0][0] as Record<string, unknown>
    // p_nom_max declares no defaultValue: blank must mean "unset", never 0.
    expect(payload).not.toHaveProperty('p_nom_max')
    expect(payload.p_nom_min).toBe(0)
  })

  it('posts a bound the user actually typed', async () => {
    renderForm()
    toggleExtendable()
    fireEvent.change(fieldInput(/^P nom max/i), { target: { value: '900' } })
    await submit('G_typed')
    await waitFor(() => expect(vi.mocked(networkApi).createGenerator).toHaveBeenCalled())
    const payload = vi.mocked(networkApi).createGenerator.mock.calls[0][0] as Record<string, unknown>
    expect(payload.p_nom_max).toBe(900)
  })
})

describe('criterion 30 — the picker shows the description', () => {
  it('puts the attribute description in the option, not only its type', async () => {
    renderForm()
    await userEvent.click(await screen.findByText('+ Add parameter'))
    const option = await screen.findByRole('option', { name: /e_sum_max/ })
    expect(option.textContent).toMatch(/MWh/)           // unit
    expect(option.textContent).toMatch(/float/)         // type
    expect(option.textContent).toMatch(/inf/)           // default, not blank
    expect(option.textContent).toMatch(/Maximum total energy/)
  })

  it('carries the full description as a title for the truncated text', async () => {
    renderForm()
    await userEvent.click(await screen.findByText('+ Add parameter'))
    const option = await screen.findByRole('option', { name: /e_sum_max/ })
    expect(option.getAttribute('title')).toContain('whole snapshot range')
  })

  it('no longer offers a curated field as an extra', async () => {
    renderForm()
    await userEvent.click(await screen.findByText('+ Add parameter'))
    expect(screen.queryByRole('option', { name: /p_nom_max/ })).toBeNull()
  })
})
