// "Assumptions behind this result" (template-e2e 26 / 08): the inputs the verdict and cost rest on that the
// person has not stated, and the synthetic-data line. One list for Guided and Expert results; the report's
// "Template assumptions" chapter lists the same rows (template ledger rows marked assumed, then the study
// run's own defaults). Chips come from AssumedChip (#1734 / issue 13); no second label.
import { useState } from 'react'
import { paramLabel } from '../../../plainNames'
import { evidenceValue, kpiMoney } from '../plainWords'
import { AssumedChip } from '../../../components/AssumedChip'
import type { EhStudyAssumption } from '../../../api/simulation'
import { useHubStudy, useHubTemplate } from '../useHubData'

export const SYNTHETIC_TEXT = 'Synthetic illustrative data unless you set your own: these results are not site-specific.'

const FOLDED_AT = 5

/** A ledger value in words: money at the tiles' rounding, other numbers to four significant digits. */
function ledgerShown(value: string | number, unit?: string): string {
  if (typeof value === 'number' && (unit === 'EUR' || unit === '€')) return kpiMoney(value)
  const v = typeof value === 'number' ? evidenceValue(value) : value
  return `${v}${unit ? ` ${unit}` : ''}`
}

interface Row { key: string, label: string, shown: string, source: string | null, note: string | null }

/** Ledger rows marked assumed first (as the report orders them), then what the study run assumed. */
export function assumptionRows(
  ledger: { item: string, label?: string, value: string | number, unit?: string, source: string, note?: string }[] | undefined,
  run: EhStudyAssumption[] | undefined,
): Row[] {
  const out: Row[] = []
  for (const r of ledger ?? []) {
    if (r.source !== 'assumed') continue
    out.push({ key: `ledger/${r.item}`, label: r.label || paramLabel(r.item),
      shown: ledgerShown(r.value, r.unit), source: r.note ?? null, note: null })
  }
  for (const r of run ?? []) {
    out.push({ key: `run/${r.key}`, label: r.label, shown: r.shown, source: r.reason, note: null })
  }
  return out
}

export function AssumptionsBehindResult({ testId = 'results-assumptions' }: { testId?: string }) {
  const { template } = useHubTemplate()
  const { study } = useHubStudy()
  if (!template) return null
  const [open, setOpen] = useState(false)
  const rows = assumptionRows(template.ledger, Array.isArray(study?.assumptions) ? study.assumptions : undefined)
  // True only while a template default still stands: a ledger row not yet stated by the person.
  const synthetic = (template.ledger ?? []).some(r => r.source === 'assumed' || r.source === 'illustrative')
  const notRecorded = !!study && study.status !== 'running' && study.assumptions === undefined
  return (
    <div data-testid={testId} className="flex flex-col gap-1 text-[11.5px]">
      {synthetic && <p data-testid={`${testId}-synthetic`} className="text-muted">{SYNTHETIC_TEXT}</p>}
      <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">
        Assumptions behind this result ({rows.length})
      </span>
      {rows.length === 0 ? (
        <p data-testid={`${testId}-none`} className="text-muted">
          {notRecorded ? 'This study did not record the defaults it assumed; run it again to list them.' : 'No assumed inputs.'}
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {(open ? rows : rows.slice(0, FOLDED_AT)).map(r => (
            <li key={r.key} data-testid={`${testId}-row`} className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-text">{r.label}: {r.shown}</span>
              <AssumedChip source={r.source} note={r.note} testId={`${testId}-chip`} />
            </li>
          ))}
        </ul>
      )}
      {rows.length > FOLDED_AT && (
        <button type="button" data-testid={`${testId}-toggle`} onClick={() => setOpen(o => !o)}
          className="self-start text-link hover:underline">
          {open ? 'Show fewer' : `(${rows.length - FOLDED_AT} more) Show all`}
        </button>
      )}
    </div>
  )
}
