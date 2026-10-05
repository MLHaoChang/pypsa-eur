// Gate S8 [S7]: `utils/decisionVocabulary.ts` is the ONLY source of the
// guided flow's novice labels. This fails when a page under
// `pages/decision/` hard-codes a label the vocabulary defines — as a string
// literal ('…' / "…") or as JSX text (>…<). Pragmatic: labels shorter than
// five characters are skipped, since a word that short collides with code.
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import * as V from '../../utils/decisionVocabulary'

function labels(): string[] {
  const out = new Set<string>()
  const maps: Array<Record<string, unknown>> = [
    V.VIEW_LABELS, V.HUB_SECTION_LABELS, V.INTAKE_STEP_LABELS, V.CHIP_LABELS, V.VERDICT_LABELS,
    V.OPTION_LABELS, V.STREAM_LABELS, V.MATURITY_LABELS, V.LEDGER_STATUS_LABELS, V.UI_LABELS,
    V.BILL_COMPONENT_LABELS, V.REOPEN_LABELS, V.STUDY_FORK_LABELS, V.TESTED_RANGE_LABELS,
  ]
  for (const m of maps) for (const v of Object.values(m)) if (typeof v === 'string') out.add(v)
  for (const e of Object.values(V.VOCAB)) out.add(e.label)
  for (const e of Object.values(V.KPI_LABELS)) { out.add(e.label); out.add(e.technical) }
  return [...out].filter(l => l.length >= 5)
}

describe('the decision pages take their labels from the vocabulary', () => {
  const dir = join(__dirname)
  const pages = readdirSync(dir).filter(f => f.endsWith('.tsx') && !f.includes('.test.'))

  it('finds the pages and the labels', () => {
    expect(pages.length).toBeGreaterThan(8)
    expect(labels().length).toBeGreaterThan(40)
  })

  it.each(pages)('%s hard-codes no vocabulary label', page => {
    // Comments may quote a label; only code is checked.
    const src = readFileSync(join(dir, page), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
    const hits = labels().filter(l => src.includes(`'${l}'`) || src.includes(`"${l}"`) || src.includes(`>${l}<`))
    expect(hits).toEqual([])
  })
})
