// Assumptions review (spec §3 screen 6, plan S8; gate S2 carries): the ledger
// with the question's key drivers first, defaults visually distinct from
// customised values, each value with its source, year and plausible range.
// A money row states its currency year beside the input and sends it with an
// edit (an edit without one silently takes the row's). A flagged row names
// its reason (the `needs_attention:<key>:<reason>` note) and can be reset.
// PV rows are marked unused when PV is off. CSV export only (import is MVP-2).
// A driver the robustness check moved shows the range it was tested over, read
// from the findings' tornado rows, and a re-centred one is marked (plan F1-F,
// F5; gate S9: the report and the workbooks already show it).
//
// U3, owner decision 4 (the key-parameter list): the site's answers and the
// key drivers come first; every other row is collapsed as "from the generic
// defaults (<version>)", except a row the user changed or that needs a look,
// which stays in view.
import { useMemo, useState } from 'react'
import type { LedgerPayload, LedgerRow, StudyError, StudyIntake, TornadoRow } from '../../api/decisionStudies'
import {
  KEY_PARAMETER_LABELS as K, LEDGER_STATUS_LABELS, PROVENANCE_LABELS, ROW_MEANING, TESTED_RANGE_LABELS, helpFor, needsAttentionReason,
} from '../../utils/decisionVocabulary'
import { isMoneyUnit, isPvRowUnused, ledgerCurrencyYearLabel, parseNeedsAttention } from './decisionModel'
import { Button, Card, Chip, Refusal } from './DecisionUi'

export interface LedgerEditOut { key: string; value: number; unit: string; currency_year?: number }

const PERCENT = (r: LedgerRow) => r.unit === 'per unit'

function shown(r: LedgerRow): string {
  if (r.value == null) return ''
  return PERCENT(r) ? String(Number((r.value * 100).toFixed(4))) : String(r.value)
}

const RECENTRED = 'range_recentred_on_user_value'

function Row({ r, intake, attention, onSave, onReset, saving, tested }: {
  r: LedgerRow
  intake: StudyIntake
  attention: string | null
  tested: TornadoRow | null
  onSave: (e: LedgerEditOut[]) => void
  onReset: (keys: string[]) => void
  saving: boolean
}) {
  const [draft, setDraft] = useState<string>(shown(r))
  const [base, setBase] = useState<string>(shown(r))
  if (shown(r) !== base) { setBase(shown(r)); setDraft(shown(r)) }
  const unused = isPvRowUnused(r, intake)
  const cy = ledgerCurrencyYearLabel(r)
  const parsed = Number(draft)
  const changed = draft.trim() !== '' && Number.isFinite(parsed) && draft !== base
  const status = attention ? 'needs_attention' : r.status
  const save = () => {
    const value = PERCENT(r) ? parsed / 100 : parsed
    onSave([{ key: r.key, value, unit: r.unit, ...(isMoneyUnit(r.unit) && r.currency_year != null ? { currency_year: r.currency_year } : {}) }])
  }
  const meaning = ROW_MEANING[r.key] ?? r.help
  const readOnlyReason = r.value == null ? (r.unavailable.value ?? 'not_computed') : null
  return (
    <li data-testid={`ledger-row-${r.key}`} data-key={r.key}
      className={`rounded border px-3 py-2 flex flex-col gap-1 ${unused ? 'border-dashed border-border opacity-70' : 'border-border'}`}>
      <div className="flex flex-wrap items-center gap-2">
        <strong>{r.label}</strong>
        <span data-testid="ledger-status">
          <Chip state={status === 'default' ? 'using_defaults' : status === 'customised' ? 'customised' : 'needs_attention'}
            label={LEDGER_STATUS_LABELS[status]} />
        </span>
        <span className="text-[10.5px] text-muted">{PROVENANCE_LABELS[r.provenance]}</span>
        {r.sensitivity_flag && <span className="text-[10.5px] text-accent">matters most</span>}
      </div>
      {r.technical_name && <span className="font-mono text-[10px] text-muted">{r.technical_name}</span>}
      {meaning && <p className="text-[11.5px] text-muted">{meaning}</p>}
      {unused ? (
        <p data-testid="ledger-unused" className="text-[11.5px]">
          Not used: {intake.pv?.enabled ? `the study uses ${intake.pv.kind ?? 'rooftop'} PV` : 'PV is off'}.
        </p>
      ) : readOnlyReason ? (
        <p className="text-[11.5px]">{readOnlyReason === 'tariff_descriptor'
          ? 'The tariff this study prices with (change it in Prices and tariffs).'
          : helpFor(readOnlyReason).text}</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <input type="number" aria-label={r.label} value={draft} onChange={e => setDraft(e.target.value)}
            className="w-32 px-2 py-1 border border-border rounded font-mono text-[12px]" />
          <span className="text-muted">{PERCENT(r) ? '%' : r.unit}</span>
          {cy && <span data-testid="ledger-currency-year" className="text-[11px] text-muted">{cy}</span>}
          <Button onClick={save} disabled={!changed || saving}>Save</Button>
          {(r.status !== 'default' || attention) && (
            <Button onClick={() => onReset([r.key])} disabled={saving}>Reset to the library value</Button>
          )}
        </div>
      )}
      {attention && (
        <div className="flex flex-wrap items-center gap-2">
          <p data-testid="ledger-attention" className="text-[11.5px] text-warn">{needsAttentionReason(attention)}</p>
          {(unused || readOnlyReason) && <Button onClick={() => onReset([r.key])} disabled={saving}>Reset to the library value</Button>}
        </div>
      )}
      <p className="text-[10.5px] text-muted">
        Source: {r.source}{r.source_year != null ? ` (${r.source_year})` : ''}
        {r.range && <> · plausible range {shownRange(r, r.range.low, r.range.high)} ({r.range.source === 'assumed' ? 'assumed ±30 %' : 'from the source'})</>}
      </p>
      {tested && (
        <p data-testid="ledger-tested-range" className="text-[10.5px] text-muted">
          {TESTED_RANGE_LABELS.tested}: {shownRange(r, tested.low_value, tested.high_value)}
          {tested.notes.includes(RECENTRED) && (
            <> <span data-testid="ledger-recentred" className="font-medium text-warn">{TESTED_RANGE_LABELS.recentred}</span>
              {' '}{helpFor(RECENTRED).text}</>
          )}
        </p>
      )}
    </li>
  )
}

