// Verdict (answer first; spec §3 screen 8, plan S8): the class and its one
// sentence, three KPIs against the grid-only baseline with their basis and
// currency year, the top drivers, the maturity, the main caveat, and every
// disclosure in words — the by-construction note on NPV/IRR/payback among
// them. A null KPI reads "not established", never 0 (ADR-0001). With
// `available: false` the options the run never reached sit beside the class.
import type { Figure, Findings, LedgerRow, StudyMaturity } from '../../api/decisionStudies'
import {
  ENGINE_LABELS, GLOSSARY, HELP, KPI_LABELS, MATURITY_SENTENCE, OPTION_LABELS, PV_VERDICT, UI_LABELS,
  VERDICT_LABELS, VERDICT_NOT_ESTABLISHED, helpFor,
} from '../../utils/decisionVocabulary'
import {
  figureBasisParts, figureText, hoursText, maturityLabel, pvVerdictContext, renderSentence, valueText, verdictTone,
} from './decisionModel'
import { Banner, Card, CodeList } from './DecisionUi'

const TONE_CLS = {
  good: 'bg-success/10 text-success border-success/40',
  caution: 'bg-warn/10 text-warn border-warn/40',
  bad: 'bg-danger/10 text-danger border-danger/40',
  unknown: 'bg-panel text-muted border-border',
} as const

// Which glossary entry explains a headline KPI.
const KPI_GLOSSARY: Record<string, keyof typeof GLOSSARY> = {
  battery_npv: 'npv', battery_payback_simple: 'payback',
}

function Explain({ entry }: { entry: keyof typeof GLOSSARY }) {
  const g = GLOSSARY[entry]
  return (
    <details className="text-[10.5px] text-muted">
      <summary className="cursor-pointer">What is {g.term.toLowerCase().startsWith('the ') ? g.term.slice(4) : g.term}?</summary>
      <span>{g.text}</span>
    </details>
  )
}

function Kpi({ fig, byConstruction }: { fig: Figure; byConstruction: boolean }) {
  const parts = figureBasisParts(fig)
  const against = fig.engine === 'cash_flow_expander' || fig.engine === 'finance_engine'
    ? 'against the grid-only baseline'
    : fig.engine === 'lp' ? 'sized by the optimisation' : ENGINE_LABELS[fig.engine]
  const plain = KPI_LABELS[fig.key]
  const gloss = KPI_GLOSSARY[fig.key]
  return (
    <div data-testid="verdict-kpi" className="rounded-[10px] border border-border bg-bg-2 px-4 py-3 flex flex-col gap-1"
      title={gloss ? GLOSSARY[gloss].text : undefined}>
      <div className="text-[10px] font-bold uppercase tracking-[0.1em] text-muted">{plain?.label ?? fig.label}</div>
      {plain && <div className="font-mono text-[10px] text-muted">{plain.technical}</div>}
      <div className="font-mono text-[18px] font-semibold text-text">{figureText(fig)}</div>
      <div className="text-[10.5px] text-muted">{[against, ...parts].join(' · ')}</div>
      {fig.value == null && fig.unavailable && (
        <div className="text-[10.5px] text-muted">{helpFor(fig.unavailable).text}</div>
      )}
      {byConstruction && (
        <div data-testid="kpi-by-construction" className="text-[10.5px] text-warn">{HELP.npv_nonnegative_at_optimum_by_construction}</div>
      )}
      {gloss && <Explain entry={gloss} />}
    </div>
  )
}

function PvVerdict({ findings }: { findings: Findings }) {
  const ctx = pvVerdictContext(findings)
  if (!ctx) return null
  const money = (v: number | null, cy: number | null) =>
    `${valueText(v, 'EUR')}${v != null ? (cy != null ? ` (EUR of ${cy})` : ' (currency year not stated)') : ''}`
  return (
    <Card title={PV_VERDICT.title}>
      <div data-testid="verdict-pv" className="flex flex-col gap-1.5">
        <p>{PV_VERDICT.total(money(ctx.optionNpv, ctx.currencyYear))}</p>
        <p>{ctx.best
          ? PV_VERDICT.best(valueText(ctx.best.powerMw, 'MW'), ctx.best.hours != null ? hoursText(ctx.best.hours) : 'unknown hours',
            money(ctx.best.npv, ctx.best.currencyYear))
          : PV_VERDICT.none}</p>
        <p className="text-[11px] text-muted">{GLOSSARY.pv_only.text}</p>
      </div>
    </Card>
  )
}

