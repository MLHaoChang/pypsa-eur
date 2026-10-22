import type { ReactNode } from 'react'
import { FlowStateDot } from '../../components/FlowStateDot'
import { ScopeChips } from '../../components/flowScope/ScopeChips'
import { FLOW_STATE_WORDS, type FlowSectionId } from '../../api/flow'
import { useProjectFlow } from '../../hooks/useProjectFlow'
import { NextSectionButton } from '../../components/NextSectionButton'
import { useUIStore } from '../../store/uiStore'
import { PANEL_SECTION, sectionMeta } from '../flowSections'
import { openSectionInExpert } from '../flowNavigation'

/** One flow section in the Expert sidebar: its number, name and state dot
 *  (the same state the Guided rail shows, from the same /flow read), then the
 *  rows that open the panels it holds. The header is a label: navigation is
 *  the rows. Sections 9 and 10 carry no state of their own; they show their
 *  one-line summary instead. */
export function FlowSectionBlock({ id, children }: { id: FlowSectionId; children?: ReactNode }) {
  const meta = sectionMeta(id)
  const { section, isError } = useProjectFlow()
  const s = section(id)
  const stateless = id === 'readiness' || id === 'results'
  const state = s?.state ?? null
  const active = useUIStore(u => u.activeSection === id)
  const panel = useUIStore(u => u.activeSlidePanel)
  // The next step for a section worked on the canvas: complete, active, and no
  // full-page panel of it open (that panel's header carries the one button).
  const showNext = !stateless && state === 'complete' && active && !(panel && PANEL_SECTION[panel] === id)
  const word = stateless ? (s?.summary ?? '') : isError ? 'could not be read' : FLOW_STATE_WORDS[state ?? 'unknown']
  return (
    <div data-testid={`flow-section-${id}`} data-state={stateless ? undefined : (state ?? 'unknown')}>
      <div className="flex items-center gap-2 pl-4 pr-3 pt-2 pb-0.5" title={`${meta.label}: ${word}${s?.summary && !stateless ? ` — ${s.summary}` : ''}`}>
        <span className="font-mono text-[9px] text-ink-400 w-3 text-right shrink-0">{meta.number}</span>
        <span className="flex-1 min-w-0 truncate text-[10px] font-semibold uppercase tracking-[0.06em] text-ink-600">
          {meta.label}
        </span>
        {!stateless && <FlowStateDot state={state} testId={`flow-dot-${id}`} />}
      </div>
      {/* The cards in scope it holds, under the label so it is never squeezed. */}
      {!stateless && <ScopeChips section={id} className="flex flex-wrap gap-0.5 pl-9 pr-3 pb-0.5" />}
      {children}
      {showNext && (
        <div className="pl-9 pr-3 py-1">
          <NextSectionButton from={id} onNext={openSectionInExpert} testId={`sidebar-next-${id}`} />
        </div>
      )}
    </div>
  )
}
