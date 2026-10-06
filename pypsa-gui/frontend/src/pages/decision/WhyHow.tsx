// "Why and how" (spec §3 screen 9, plan S8): the value-stream waterfall,
// labelled by what it is measured against (`value_streams_basis`: the full
// saving against the grid-only baseline, or the battery's increment over the
// same PV alone); the cumulative discounted cash flow with the payback and
// replacement years marked; the option table across durations and PV. The
// typical-week chart is not in MVP-1 (`typical_week_not_in_mvp1`): the
// option's own dispatch is one click away in the Expert view.
import type { Findings, InvestmentCase } from '../../api/decisionStudies'
import {
  BASIS_SENTENCE, GLOSSARY, HELP, OPTION_LABELS, STREAMS_BASIS_LABELS, STREAM_LABELS, NOT_ESTABLISHED, UI_LABELS, helpFor,
} from '../../utils/decisionVocabulary'
import { shownStreams, streamsBasis, valueText } from './decisionModel'
import { Card, CodeList } from './DecisionUi'

function Waterfall({ findings }: { findings: Findings }) {
  const basis = streamsBasis(findings)
  const streams = shownStreams(findings.value_streams)
  if (!basis) {
    const codes = findings.honesty_notes.filter(c => c.startsWith('value_streams_'))
    return (
      <Card title={UI_LABELS.valueStreams}>
        <div data-testid="waterfall">
          <p>{HELP.value_streams_not_established}</p>
          <CodeList codes={codes} />
        </div>
      </Card>
    )
  }
  const label = STREAMS_BASIS_LABELS[basis]
  const max = Math.max(1, ...streams.map(s => Math.abs(s.annual_value ?? 0)))
  const total = streams.every(s => s.annual_value != null)
    ? streams.reduce((a, s) => a + (s.annual_value ?? 0), 0) : null
  const cy = findings.options.find(o => o.option_id === findings.value_streams_option)?.currency_year ?? null
  const unit = 'EUR/yr'
  return (
    <Card title={UI_LABELS.valueStreams}>
      <div data-testid="waterfall" className="flex flex-col gap-2">
        <div>
          <strong>{label.title}</strong>
          {findings.value_streams_option && <> — {OPTION_LABELS[findings.value_streams_option] ?? findings.value_streams_option}</>}
          <p className="text-[11px] text-muted">{label.sentence} {BASIS_SENTENCE}{cy != null ? `, EUR of ${cy}` : ''}; from the bill calculator.</p>
        </div>
        <ul className="flex flex-col gap-1.5">
          {streams.map(s => {
            const v = s.annual_value
            const w = v == null ? 0 : Math.max(2, (Math.abs(v) / max) * 100)
            return (
              <li key={s.key ?? s.label} data-stream={s.key ?? s.label} className="grid grid-cols-[10rem_1fr_8rem] items-center gap-2">
                <span title={s.key === 'taxes_levies' ? GLOSSARY.taxes_levies.text : undefined}>{STREAM_LABELS[s.key ?? ''] ?? s.label}</span>
                <span className="h-3 rounded-sm bg-panel relative" aria-hidden="true">
                  <span className={`absolute inset-y-0 left-0 rounded-sm ${v != null && v < 0 ? 'bg-muted' : 'bg-accent'}`} style={{ width: `${w}%` }} />
                </span>
                <span className="font-mono text-right">{valueText(v, unit)}{s.share != null ? ` · ${Math.round(s.share * 100)} %` : ''}</span>
              </li>
            )
          })}
        </ul>
        <div className="text-right font-mono">Total: {valueText(total, unit)}</div>
      </div>
    </Card>
  )
}

