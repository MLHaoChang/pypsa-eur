// Finance (spec §3 screen 5, plan S8): MVP-1 has one labelled convention
// (real, pre-tax, without subsidy; site owner), shown as fixed labels with
// their source. The discount rate is a ledger row, edited in Assumptions.
import type { DecisionStudy, LedgerRow } from '../../api/decisionStudies'
import { BASIS_SENTENCE, ROW_MEANING } from '../../utils/decisionVocabulary'
import { valueText } from './decisionModel'
import { Card } from './DecisionUi'

export default function FinanceStep({ study, ledgerRows }: { study: DecisionStudy; ledgerRows: LedgerRow[] }) {
  const rate = ledgerRows.find(r => r.key === 'discount_rate')
  const life = ledgerRows.find(r => r.key === 'battery_storage_lifetime_years')
  return (
    <Card title="How the money is counted">
      <dl data-testid="finance" className="grid grid-cols-[12rem_1fr] gap-x-3 gap-y-2">
        <dt className="text-muted">Discount rate</dt>
        <dd>{rate ? <>{valueText(rate.value, rate.unit)} — {ROW_MEANING.discount_rate} <span className="text-[10.5px] text-muted">Source: {rate.source}{rate.source_year ? ` (${rate.source_year})` : ''}. Change it in Assumptions.</span></> : '—'}</dd>
        <dt className="text-muted">Horizon</dt>
        <dd>{life ? <>{valueText(life.value, life.unit)}: the battery storage lifetime; the inverter is replaced at the end of its own lifetime. <span className="text-[10.5px] text-muted">Source: {life.source}</span></> : '—'}</dd>
        <dt className="text-muted">Basis</dt>
        <dd>{BASIS_SENTENCE} (the IRENA convention). Other bases are not in this version.</dd>
        <dt className="text-muted">Perspective</dt>
        <dd>The site owner’s: savings on your own bill. Other perspectives are not in this version.</dd>
        <dt className="text-muted">Currency year</dt>
        <dd>{study.currency_year != null ? `EUR of ${study.currency_year}` : 'not stated'} — every money figure is stated in it.</dd>
      </dl>
    </Card>
  )
}
