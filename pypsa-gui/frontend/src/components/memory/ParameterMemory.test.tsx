// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

// Slice 03b: saved parameter values in the asset forms (GK B 47 A1–A6, UX 15 P1–P6, UX 18).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render as plainRender, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import fixture from '../../test/fixtures/assetSchema.json'
import type { AssetFieldSpec, AssetSchema } from '../../api/assetSchema'
import type { ParameterChip, ParameterPreview } from '../../api/memory'
import { useUIStore } from '../../store/uiStore'
import { AssetFieldsEdit } from '../../layout/properties/AssetFields'
import { ExpandedProps } from '../../layout/properties/cardKit'
import { FieldDrawer } from '../FieldDrawer'
import {
  PickCard, SuggestionChips, chipWords, memoryLine, parameterWords, useSaveAction,
} from './ParameterMemory'

const api = vi.hoisted(() => ({
  suggestions: vi.fn(), save: vi.fn(), preview: vi.fn(), apply: vi.fn(), act: vi.fn(),
}))
vi.mock('../../api/memory', async importOriginal => {
  const real = await importOriginal<typeof import('../../api/memory')>()
  return {
    ...real,
    memoryParameterApi: { suggestions: api.suggestions, save: api.save, preview: api.preview, apply: api.apply },
    memoryApi: { ...real.memoryApi, act: api.act },
  }
})
const toasts = vi.hoisted(() => ({ undo: vi.fn(), success: vi.fn(), plain: vi.fn() }))
vi.mock('../../utils/toasts', async importOriginal => ({
  ...(await importOriginal<typeof import('../../utils/toasts')>()), showUndoToast: toasts.undo,
}))
vi.mock('react-hot-toast', () => ({ default: Object.assign(toasts.plain, { success: toasts.success }) }))
vi.mock('../../hooks/useAssetSchema', () => ({
  useAssetSchema: (c: string | null) => ({ data: c ? (fixture as Record<string, unknown>)[c] : undefined }),
}))

const schemas = fixture as Record<string, unknown>
const GEN_SCHEMA = schemas.Generator as AssetSchema
const spec = (key: string) => GEN_SCHEMA.groups.flatMap(g => g.fields).find(f => f.key === key) as AssetFieldSpec
const TARGET = { component_class: 'Generator', name: 'gas' }

const chip = (over: Partial<ParameterChip> = {}): ParameterChip => ({
  id: 'mem_1', version: 2, scope: 'user', value: 250, unit: 'MW', currency: null, currency_year: null,
  carrier: 'gas', current: false, saved_at: '2026-11-03T10:00:00+00:00',
  source: { label: 'Project A', id: 'p1', access: true }, ...over,
})
const preview = (memOver: Record<string, unknown> = {}): ParameterPreview => ({
  version: 1, kind: 'parameter_edit', tool: 'update_component', complete: true,
  says: 'gas p_nom 100 → 250 MW', digest: 'f'.repeat(64), card_digest: 'c'.repeat(64),
  rows: [{ component_class: 'Generator', name: 'gas', field: 'p_nom', label: 'Capacity', old: 100, new: 250,
    unit: 'MW', role: 'typed' }],
  memory_note: 'Saved value (version 2): 250 MW',
  memory: {
    item: 'mem_1', version: 2, field: 'p_nom', label: 'Capacity', value: 250, unit: 'MW', unit_display: 'MW',
    currency: null, currency_year: null, carrier: 'gas',
    target: { component_class: 'Generator', name: 'gas', value: 100, source: null }, ...memOver,
  } as ParameterPreview['memory'],
})
const refusal = (status: number, error_kind: string, message: string) =>
  Object.assign(new Error(message), { response: { status, data: { detail: { error_kind, message } } } })

let qc: QueryClient
const render = (ui: ReactElement) => plainRender(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)

beforeEach(() => {
  vi.clearAllMocks()
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  useUIStore.setState({ uiMode: 'expert', currentProject: 'proj-1' })
})
afterEach(cleanup)

