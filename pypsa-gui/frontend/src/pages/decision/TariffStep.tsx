// Prices and tariffs (spec §3 screen 4, plan S8). The tariff picker lists the
// library's seed tariffs; the chosen tariff's own honesty notes are shown with
// the sentence behind each code (`Tariff.honesty_help`; gate S2: wherever the
// tariff's name appears). The baseline bill preview is the bill calculator on
// the load alone (grid only: the import IS the load), before any option.
import type { IntakePreview, StudyError, StudyIntake, StudyLibrary, Tariff } from '../../api/decisionStudies'
import { BASIS_SENTENCE, NOT_ESTABLISHED, errorCopy } from '../../utils/decisionVocabulary'
import { valueText } from './decisionModel'
import { Banner, Card, CodeList, Refusal } from './DecisionUi'

const COMPONENTS: Array<[Exclude<keyof IntakePreviewBillComponents, 'unavailable'>, string]> = [
  ['energy', 'Energy'], ['network', 'Network charges'], ['demand', 'Demand charge'],
  ['capacity', 'Capacity charge'], ['fixed', 'Fixed charges'], ['export_credit', 'Export credit'],
]
type IntakePreviewBillComponents = Extract<IntakePreview['bill'], { status: 'ok' }>['bill']['by_component']

function summary(t: Tariff): string {
  const parts = [`${t.energy_bands.length} energy band${t.energy_bands.length === 1 ? '' : 's'}`]
  if (t.demand_charge) parts.push(`demand charge ${t.demand_charge.price_per_mw_per_period} ${t.currency}/MW per ${t.billing_period}`)
  else parts.push('no demand charge')
  if (t.export.price_per_mwh != null) parts.push(`export ${t.export.price_per_mwh} ${t.currency}/MWh`)
  return parts.join(' · ')
}

export function chosenTariff(library: StudyLibrary | null | undefined, intake: StudyIntake): Tariff | null {
  if (intake.tariff?.custom) return intake.tariff.custom
  const id = intake.tariff?.tariff_id ?? library?.default_tariff_id
  return library?.tariffs.find(t => t.tariff_id === id) ?? null
}

export function BillPreview({ preview, error }: { preview: IntakePreview | null; error: StudyError | null }) {
  if (error) return <Refusal error={error} testId="bill-preview" />
  if (!preview) return <p data-testid="bill-preview" className="text-muted">Working out your bill today…</p>
  const b = preview.bill
  if (b.status !== 'ok') {
    const copy = errorCopy(b.error_kind)
    return <p data-testid="bill-preview">Your bill today is {NOT_ESTABLISHED}: {copy.title} {copy.action}</p>
  }
  const cur = b.bill.currency
  return (
    <div data-testid="bill-preview" className="flex flex-col gap-1">
      <p>
        Your bill today (grid only, before any option): <strong className="font-mono">{valueText(b.bill.annual_bill, `${cur}/yr`)}</strong>
        <span className="text-muted"> · {BASIS_SENTENCE}{b.bill.currency_year != null ? `, ${cur} of ${b.bill.currency_year}` : ''} · bill calculator, no optimisation</span>
      </p>
      <ul className="grid grid-cols-2 gap-x-4 text-[11.5px]">
        {COMPONENTS.map(([k, label]) => (
          <li key={k} className="flex justify-between"><span>{label}</span>
            <span className="font-mono">{valueText(b.bill.by_component[k], `${cur}/yr`)}</span></li>
        ))}
      </ul>
      <p className="text-[11px] text-muted">Priced on the tariff as published, before any change you make in Assumptions.</p>
    </div>
  )
}

export default function TariffStep({ library, intake, preview, previewError, onChoose, saving, error }: {
  library: StudyLibrary | null
  intake: StudyIntake
  preview: IntakePreview | null
  previewError: StudyError | null
  onChoose: (tariffId: string) => void
  saving: boolean
  error: StudyError | null
}) {
  const chosen = chosenTariff(library, intake)
  const custom = !!intake.tariff?.custom
  return (
    <div className="flex flex-col gap-4">
      <Card title="Your tariff">
        {custom && chosen && <p>Your own tariff: <strong>{chosen.name}</strong> ({summary(chosen)}).</p>}
        <fieldset className="flex flex-col gap-2" disabled={saving}>
          <legend className="text-muted mb-1">{custom ? 'Or choose a library tariff:' : 'Choose the tariff closest to your bill:'}</legend>
          {(library?.tariffs ?? []).map(t => (
            <label key={t.tariff_id} className="flex items-start gap-2">
              <input type="radio" name="tariff" checked={!custom && chosen?.tariff_id === t.tariff_id}
                onChange={() => onChoose(t.tariff_id)} aria-label={t.name} />
              <span><strong>{t.name}</strong><br /><span className="text-[11px] text-muted">{summary(t)} · source: {t.source}{t.source_year ? ` (${t.source_year})` : ''}</span></span>
            </label>
          ))}
        </fieldset>
        {!intake.tariff && (
          <Banner tone="warn" title="No tariff chosen yet: the library’s default is used.">
            <span>Choose the tariff closest to your own bill; the verdict is only as good as the prices.</span>
          </Banner>
        )}
        {error && <Refusal error={error} />}
      </Card>
      {chosen && chosen.honesty_notes.length > 0 && (
        <Card title={`About “${chosen.name}”`}>
          <CodeList codes={chosen.honesty_notes} tariffHelp={chosen.honesty_help} testId="tariff-notes" />
        </Card>
      )}
      <Card title="Your bill today">
        <BillPreview preview={preview} error={previewError} />
      </Card>
    </div>
  )
}