function shownRange(r: LedgerRow, low: number, high: number): string {
  const f = (v: number) => (PERCENT(r) ? `${Number((v * 100).toFixed(2))} %` : `${Number(v.toFixed(4))} ${r.unit}`)
  return `${f(low)} to ${f(high)}`
}

export default function LedgerReview({ payload, intake, onSave, onReset, saving, error, csvUrl, tested = [] }: {
  payload: LedgerPayload
  intake: StudyIntake
  onSave: (edits: LedgerEditOut[]) => void
  onReset: (keys: string[]) => void
  saving: boolean
  error: StudyError | null
  csvUrl: string
  /** The last tornado's rows (`findings.robustness.tornado`): what it tested. */
  tested?: TornadoRow[]
}) {
  const rows = payload.ledger.rows
  const attention = useMemo(() => {
    const m = new Map<string, string>()
    for (const n of parseNeedsAttention(payload.ledger.honesty_notes)) m.set(n.key, n.reason)
    for (const r of rows) if (r.status === 'needs_attention' && !m.has(r.key)) m.set(r.key, 'needs_attention')
    return m
  }, [payload, rows])
  const keyRows = rows.filter(r => r.sensitivity_flag)
  const others = rows.filter(r => !r.sensitivity_flag)
  const changed = others.filter(r => r.status !== 'default' || attention.has(r.key))
  const defaults = others.filter(r => !changed.includes(r))
  const keyCount = keyRows.length
  const rowOf = (r: LedgerRow) => (
    <Row key={r.key} r={r} intake={intake} attention={attention.get(r.key) ?? null}
      onSave={onSave} onReset={onReset} saving={saving}
      tested={tested.find(t => t.key === r.key) ?? null} />
  )
  return (
    <Card title="Your assumptions" right={<a href={csvUrl} download className="text-accent text-[11px] hover:underline">Download as CSV</a>}>
      <SiteAnswers intake={intake} />
      <p data-testid="ledger-key-count">
        These {keyCount} assumptions matter most for this question: please check them. Values marked
        “Default” come from the library ({payload.ledger.ledger_version}); your own values are marked “Customised”.
      </p>
      {payload.ledger.honesty_notes.filter(n => !n.startsWith('needs_attention:')).map(n => (
        <p key={n} className="text-[11px] text-muted">{helpFor(n).text}</p>
      ))}
      {error && <Refusal error={error} />}
      <ul className="flex flex-col gap-2">{keyRows.map(rowOf)}</ul>
      {changed.length > 0 && (
        <section data-testid="ledger-changed" className="flex flex-col gap-2">
          <h4 className="text-[12px] font-semibold">{K.changedHeading}</h4>
          <ul className="flex flex-col gap-2">{changed.map(rowOf)}</ul>
        </section>
      )}
      {defaults.length > 0 && (
        <details data-testid="ledger-defaults" className="flex flex-col gap-2">
          <summary data-testid="ledger-defaults-summary" className="cursor-pointer text-[12px] text-muted">
            {K.defaultsSummary(defaults.length, payload.ledger.ledger_version)}
          </summary>
          <ul className="flex flex-col gap-2 pt-2">{defaults.map(rowOf)}</ul>
        </details>
      )}
    </Card>
  )
}

/** The intake answers the question is sensitive to (decision 4's first two groups). */
function SiteAnswers({ intake }: { intake: StudyIntake }) {
  const site = intake.site ?? {}
  const load = intake.load ?? {}
  const loadText = load.source === 'upload'
    ? K.loadUpload(load.filename ?? 'uploaded')
    : load.profile ? K.loadProfile(load.profile, typeof load.annual_mwh === 'number' ? load.annual_mwh : null)
      : K.notAnswered
  const t = intake.tariff
  const tariffText = t?.custom?.name ?? t?.tariff_id ?? K.tariffDefault
  const pv = intake.pv?.enabled ? K.pvOn(intake.pv.kind ?? 'rooftop') : K.pvOff
  const items: Array<[string, string, string]> = [
    ['zone', K.zone, site.zone ?? K.notAnswered],
    ['connection', K.connection, typeof site.connection_mw === 'number' ? `${site.connection_mw} MW` : K.notAnswered],
    ['load', K.load, loadText],
    ['tariff', K.tariff, tariffText],
    ['pv', K.pv, pv],
  ]
  return (
    <section data-testid="ledger-site-answers" className="flex flex-col gap-1">
      <h4 className="text-[12px] font-semibold">{K.siteHeading}</h4>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 text-[12px]">
        {items.map(([id, label, value]) => (
          <div key={id} data-testid={`ledger-site-${id}`} className="contents">
            <dt className="text-muted">{label}</dt><dd>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
