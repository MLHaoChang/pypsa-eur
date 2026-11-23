// Catalogue 08: a half-width column cuts a long option name in the closed dropdown, so an enum
// with a long option takes the full row; a short one keeps its half. Catalogue 07: a long Row
// label wraps in its own column and the value stays on the label's line, right-aligned.
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { AssetFieldSpec, AssetSchema } from '../../api/assetSchema'
import { AssetFieldsEdit } from './AssetFields'
import { Row } from './cardKit'

const spec = (key: string, options: string[]): AssetFieldSpec => ({
  key, technical_name: key, group: 'performance', label: key, help: '', unit_display: '', unit_storage: '',
  scale: 1, kind: 'enum', options, required: false, key_param: false, derived: false, reveal_when: null,
  part: null, blank: 'clear',
})
const SCHEMA: AssetSchema = {
  component: 'Generator',
  groups: [{ id: 'performance', label: 'Performance', fields: [
    spec('harmonic_source', ['linear', '6-pulse', 'active-front-end']), spec('short_one', ['a', 'b']),
  ] }],
}

describe('EnumInput width', () => {
  it('spans the row when an option is long, and stays half-width otherwise', () => {
    render(<QueryClientProvider client={new QueryClient()}><div className="grid grid-cols-2">
      <AssetFieldsEdit schema={SCHEMA} fs={{}} set={() => {}} current={{}} carriers={[]} buses={[]} />
    </div></QueryClientProvider>)
    expect(screen.getByTestId('asset-enum-harmonic_source').className).toContain('col-span-2')
    expect(screen.getByTestId('asset-enum-short_one').className).not.toContain('col-span-2')
  })
})

describe('Row label wrap', () => {
  it('lets the label shrink and wrap while the value keeps its width on the same line', () => {
    render(<Row label="Maximum short-circuit power" value={3000} unit="MVA" />)
    const label = screen.getByTestId('row-label')
    expect(label.className).toMatch(/min-w-0/)
    expect(label.className).not.toMatch(/shrink-0/)
    expect(screen.getByText('3,000').className).toMatch(/shrink-0/)
  })
})
