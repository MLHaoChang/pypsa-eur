// The decision study's question-first rail (U3; UX assessment §4.3 and §11
// "Guided shell"). In Guided the study is these seven steps, generated from
// the question; each opens one view of the panel and shows its state, read
// from the same section statuses the hub's chips use. The views the rail
// does not name (options, tariff, finance, why) stay one click away on the
// Question step's hub. Expert keeps the full section nav.
import { Check } from 'lucide-react'
import {
  RAIL_LABELS, RAIL_NAV_LABEL, type ChipState, type DecisionView, type HubSectionId, type RailStep,
} from '../../utils/decisionVocabulary'

export const RAIL: ReadonlyArray<{ step: RailStep; view: DecisionView; sections: HubSectionId[] }> = [
  { step: 'question', view: 'hub', sections: [] },
  { step: 'site', view: 'intake', sections: ['site', 'demand'] },
  { step: 'keyChoices', view: 'ledger', sections: ['assumptions'] },
  { step: 'run', view: 'run', sections: ['run'] },
  { step: 'verdict', view: 'verdict', sections: ['findings'] },
  { step: 'improve', view: 'robust', sections: [] },
  { step: 'report', view: 'report', sections: ['report'] },
]

// A section that is answered, by the user or by an accepted default.
const SETTLED = new Set<ChipState>(['done', 'using_defaults', 'customised'])

/** `done` when every section of the step is settled; `robust`: the tornado ran on this run. */
export function railState(step: RailStep, statuses: Record<HubSectionId, ChipState>,
  robust: boolean): 'done' | 'not_started' {
  if (step === 'question') return 'done'
  if (step === 'improve') return robust ? 'done' : 'not_started'
  const r = RAIL.find(x => x.step === step)!
  return r.sections.every(s => SETTLED.has(statuses[s])) ? 'done' : 'not_started'
}

export default function QuestionRail({ current, statuses, robust, onOpen }: {
  current: DecisionView
  statuses: Record<HubSectionId, ChipState>
  robust: boolean
  onOpen: (view: DecisionView) => void
}) {
  return (
    <nav aria-label={RAIL_NAV_LABEL} data-testid="decision-rail"
      className="flex items-center gap-1 overflow-x-auto px-5 py-1.5 border-b border-border">
      {RAIL.map(({ step, view }, i) => {
        const on = view === current
        const done = railState(step, statuses, robust) === 'done'
        return (
          <div key={step} className="flex items-center gap-1">
            {i > 0 && <span aria-hidden className="h-px w-3 bg-border" />}
            <button type="button" data-testid={`decision-rail-${step}`} aria-current={on ? 'step' : undefined}
              data-state={on ? 'current' : done ? 'done' : 'open'} onClick={() => onOpen(view)}
              className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] ${
                on ? 'border-accent bg-accent/10 text-accent font-semibold'
                  : done ? 'border-success/50 text-success' : 'border-border text-muted hover:border-accent hover:text-accent'}`}>
              <span className="font-mono text-[10px]">{i + 1}</span>
              {RAIL_LABELS[step]}
              {done && !on && <Check size={11} />}
            </button>
          </div>
        )
      })}
    </nav>
  )
}
