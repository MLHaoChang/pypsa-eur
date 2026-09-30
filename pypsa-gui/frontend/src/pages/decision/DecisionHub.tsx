// The study hub (spec §3 screen 2, plan S8): every section with a status
// chip, the maturity badge (read from the ledger payload, the ONE maturity
// source), and the assumptions that matter most. "Using defaults" is a
// visible state, never silent.
import type { DecisionStudy, LedgerPayload } from '../../api/decisionStudies'
import {
  BASE_PROJECT_NOTE, HUB_SECTION_LABELS, MATURITY_SENTENCE, type ChipState, type DecisionView, type HubSectionId,
} from '../../utils/decisionVocabulary'
import { maturityLabel } from './decisionModel'
import { Banner, Card, Chip } from './DecisionUi'

const TARGET: Record<HubSectionId, DecisionView> = {
  site: 'intake', demand: 'intake', options: 'options', tariff: 'tariff', finance: 'finance',
  assumptions: 'ledger', run: 'run', findings: 'verdict', report: 'report',
}

export default function DecisionHub({ study, ledger, statuses, onOpen }: {
  study: DecisionStudy
  ledger: LedgerPayload | null
  statuses: Record<HubSectionId, ChipState>
  onOpen: (view: DecisionView) => void
}) {
  const m = ledger?.maturity ?? null
  const keyRows = ledger?.ledger.rows.filter(r => r.sensitivity_flag) ?? []
  return (
    <div className="flex flex-col gap-4">
      <Card title="Maturity" right={<span data-testid="hub-maturity" className="font-semibold">{maturityLabel(m)}</span>}>
        {m?.class && <p>{MATURITY_SENTENCE[m.class]}</p>}
        {m?.accuracy_band && (
          <p className="text-muted">Indicative accuracy {m.accuracy_band.low_pct} % to +{m.accuracy_band.high_pct} %
            {m.accuracy_band.reference ? ` (${m.accuracy_band.reference})` : ''}.</p>
        )}
        {keyRows.length > 0 && (
          <p>These {keyRows.length} assumptions matter most for this question:{' '}
            {keyRows.map(r => r.label).join('; ')}.{' '}
            <button type="button" className="text-accent hover:underline" onClick={() => onOpen('ledger')}>Check them</button>
          </p>
        )}
      </Card>
      <Card title="Your study">
        <ul data-testid="hub-sections" className="flex flex-col divide-y divide-border">
          {(Object.keys(HUB_SECTION_LABELS) as HubSectionId[]).map(id => (
            <li key={id} data-section={id} className="flex items-center justify-between py-1.5">
              <button type="button" className="text-left hover:text-accent" onClick={() => onOpen(TARGET[id])}>
                {HUB_SECTION_LABELS[id]}
              </button>
              <Chip state={statuses[id]} />
            </li>
          ))}
        </ul>
      </Card>
      {study.pack_project != null && <Banner title="About this study’s project">{BASE_PROJECT_NOTE}</Banner>}
    </div>
  )
}