export default function Verdict({ findings, maturity, ledgerRows, tariffHelp }: {
  findings: Findings
  maturity: StudyMaturity | null
  ledgerRows: LedgerRow[]
  tariffHelp?: Record<string, string> | null
}) {
  const v = findings.verdict
  const tone = verdictTone(v, findings.available)
  const established = v.status === 'ok' && v.class != null
  const labelOf = (key: string) => ledgerRows.find(r => r.key === key)?.label ?? key
  const pending = findings.available ? [] : findings.pending_options
  return (
    <div className="flex flex-col gap-4">
      <div data-testid="verdict-head" className="flex flex-wrap items-center gap-3">
        <span data-testid="verdict-class" className={`rounded-full border px-3 py-1 text-[13px] font-semibold ${TONE_CLS[tone]}`}>
          {established ? VERDICT_LABELS[v.class!] : VERDICT_NOT_ESTABLISHED}
        </span>
        {!findings.available && (
          <span data-testid="verdict-pending" className="text-[12px] text-muted">
            {pending.length
              ? <>Not reached by the run: {pending.map(id => OPTION_LABELS[id] ?? id).join(', ')}</>
              : 'The run did not solve every option.'}
          </span>
        )}
        <span data-testid="verdict-maturity" className="ml-auto text-[11px] text-muted" title={maturity?.class ? MATURITY_SENTENCE[maturity.class] : undefined}>
          Maturity: <strong className="text-text">{maturityLabel(maturity)}</strong>
          {maturity?.accuracy_band && ` (indicative accuracy ${maturity.accuracy_band.low_pct} % to +${maturity.accuracy_band.high_pct} %)`}
        </span>
      </div>

      {v.sentence && (
        <p data-testid="verdict-sentence" className="text-[14px] leading-6 text-text">{renderSentence(v.sentence, v.facts)}</p>
      )}
      {established && (
        <details data-testid="verdict-bounds" className="text-[11px] text-muted">
          <summary className="cursor-pointer">What do “at the centre” and “tornado bound” mean?</summary>
          <span>{GLOSSARY.tornado.text}</span>
        </details>
      )}
      {v.sentence_template === 'recommended_among_judged' && (
        <Banner tone="warn" testId="verdict-among-judged" title="This recommendation covers only the options the study could judge.">
          <span>A battery pays here, but not every option was judged, so a different size may be better. Run the robustness check to judge an option it has not valued yet; an option the run did not reach needs a new run.</span>
        </Banner>
      )}

      {v.disclosures.length > 0 && (
        <Card title={UI_LABELS.readBefore}>
          <CodeList codes={v.disclosures} tariffHelp={tariffHelp} testId="verdict-disclosures" />
        </Card>
      )}

      {v.headline_kpis.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-3">
          {v.headline_kpis.map(k => (
            <Kpi key={k.key} fig={k}
              byConstruction={k.key === 'battery_npv' && k.value != null
                && v.disclosures.includes('npv_nonnegative_at_optimum_by_construction')} />
          ))}
        </div>
      )}

      <PvVerdict findings={findings} />

      {v.reasons.length > 0 && (
        <Card title={established ? 'What the verdict could not judge' : 'Why there is no verdict yet'}>
          <CodeList codes={v.reasons} tariffHelp={tariffHelp} testId="verdict-reasons" />
        </Card>
      )}

      {v.drivers.length > 0 && (
        <Card title="What drives the result most">
          <ol data-testid="verdict-drivers" className="list-decimal pl-5">
            {v.drivers.map(d => <li key={d}>{labelOf(d)}</li>)}
          </ol>
        </Card>
      )}

      {v.main_caveat && (
        <Banner tone="info" testId="verdict-caveat" title="The caveat that matters most">
          <span>{helpFor(v.main_caveat, tariffHelp).text}</span>
        </Banner>
      )}
    </div>
  )
}