describe('chip words (UX P1, GK B 47 A5)', () => {
  it('reads "Used before: … in Project A" and the save date', () => {
    expect(chipWords(chip(), spec('p_nom'))).toEqual(['Used before: 250 MW in Project A', 'Saved 2026-11-03'])
  })
  it('shows a percentage as the form does', () => {
    expect(chipWords(chip({ value: 0.07, unit: 'fraction' }), spec('efficiency'))[0])
      .toBe('Used before: 7 % in Project A')
  })
  it('a cost chip uses money() and the price year and currency as saved', () => {
    const [l1, l2] = chipWords(chip({ value: 1_200_000, unit: 'EUR/MW', currency: 'USD', currency_year: 2021 }),
      spec('overnight_cost'))
    expect(l1).toBe('Used before: $ 1.2 k/kW in Project A')
    expect(l2).toBe('Saved 2026-11-03 · 2021 USD')
  })
  it('names no project the user cannot open', () => {
    expect(chipWords(chip({ source: { label: null, id: null, access: false } }), spec('p_nom'))[0])
      .toBe('Used before: 250 MW in another project')
  })
})

describe('chips (UX P2, GK B 47 A3)', () => {
  it('marks a chip current only from the server flag', () => {
    render(<SuggestionChips chips={[chip({ current: true })]} spec={spec('p_nom')} target={TARGET} onApplied={vi.fn()} />)
    expect(screen.getByTestId('memory-chip-current').textContent).toContain('current')
    expect(screen.queryByTestId('memory-chip')).toBeNull()
  })
  it('a value equal only after rounding is not marked current', () => {
    // The field shows 250 MW; the saved value is 250.00004, which the chip rounds to 250.
    render(<SuggestionChips chips={[chip({ value: 250.00004, current: false })]} spec={spec('p_nom')}
      target={TARGET} onApplied={vi.fn()} />)
    expect(screen.getByTestId('memory-chip').textContent).toContain('250 MW')
    expect(screen.queryByTestId('memory-chip-current')).toBeNull()
  })
})

