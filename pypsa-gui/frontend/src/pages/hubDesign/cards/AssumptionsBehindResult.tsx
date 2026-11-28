// "Assumptions behind this result" (template-e2e 26 / 08): the inputs the verdict and cost rest on that the
// person has not stated, and the synthetic-data line. One list for Guided and Expert results; the report's
// "Template assumptions" chapter lists the same rows (template ledger rows marked assumed, then the study
// run's own defaults). Chips come from AssumedChip (#1734 / issue 13); no second label.
import { AssumedChip } from '../../../components/AssumedChip'
import type { EhStudyAssumption } from '../../../api/simulation'
import { useHubStudy, useHubTemplate } from '../useHubData'

export const SYNTHETIC_TEXT = 'Synthetic illustrative data unless you set your own: these results are not site-specific.'

interface Row { key: string, label: string, shown: string, source: string | null, note: string | null }

/** Ledger rows marked assumed first (as the report orders them), then what the study run assumed. */
export function assumptionRows(
  ledger: { item: string, label?: string, value: string | number, unit?: string, source: string, note?: string }[] | undefined,
  run: EhStudyAssumption[] | undefined,
): Row[] {
  const out: Row[] = []
  for (const r of ledger ?? []) {
    if (r.source !== 'assumed') continue
    out.push({ key: `ledger/${r.item}`, label: r.label || r.item,
      shown: `${r.value}${r.unit ? ` ${r.unit}` : ''}`, source: null, note: r.note ?? null })
  }
  for (const r of run ?? []) {
    out.push({ key: `run/${r.key}`, label: r.label, shown: r.shown, source: null, note: r.reason })
  }
  return out
}

export function AssumptionsBehindResult({ testId = 'results-assumptions' }: { testId?: string }) {
  const { template } = useHubTemplate()
  const { study } = useHubStudy()
  if (!template) return null
  const rows = assumptionRows(template.ledger, Array.isArray(study?.assumptions) ? study.assumptions : undefined)
  const notRecorded = !!study && study.status !== 'running' && study.assumptions === undefined
  return (
    <div data-testid={testId} className="flex flex-col gap-1 text-[11.5px]">
      <p data-testid={`${testId}-synthetic`} className="rounded border border-warn/50 bg-warn/10 px-2 py-1 text-warn">
        {SYNTHETIC_TEXT}
      </p>
      <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">
        Assumptions behind this result ({rows.length})
      </span>
      {rows.length === 0 ? (
        <p data-testid={`${testId}-none`} className="text-muted">
          {notRecorded ? 'This study did not record the defaults it assumed; run it again to list them.' : 'No assumed inputs.'}
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {rows.map(r => (
            <li key={r.key} data-testid={`${testId}-row`} className="flex flex-col gap-0.5">
              <span className="text-text">{r.label}: {r.shown}</span>
              <AssumedChip source={r.source} note={r.note} testId={`${testId}-chip`} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
