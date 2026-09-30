// Options (spec §3 screen 3, plan S8): the battery durations the question
// compares, the PV toggle, and the cost the options are priced at — the
// library's central value with its range and source year. The low/high ends
// are shown, not applied: a value the user did not quote must not count as
// "customised" (it would raise the maturity badge). A quote goes in Assumptions.
import type { LedgerRow, StudyError, StudyIntake } from '../../api/decisionStudies'
import { OPTION_LABELS, UI_LABELS } from '../../utils/decisionVocabulary'
import { ledgerCurrencyYearLabel, optionsFor, valueText } from './decisionModel'
import { Card, Refusal } from './DecisionUi'

const COST_KEYS = ['battery_storage_eur_per_kwh', 'battery_inverter_eur_per_kw']

export default function Options({ intake, ledgerRows, onPv, saving, error, hasRun }: {
  intake: StudyIntake
  ledgerRows: LedgerRow[]
  onPv: (pv: { enabled: boolean; kind: 'rooftop' | 'utility' }) => void
  saving: boolean
  error: StudyError | null
  hasRun: boolean
}) {
  const pv = { enabled: !!intake.pv?.enabled, kind: intake.pv?.kind ?? 'rooftop' }
  const pvCost = ledgerRows.find(r => r.key === `pv_${pv.kind}_eur_per_kw`)
  return (
    <div className="flex flex-col gap-4">
      <Card title="What the study compares">
        <ul data-testid="options-list" className="list-disc pl-5">
          {optionsFor(intake).map(id => <li key={id}>{OPTION_LABELS[id] ?? id}</li>)}
        </ul>
        <p className="text-muted">
          The optimiser chooses the battery power for each duration; the grid-only baseline is what every option is measured against.
        </p>
      </Card>
      <Card title="Solar PV">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={pv.enabled} disabled={saving}
            onChange={e => onPv({ ...pv, enabled: e.target.checked })} />
          Also compare a battery with new solar PV
        </label>
        {pv.enabled && (
          <label className="flex items-center gap-2">{UI_LABELS.pvType}
            <select value={pv.kind} disabled={saving} onChange={e => onPv({ ...pv, kind: e.target.value as 'rooftop' | 'utility' })}
              className="border border-border rounded px-2 py-1">
              <option value="rooftop">{UI_LABELS.pvRooftop}</option>
              <option value="utility">{UI_LABELS.pvGround}</option>
            </select>
            {pvCost?.value != null && <span className="text-muted">{valueText(pvCost.value, pvCost.unit)} ({ledgerCurrencyYearLabel(pvCost)})</span>}
          </label>
        )}
        <p className="text-[11px] text-muted">
          PV adds one option and one solve. Its output is a synthetic clear-sky profile, not measured.
          {hasRun ? ' Changing this after a run means running the study again.' : ''}
        </p>
        {error && <Refusal error={error} />}
      </Card>
      <Card title="What the battery costs">
        <ul className="flex flex-col gap-1.5">
          {COST_KEYS.map(k => ledgerRows.find(r => r.key === k)).filter((r): r is LedgerRow => !!r).map(r => (
            <li key={r.key} data-testid={`cost-${r.key}`}>
              <strong>{r.label}</strong>: {valueText(r.value, r.unit)} {ledgerCurrencyYearLabel(r) && `(${ledgerCurrencyYearLabel(r)})`}
              {r.range && <> · low {valueText(r.range.low, r.unit)}, high {valueText(r.range.high, r.unit)}{r.range.source === 'assumed' ? ' (assumed ±30 %)' : ''}</>}
              <br /><span className="text-[10.5px] text-muted">Source: {r.source}{r.source_year ? ` (${r.source_year})` : ''} · {r.status === 'default' ? 'library default' : 'your value'}</span>
            </li>
          ))}
        </ul>
        <p className="text-[11px] text-muted">To price your supplier’s quote, enter it in Assumptions. The robustness check tests the low and high ends.</p>
      </Card>
    </div>
  )
}