describe('the pick card (GK B 47 A4–A6, UX P3)', () => {
  const open = async () => {
    const onApplied = vi.fn()
    render(<SuggestionChips chips={[chip()]} spec={spec('p_nom')} target={TARGET} onApplied={onApplied} />)
    await userEvent.click(screen.getByTestId('memory-chip'))
    return onApplied
  }

  it('shows the memory line and every fact the digest binds', async () => {
    api.preview.mockResolvedValue(preview({ currency: 'EUR', currency_year: 2024 }))
    await open()
    expect(await screen.findByTestId('memory-line')).toHaveProperty('textContent',
      'From memory v2 · saved in Project A · 2026-11-03 · 2024 EUR · carrier gas')
    const facts = screen.getByTestId('memory-pick-facts').textContent
    expect(facts).toContain('Capacity: 250 MW')
    expect(facts).toContain('Applies to gas')
  })

  it.each([
    ['value', { value: 300 }], ['unit', { unit: 'kW' }], ['currency', { currency: 'USD', currency_year: 2024 }],
    ['price year', { currency: 'EUR', currency_year: 2019 }], ['carrier', { carrier: 'biogas' }],
    ['version', { version: 3 }], ['target field', { label: 'Maximum capacity' }],
    ['target asset', { target: { component_class: 'Generator', name: 'gas2', value: 100, source: null } }],
  ])('a different %s in the preview changes the card text', async (_what, over) => {
    api.preview.mockResolvedValueOnce(preview({ currency: 'EUR', currency_year: 2024 }))
    await open()
    const before = (await screen.findByTestId('memory-pick-card')).textContent
    cleanup()
    api.preview.mockResolvedValueOnce(preview({ currency: 'EUR', currency_year: 2024, ...over }))
    await open()
    await screen.findByTestId('memory-line')
    expect(screen.getByTestId('memory-pick-card').textContent).not.toBe(before)
  })

  it('the source project is on the card and moves with the chip', () => {
    const m = preview().memory
    expect(memoryLine(m, chip())).toContain('saved in Project A')
    expect(memoryLine(m, chip({ source: { label: 'Project B', id: 'p2', access: true } }))).toContain('saved in Project B')
  })

  it('Apply posts the preview it showed, untouched, and the form takes in what was written', async () => {
    const p = preview()
    api.preview.mockResolvedValue(p)
    api.apply.mockResolvedValue({ written: { p_nom: 250 }, apply_counted: true, memory: p.memory })
    const onApplied = await open()
    await userEvent.click(await screen.findByTestId('memory-pick-apply'))
    await waitFor(() => expect(onApplied).toHaveBeenCalledWith({ p_nom: 250 }))
    expect(api.apply).toHaveBeenCalledWith(p, 'proj-1')
    expect(api.preview).toHaveBeenCalledWith(expect.objectContaining({ id: 'mem_1', version: 2 }), expect.objectContaining({
      project_id: 'proj-1', ...TARGET }))
  })

  it('the apply body is the preview\'s ids and digest, byte for byte', async () => {
    const real = await vi.importActual<typeof import('../../api/memory')>('../../api/memory')
    const client = (await import('../../api/client')).default
    const post = vi.spyOn(client, 'post').mockResolvedValue({ data: {} })
    const p = preview()
    await real.memoryParameterApi.apply(p, 'proj-1')
    const [, body] = post.mock.calls[0]
    expect(JSON.stringify(body)).toBe(JSON.stringify({
      item_id: p.memory.item, version: p.memory.version, project_id: 'proj-1',
      component_class: p.memory.target.component_class, name: p.memory.target.name, digest: p.digest,
    }))
    post.mockRestore()
  })

  it.each([
    ['currency_mismatch', 'Capacity: this value was saved in USD and this project uses EUR. Nothing is converted.'],
    ['memory_money_year_mismatch', 'Capacity: this value was saved in 2019 money and this project reads it in 2024 money. Nothing is converted or relabelled.'],
    ['memory_carrier_mismatch', 'Capacity: this value was saved for solar and this asset is gas.'],
    ['memory_unit_mismatch', 'Capacity: this value was saved in kW and this field is stored in MW. Nothing is converted.'],
    ['memory_money_basis_missing', 'Capacity: this project states no currency and money year for it, so a saved price is not applied.'],
    ['memory_no_change', 'This field already has the saved value.'],
  ])('a %s refusal replaces the card, and nothing can be applied', async (kind, message) => {
    api.preview.mockRejectedValue(refusal(422, kind, message))
    await open()
    expect((await screen.findByTestId('memory-pick-refusal')).textContent).toBe(message)
    expect(screen.queryByTestId('memory-pick-apply')).toBeNull()
    expect(screen.queryByTestId('memory-line')).toBeNull()
  })

  it.each(['edit_preview_stale', 'memory_stale'])('a %s 409 on apply applies nothing and offers only Preview again',
    async kind => {
      api.preview.mockResolvedValue(preview())
      api.apply.mockRejectedValue(refusal(409, kind, 'This saved value changed since it was shown. Pick it again.'))
      const spy = vi.spyOn(qc, 'invalidateQueries')
      const onApplied = await open()
      await userEvent.click(await screen.findByTestId('memory-pick-apply'))
      await screen.findByTestId('memory-pick-refusal')
      expect(onApplied).not.toHaveBeenCalled()
      expect(spy.mock.calls.map(c => JSON.stringify(c[0]?.queryKey))).not.toContainEqual(
        expect.stringContaining('generators'))
      expect(screen.getByTestId('memory-pick-again')).toBeTruthy()
      expect(screen.queryByTestId('memory-pick-apply')).toBeNull()
      expect(screen.queryByTestId('memory-pick-cancel')).toBeNull()
      // Preview again re-reads the chips and previews the item's current version.
      api.suggestions.mockResolvedValue({ fields: { p_nom: [chip({ version: 3 })] }, saveable: [] })
      api.preview.mockResolvedValue(preview({ version: 3 }))
      await userEvent.click(screen.getByTestId('memory-pick-again'))
      await waitFor(() => expect(api.preview).toHaveBeenLastCalledWith({ ...chip({ version: 3 }) },
        expect.objectContaining(TARGET)))
      expect(await screen.findByTestId('memory-pick-apply')).toBeTruthy()
    })

  it.each(['Escape', 'Cancel'])('%s closes the card and returns focus to the chip', async how => {
    api.preview.mockResolvedValue(preview())
    await open()
    await screen.findByTestId('memory-pick-apply')
    if (how === 'Escape') await userEvent.keyboard('{Escape}')
    else await userEvent.click(screen.getByTestId('memory-pick-cancel'))
    expect(screen.queryByTestId('memory-pick-card')).toBeNull()
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('memory-chip')))
  })

  it('after an Apply, focus follows the chip to its "current" marker', async () => {
    const p = preview()
    api.preview.mockResolvedValue(p)
    api.apply.mockResolvedValue({ written: { p_nom: 250 }, apply_counted: true, memory: p.memory })
    const { rerender } = render(<SuggestionChips chips={[chip()]} spec={spec('p_nom')} target={TARGET} onApplied={vi.fn()} />)
    await userEvent.click(screen.getByTestId('memory-chip'))
    await userEvent.click(await screen.findByTestId('memory-pick-apply'))
    await waitFor(() => expect(screen.queryByTestId('memory-pick-card')).toBeNull())
    // The refetch marks the applied chip current.
    rerender(<QueryClientProvider client={qc}><SuggestionChips chips={[chip({ current: true })]} spec={spec('p_nom')}
      target={TARGET} onApplied={vi.fn()} /></QueryClientProvider>)
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('memory-chip-current')))
  })

  it('a refused preview card still closes with Esc', async () => {
    api.preview.mockRejectedValue(refusal(422, 'memory_carrier_mismatch', 'Capacity: this value was saved for solar and this asset is gas.'))
    render(<PickCard chip={chip()} spec={spec('p_nom')} target={TARGET} onClose={vi.fn()} onApplied={vi.fn()} />)
    await screen.findByTestId('memory-pick-refusal')
    expect(screen.getByTestId('memory-pick-cancel').textContent).toBe('Close')
  })
})