function CashFlow({ c }: { c: InvestmentCase | null }) {
  if (!c || c.status !== 'ok' || !c.kpis || !c.years.length) {
    return (
      <Card title={UI_LABELS.cashFlow}>
        <p data-testid="cash-flow">The option’s investment case is {NOT_ESTABLISHED}, so there is no cash flow to show.</p>
      </Card>
    )
  }
  const W = 560, H = 160, P = 24
  const ys = c.years.map(y => y.cumulative_discounted)
  const lo = Math.min(0, ...ys), hi = Math.max(0, ...ys)
  const n = c.years.length - 1 || 1
  const x = (i: number) => P + (i / n) * (W - 2 * P)
  const y = (v: number) => H - P - ((v - lo) / (hi - lo || 1)) * (H - 2 * P)
  const pb = c.kpis.payback_discounted
  const replacements = c.years.filter(yr => yr.replacements > 0).map(yr => yr.year)
  return (
    <Card title={UI_LABELS.cashFlow}>
      <div data-testid="cash-flow" className="flex flex-col gap-1">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Cumulative discounted cash flow by year" className="w-full max-w-[560px]">
          <line x1={P} x2={W - P} y1={y(0)} y2={y(0)} stroke="var(--color-grid)" />
          <polyline fill="none" stroke="var(--color-accent)" strokeWidth={2}
            points={c.years.map((yr, i) => `${x(i)},${y(yr.cumulative_discounted)}`).join(' ')} />
          {pb != null && <line x1={x(pb)} x2={x(pb)} y1={P} y2={H - P} stroke="var(--color-text)" strokeDasharray="3 3" />}
          {replacements.map(r => <circle key={r} cx={x(r)} cy={y(c.years[r]?.cumulative_discounted ?? 0)} r={4} fill="var(--color-warn)" />)}
        </svg>
        <p className="text-[11px] text-muted">
          Discounted payback: {valueText(pb, 'years')} (dashed line)
          {replacements.length ? ` · inverter replacements in years ${replacements.join(', ')} (dots)` : ''}
          {' · '}{c.horizon_years} years at a {valueText(c.discount_rate, 'per unit')} real discount rate
          {c.currency_year != null ? ` · EUR of ${c.currency_year}` : ''}
        </p>
        <p>
          {GLOSSARY.irr.term}: IRR {c.kpis.irr != null ? `${Number((c.kpis.irr * 100).toFixed(1))} %` : NOT_ESTABLISHED}
          {' · '}{GLOSSARY.payback.term}: {valueText(c.kpis.payback_simple, 'years')}
        </p>
        <p className="text-[11px] text-muted">{GLOSSARY.irr.text} {GLOSSARY.payback.text}</p>
        <p className="text-[11px] text-warn">{HELP.irr_and_discounted_payback_bounded_at_optimum_by_construction}</p>
      </div>
    </Card>
  )
}

export default function WhyHow({ findings, optionCase, onExpert }: {
  findings: Findings
  optionCase: InvestmentCase | null
  onExpert: (optionId: string) => void
}) {
  const att = Object.fromEntries(findings.battery_attribution.map(a => [a.option_id, a]))
  const cy = findings.options.find(o => o.currency_year != null)?.currency_year ?? null
  // Gate S8 [S3]: the case's own assumptions (and the findings' that the case
  // does not repeat), each in its HELP sentence.
  const assumes = [...new Set([...(optionCase?.honesty_notes ?? []), ...findings.honesty_notes])]
  return (
    <div className="flex flex-col gap-4">
      <Waterfall findings={findings} />
      <CashFlow c={optionCase} />
      {assumes.length > 0 && (
        <Card title={UI_LABELS.caseAssumes}>
          <CodeList codes={assumes} testId="case-notes" />
        </Card>
      )}
      <Card title="The options compared">
        <table data-testid="option-table" className="w-full text-left text-[12px]">
          <thead className="text-[10.5px] text-muted">
            <tr><th>{UI_LABELS.colOption}</th><th>{UI_LABELS.colSolved}</th><th>{UI_LABELS.colBattery}</th>
              <th>{UI_LABELS.colBill}{cy != null ? ` (EUR of ${cy})` : ''}</th><th>{UI_LABELS.colBatteryNpv}</th>
              <th>{UI_LABELS.colOptionNpv}</th><th /></tr>
          </thead>
          <tbody>
            {findings.options.map(o => {
              const bat = o.sizes.find(s => s.asset === 'battery')
              const a = att[o.option_id]
              return (
                <tr key={o.option_id} data-option={o.option_id} className="border-t border-border">
                  <td>{OPTION_LABELS[o.option_id] ?? o.label}</td>
                  <td>{o.solve_status === 'ok' ? 'yes' : o.solve_status.replace('_', ' ')}</td>
                  <td className="font-mono">{bat ? `${valueText(bat.p_nom_opt, 'MW')} / ${valueText(bat.e_nom_opt, 'MWh')}` : '—'}</td>
                  <td className="font-mono">{valueText(o.bill, 'EUR/yr')}</td>
                  <td className="font-mono" title={a && a.battery_npv == null ? helpFor(a.unavailable.battery_npv ?? 'not_computed').text : undefined}>
                    {a ? valueText(a.battery_npv, 'EUR') : '—'}
                  </td>
                  <td className="font-mono">{a ? valueText(a.option_npv, 'EUR') : '—'}</td>
                  <td>
                    {o.solve_status === 'ok' && o.project_ref && (
                      <button type="button" className="text-accent hover:underline text-[11px]" onClick={() => onExpert(o.option_id)}>
                        {UI_LABELS.openInExpert}
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Card>
      <Card title="How the battery runs in a typical week">
        <p>{HELP.typical_week_not_in_mvp1} Open an option in the Expert view and go to Results → Dispatch to see how it runs.</p>
      </Card>
    </div>
  )
}
