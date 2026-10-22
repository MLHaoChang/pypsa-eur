// The Guided progress rail: the ten flow sections (workspace-reconciliation
// §4, design §3.4), each with its state from `/flow`. Five buttons are the
// `hub_design` tour's `reveal` controls (start, site, goal, results, improve),
// so each test id is a LITERAL `testId="…"` below: `test_guides.py` scans the
// source for literal `data-testid=` / `testId=` strings, and neither a
// template string nor a lookup table would be found. The other five sections
// carry `hub-rail-section-*` ids.
import { Loader2 } from 'lucide-react'
import { FlowStateDot } from '../../components/FlowStateDot'
import { ScopeChips, useScopeChips } from '../../components/flowScope/ScopeChips'
import { countOpen, countWords } from '../../utils/flowItems'
import type { FlowSectionId, FlowSectionState } from '../../api/flow'
import { FLOW_STATE_WORDS } from '../../api/flow'
import { useProjectFlow } from '../../hooks/useProjectFlow'
import { sectionMeta } from '../../layout/flowSections'
import type { FlowState } from './flow'
import { BLOCKED_NO_PROJECT, BLOCKED_NO_STUDY } from './flow'

/** Navigation state of a rail button, with the flow's state folded in:
 *  `done` is a complete section, `warning` a section with something to check. */
export type RailButtonState = 'current' | 'blocked' | 'done' | 'warning' | 'todo' | 'not_needed'

interface RailProps {
  flow: FlowState
  /** The section shown. */
  section: FlowSectionId
  /** The Improve sub-step is the one shown (it belongs to section 10). */
  improveShown: boolean
  onPick: (s: FlowSectionId) => void
  onPickImprove: () => void
}

/** Why a section cannot be opened, or null when it can. With no project only
 *  the first section opens; Improve needs a study's findings. */
export function sectionBlockedReason(flow: FlowState, id: FlowSectionId): string | null {
  if (flow === 'no_project') return id === 'project' ? null : BLOCKED_NO_PROJECT
  return null
}

function buttonState(
  id: FlowSectionId, props: RailProps, state: FlowSectionState | null,
): RailButtonState {
  if (sectionBlockedReason(props.flow, id)) return 'blocked'
  if (id === props.section && !(id === 'results' && props.improveShown)) return 'current'
  if (state === 'complete') return 'done'
  if (state === 'warning') return 'warning'
  if (state === 'not_needed') return 'not_needed'
  return 'todo'
}

function RailButton({ id, testId, ...props }: RailProps & { id: FlowSectionId; testId: string }) {
  const { section, isError } = useProjectFlow()
  const meta = sectionMeta(id)
  const flowSection = section(id)
  // Sections 9 and 10 carry no state of their own (design §3.2).
  const state = id === 'readiness' || id === 'results' ? null : flowSection?.state ?? null
  const bs = buttonState(id, props, state)
  const why = sectionBlockedReason(props.flow, id)
  const tone = bs === 'current' ? 'border-accent bg-accent/10 text-accent font-semibold'
    : bs === 'done' ? 'border-success/50 text-success'
      : bs === 'warning' ? 'border-warn/60 text-warn'
        : bs === 'blocked' ? 'border-border/60 text-muted/50 cursor-not-allowed'
          : bs === 'not_needed' ? 'border-border/60 text-muted/70'
            : 'border-border text-muted hover:border-accent hover:text-accent'
  const stateless = id === 'readiness' || id === 'results'
  const counts = countWords(countOpen(flowSection?.items ?? []))
  const stateWord = stateless ? null : isError || !flowSection ? FLOW_STATE_WORDS.unknown
    : counts || FLOW_STATE_WORDS[state ?? 'unknown']
  // The cards in scope whose blocks this section holds (W4a chips).
  const chips = useScopeChips(id)
  const label = `${meta.label}${stateWord ? `: ${stateWord}` : ''}${chips.length ? ` · ${chips.join(', ')}` : ''}`
  return (
    <div className="flex items-center gap-1">
      {id !== 'project' && <span aria-hidden className="h-px w-3 bg-border" />}
      <button type="button" data-testid={testId} data-state={bs}
        aria-current={bs === 'current' ? 'step' : undefined}
        disabled={bs === 'blocked'}
        title={why ?? label} aria-label={why ? undefined : label}
        onClick={() => props.onPick(id)}
        className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-[11px] ${tone}`}>
        <span className="font-mono text-[10px]">{meta.number}</span>
        {/* Narrow windows keep the number and dot; the label stays on the
            current pill and in every pill's title. */}
        <span className={bs === 'current' ? '' : 'hidden min-[900px]:inline'}>{meta.label}</span>
        {bs === 'current' && <ScopeChips section={id} testId="hub-rail-scope-chips" />}
        {!stateless && bs !== 'blocked' && <FlowStateDot state={isError ? null : state} />}
        {id === 'scenarios' && props.flow === 'running' && (
          <Loader2 size={11} className="animate-spin" data-testid="hub-rail-spinner" />
        )}
      </button>
    </div>
  )
}

/** Improve is Results' second step: the findings and what to try next. */
function ImproveButton(props: RailProps) {
  const why = props.flow === 'no_project' ? BLOCKED_NO_PROJECT
    : props.flow === 'no_study' || props.flow === 'running' ? BLOCKED_NO_STUDY : null
  const state: RailButtonState = why ? 'blocked' : props.improveShown ? 'current' : 'todo'
  return (
    <button type="button" data-testid="hub-rail-step-improve" data-state={state}
      aria-current={state === 'current' ? 'step' : undefined}
      disabled={state === 'blocked'} title={why ?? 'Improve: what to try next'}
      onClick={props.onPickImprove}
      className={`ml-1 inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2.5 py-1 text-[11px] ${
        state === 'current' ? 'border-accent bg-accent/10 text-accent font-semibold'
          : state === 'blocked' ? 'border-border/60 text-muted/50 cursor-not-allowed'
            : 'border-border text-muted hover:border-accent hover:text-accent'}`}>
      ↳ Improve
    </button>
  )
}

export function StepRail(props: RailProps) {
  return (
    <nav data-testid="hub-rail" aria-label="Project flow sections"
      className="flex items-center gap-1 overflow-x-auto pb-1">
      <RailButton {...props} id="project" testId="hub-rail-step-start" />
      <RailButton {...props} id="carriers" testId="hub-rail-section-carriers" />
      <RailButton {...props} id="connection" testId="hub-rail-step-site" />
      <RailButton {...props} id="loads" testId="hub-rail-section-loads" />
      <RailButton {...props} id="assets" testId="hub-rail-section-assets" />
      <RailButton {...props} id="profiles" testId="hub-rail-section-profiles" />
      <RailButton {...props} id="commercial" testId="hub-rail-section-commercial" />
      <RailButton {...props} id="scenarios" testId="hub-rail-step-goal" />
      <RailButton {...props} id="readiness" testId="hub-rail-section-readiness" />
      <RailButton {...props} id="results" testId="hub-rail-step-results" />
      <ImproveButton {...props} />
    </nav>
  )
}