function ReadOut({ fields = ['p_nom'] as string[] }) {
  const action = useSaveAction('Generator', 'gas', { name: 'gas', p_nom: 100 })
  return <ExpandedProps items={fields.map(f => ({ label: spec(f).label, value: 1, field: f }))} action={action} />
}

describe('Save for reuse (GK B 47 A1, A2; UX 18 points 2, 4)', () => {
  it('is a button, in the Tab order, only on a field Save accepts', async () => {
    api.suggestions.mockResolvedValue({ fields: {}, saveable: ['p_nom'] })
    render(<ReadOut fields={['p_nom', 'marginal_cost']} />)
    const btn = await screen.findByTestId('memory-save-p_nom')
    expect(btn.tagName).toBe('BUTTON')
    expect(btn.getAttribute('tabindex')).toBeNull()
    expect(btn.className).toContain('focus:opacity-100')
    expect(btn.className).toContain('focus-visible:outline')
    expect(screen.queryByTestId('memory-save-marginal_cost')).toBeNull()
  })

  it('a first save offers Undo, which retires exactly the item it created', async () => {
    api.suggestions.mockResolvedValue({ fields: {}, saveable: ['p_nom'] })
    api.save.mockResolvedValue({ id: 'mem_new', version: 1, created: true, applies_to: 'capacity_expansion' })
    render(<ReadOut />)
    await userEvent.click(await screen.findByTestId('memory-save-p_nom'))
    await waitFor(() => expect(toasts.undo).toHaveBeenCalled())
    expect(api.save).toHaveBeenCalledWith({ project_id: 'proj-1', component_class: 'Generator', name: 'gas', field: 'p_nom' })
    const [message, onUndo] = toasts.undo.mock.calls[0]
    expect(message).toBe('Saved for reuse in capacity expansion projects')
    await act(async () => { await onUndo() })
    expect(api.act).toHaveBeenCalledWith('mem_new', 'retire')
  })

  it('a re-save is a new version: no Undo, and the earlier value is named as kept', async () => {
    api.suggestions.mockResolvedValue({ fields: {}, saveable: ['p_nom'] })
    api.save.mockResolvedValue({ id: 'mem_old', version: 3, created: false, applies_to: null })
    render(<ReadOut />)
    await userEvent.click(await screen.findByTestId('memory-save-p_nom'))
    await waitFor(() => expect(toasts.success).toHaveBeenCalled())
    expect(toasts.undo).not.toHaveBeenCalled()
    expect(toasts.success.mock.calls[0][0]).toBe(
      'Saved value updated (version 3). The earlier value stays in the Memory dialog as a version.')
    expect(api.act).not.toHaveBeenCalled()
  })

  it('a refusal shows in place, in a status region, and clears after its timeout', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      api.suggestions.mockResolvedValue({ fields: {}, saveable: ['p_nom'] })
      api.save.mockRejectedValue(refusal(422, 'memory_field_has_series',
        'Capacity of gas is set by a time series, which the solve reads instead of a single value.'))
      render(<ReadOut />)
      await userEvent.click(await screen.findByTestId('memory-save-p_nom'))
      const status = await screen.findByTestId('memory-save-refusal-p_nom')
      await waitFor(() => expect(status.textContent).toMatch(/^Capacity of gas is set by a time series/))
      expect(status.getAttribute('role')).toBe('status')
      await act(async () => { vi.advanceTimersByTime(8100) })
      expect(status.textContent).toBe('')
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('Expert only (UX P6, UX 18 point 5)', () => {
  const GEN = { name: 'gas', bus: 'B1', carrier: 'gas', p_nom: 100 }
  const fs = Object.fromEntries(Object.entries(GEN).map(([k, v]) => [k, String(v)]))
  const Form = () => (
    <div className="grid grid-cols-2">
      <ReadOut />
      <AssetFieldsEdit schema={GEN_SCHEMA} fs={fs} set={() => {}} current={GEN} carriers={[]} buses={[]} suggestions />
    </div>
  )
  beforeEach(() => api.suggestions.mockResolvedValue({ fields: { p_nom: [chip()] }, saveable: ['p_nom'] }))

  it('Expert shows the chips in the edit form and Save in the read-out', async () => {
    render(<Form />)
    expect(await screen.findByTestId('memory-chips-p_nom')).toBeTruthy()
    expect(await screen.findByTestId('memory-save-p_nom')).toBeTruthy()
  })

  it('the Guided field drawer renders neither and asks for nothing', async () => {
    useUIStore.setState({ uiMode: 'guided', fieldDrawer: { section: 'assets', component: 'Generator', entity: 'gas' } as never })
    render(<FieldDrawer>{() => <Form />}</FieldDrawer>)
    expect(screen.getByTestId('field-drawer')).toBeTruthy()
    await new Promise(r => setTimeout(r, 20))
    expect(screen.queryByTestId('memory-chips-p_nom')).toBeNull()
    expect(screen.queryByTestId('memory-save-p_nom')).toBeNull()
    expect(api.suggestions).not.toHaveBeenCalled()
  })
})

describe('Memory dialog words (UX P5)', () => {
  it('reads "<Field label> (<Class in user words>)" with the form\'s own label', () => {
    expect(parameterWords({ component_class: 'StorageUnit', field: 'p_nom', value: 5, unit: 'MW' },
      (schemas.StorageUnit as AssetSchema).groups.flatMap(g => g.fields).find(f => f.key === 'p_nom')))
      .toMatch(/^.+ \(Storage\): 5 MW$/)
    expect(parameterWords({ component_class: 'Generator', field: 'marginal_cost', value: 50, unit: 'EUR/MWh',
      currency: 'EUR', currency_year: 2024, carrier: 'gas' }, spec('marginal_cost')))
      .toBe('Marginal cost (Generator): € 50/MWh · 2024 EUR · carrier gas')
  })

  it('every saveable field of every class reads with the label its asset form shows, never a key', () => {
    for (const [cls, raw] of Object.entries(schemas)) {
      const s = raw as AssetSchema
      if (!s?.groups) continue
      for (const f of s.groups.flatMap(g => g.fields)) {
        if (f.derived || (f.kind !== 'number' && f.kind !== 'int')) continue
        const words = parameterWords({ component_class: cls, field: f.key, value: 1, unit: f.unit_storage }, f)
        expect(words.startsWith(`${f.label} (`)).toBe(true)
        expect(words).not.toContain(`${cls}.`)
      }
    }
  })
})
