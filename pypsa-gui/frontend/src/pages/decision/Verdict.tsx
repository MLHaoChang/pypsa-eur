// Verdict (answer first; spec §3 screen 8, plan S8): the class and its one
// sentence, three KPIs against the grid-only baseline with their basis and
// currency year, the top drivers, the maturity, the main caveat, and every
// disclosure in words — the by-construction note on NPV/IRR/payback among
// them. A null KPI reads "not established", never 0 (ADR-0001). With
// `available: false` the options the run never reached sit beside the class.
import type { Figure, Findings, LedgerRow, StudyMaturity } from '../../api/decisionStudies'
import {
  ENGINE_LABELS, MATURITY_SENTENCE, OPTION_LABELS, VERDICT_LABELS, VERDICT_NOT_ESTABLISHED, helpFor,
} from '../../utils/decisionVocabulary'
import { figureBasisParts, figureText, maturityLabel, renderSentence, verdictTone } from './decisionModel'
import { Banner, Card, CodeList } from './DecisionUi'

const TONE_CLS = {
  good: 'bg-success/10 text-success border-success/40',
  caution: 'bg-warn/10 text-warn border-warn/40',
  bad: 'bg-danger/10 text-danger border-danger/40',
  unknown: 'bg-panel text-muted border-border',
} as const

function Kpi({ fig }: { fig: Figure }) {
  const parts = figureBasisParts(fig)
  const against = fig.engine === 'cash_flow_expander' ? 'against the grid-only baseline'
    : fig.engine === 'lp' ? 'sized by the optimisation' : ENGINE_LABELS[fig.engine]
  return (
    <div data-testid="verdict-kpi" className="rounded-[10px] border border-border bg-bg-2 px-4 py-3 flex flex-col gap-1">
      <div className="text-[9px] font-bold uppercase tracking-[0.14em] text-muted">{fig.label}</div>
      <div className="font-mono text-[18px] font-semibold text-text">{figureText(fig)}</div>
      <div className="text-[10.5px] text-muted">{[against, ...parts].join(' · ')}</div>
      {fig.value == null && fig.unavailable && (
        <div className="text-[10.5px] text-muted">{helpFor(fig.unavailable).text}</div>
      )}
    </div>
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
      {v.sentence_template === 'recommended_among_judged' && (
        <Banner tone="warn" testId="verdict-among-judged" title="This recommendation covers only the options the study could judge.">
          <span>A battery pays here, but not every option was judged, so a different size may be better. Run the robustness check to judge the rest.</span>
        </Banner>
      )}

      {v.headline_kpis.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-3">
          {v.headline_kpis.map(k => <Kpi key={k.key} fig={k} />)}
        </div>
      )}

      {v.disclosures.length > 0 && (
        <Card title="Read these before the numbers">
          <CodeList codes={v.disclosures} tariffHelp={tariffHelp} testId="verdict-disclosures" />
        </Card>
      )}

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
