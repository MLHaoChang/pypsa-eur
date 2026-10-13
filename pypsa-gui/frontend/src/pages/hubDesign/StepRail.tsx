// The hub-design progress rail (guided-mode spec §5.1, §5.4). Its five
// buttons are the `hub_design` tour's `reveal` controls, so each test id is a
// LITERAL `testId="…"` below: `test_guides.py` scans the source for literal
// `data-testid=` / `testId=` strings, and neither a template string nor a
// lookup table (`data-testid={IDS[s]}`) would be found (§5.1, review B3).
import { Check, Loader2 } from 'lucide-react'
import type { FlowState } from './flow'
import { blockedReason, railState } from './flow'
import type { HubStep } from './hubDesignStore'

const LABEL: Record<HubStep, string> = {
  start: 'Start', site: 'Site', goal: 'Goal', results: 'Results', improve: 'Improve',
}
const NUMBER: Record<HubStep, number> = { start: 1, site: 2, goal: 3, results: 4, improve: 5 }

interface RailProps {
  flow: FlowState
  step: HubStep
  isTemplate: boolean
  onPick: (s: HubStep) => void
}

function RailButton({ s, testId, flow, step, isTemplate, onPick }: RailProps & {
  s: HubStep; testId: string
}) {
  const state = railState(flow, s, step, isTemplate)
  const why = blockedReason(flow, s)
  const tone = state === 'current' ? 'border-accent bg-accent/10 text-accent font-semibold'
    : state === 'done' ? 'border-success/50 text-success'
      : state === 'blocked' ? 'border-border/60 text-muted/50 cursor-not-allowed'
        : 'border-border text-muted hover:border-accent hover:text-accent'
  return (
    <div className="flex items-center gap-1">
      {s !== 'start' && <span aria-hidden className="h-px w-4 bg-border" />}
      <button type="button" data-testid={testId} data-state={state}
        aria-current={state === 'current' ? 'step' : undefined}
        disabled={state === 'blocked'} title={why ?? undefined}
        onClick={() => onPick(s)}
        className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] ${tone}`}>
        <span className="font-mono text-[10px]">{NUMBER[s]}</span>
        {LABEL[s]}
        {state === 'done' && <Check size={11} />}
        {s === 'goal' && flow === 'running' && (
          <Loader2 size={11} className="animate-spin" data-testid="hub-rail-spinner" />
        )}
      </button>
    </div>
  )
}

export function StepRail(props: RailProps) {
  return (
    <nav data-testid="hub-rail" aria-label="Hub design steps"
      className="flex items-center gap-1 overflow-x-auto">
      <RailButton {...props} s="start" testId="hub-rail-step-start" />
      <RailButton {...props} s="site" testId="hub-rail-step-site" />
      <RailButton {...props} s="goal" testId="hub-rail-step-goal" />
      <RailButton {...props} s="results" testId="hub-rail-step-results" />
      <RailButton {...props} s="improve" testId="hub-rail-step-improve" />
    </nav>
  )
}
