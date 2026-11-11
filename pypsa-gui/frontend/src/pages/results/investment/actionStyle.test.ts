// ui-modern 05 slice 2: section 7's small actions use the card style, and keep their names.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { ACTION } from './actionStyle'

const src = (f: string) => readFileSync(resolve(__dirname, f), 'utf8')

describe('section 7 actions', () => {
  it.each(['ContractsEditor.tsx', 'FinanceInputsEditor.tsx', 'TariffBuilder.tsx'])('%s has no bare underlined action', f => {
    expect(src(f)).not.toMatch(/className="underline"/)
    expect(src(f)).toContain("from './actionStyle'")
  })
  it('the three named actions keep their names', () => {
    expect(src('ContractsEditor.tsx')).toMatch(/className=\{ACTION\}[\s\S]{0,300}?Add contract</)
    expect(src('ContractsEditor.tsx')).toMatch(/className=\{ACTION\}[\s\S]{0,300}?Reload the contracts</)
    expect(src('FinanceInputsEditor.tsx')).toMatch(/className=\{ACTION\}[\s\S]{0,300}?State the finance inputs</)
  })
  it('the style is the bordered one, and shows a disabled action as such', () => {
    expect(ACTION).toContain('border-border')
    expect(ACTION).toContain('disabled:opacity-50')
  })
})
