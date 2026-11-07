// UIP-01: a fix link that names a Link field opens the editable form at that
// field (focused, highlighted); Properties never groups a year ("2027", not
// "2,027") and never repeats the unit word ("year year").
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render as plainRender, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import fixture from '../../test/fixtures/assetSchema.json'
import type { AssetSchema } from '../../api/assetSchema'
import { AssetFieldsEdit, assetReadoutItems } from './AssetFields'
import { Row, type FS } from './cardKit'
import { useUIStore } from '../../store/uiStore'
import { openFlowTargetInPlace, openFlowTarget } from '../flowNavigation'

const render = (ui: ReactElement) => plainRender(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>)
const SCHEMA = (fixture as Record<string, unknown>).Link as AssetSchema
const LINK: Record<string, unknown> = { name: 'imp', bus0: 'a', bus1: 'b', carrier: 'AC', p_nom: 100, efficiency: 0.9, build_year: 2027 }
const FORM: FS = Object.fromEntries(Object.entries(LINK).map(([k, v]) => [k, String(v)]))

afterEach(() => { cleanup(); useUIStore.setState({ propertiesEditRequest: null, selectedComponent: null, fieldDrawer: null }) })

describe('focusField', () => {
  it('scrolls to, focuses and highlights the named field', () => {
    Element.prototype.scrollIntoView = vi.fn()
    render(<div className="grid grid-cols-2"><AssetFieldsEdit schema={SCHEMA} fs={FORM} set={() => {}} current={LINK}
      carriers={[]} buses={[]} focusField="efficiency" /></div>)
    const input = document.querySelector('[data-field="efficiency"] input') as HTMLInputElement
    expect(input).toBeTruthy()
    expect(document.activeElement).toBe(input)
    expect(input.classList.contains('field-flash')).toBe(true)
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled()
  })
})

describe('a Link fix link requests the editor at its field', () => {
  const target = { section: 'connection', panel: null, component: 'Link', entity: 'imp', field: 'efficiency' } as const
  it.each([['Guided', openFlowTargetInPlace], ['Expert', openFlowTarget]] as const)('%s', (_m, open) => {
    open(target)
    expect(useUIStore.getState().propertiesEditRequest).toEqual({ type: 'Link', name: 'imp', field: 'efficiency' })
  })
  it('a target without a field leaves the read-only card alone', () => {
    openFlowTarget({ ...target, field: null })
    expect(useUIStore.getState().propertiesEditRequest).toBeNull()
  })
})

describe('a year reads as a year', () => {
  it('has no thousands separator and no unit word', () => {
    const items = assetReadoutItems(SCHEMA, LINK, new Set(), undefined, undefined)
    const y = items.find(i => i.label === 'Build year')!
    const { container } = render(<Row label={y.label} value={y.value} unit={y.unit} />)
    expect(container.textContent).toBe('Build year2027')
    expect(within(container).queryByText('year')).toBeNull()
  })
  it('keeps the unit word out of the edit label too', () => {
    render(<div className="grid grid-cols-2"><AssetFieldsEdit schema={SCHEMA} fs={FORM} set={() => {}} current={LINK}
      carriers={[]} buses={[]} /></div>)
    const label = document.querySelector('[data-field="build_year"]')!.textContent ?? ''
    expect(label).toContain('Build year')
    expect(label).not.toContain('(year)')
  })
  it('still groups an ordinary number', () => {
    const { container } = render(<Row label="Cost" value={12345} />)
    expect(container.textContent).toMatch(/12.345/)
  })
})
void screen
