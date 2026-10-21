import type { ReactNode } from 'react'
import { AlertTriangle, ArrowRight, Info, XCircle } from 'lucide-react'
import { FlowStateDot } from '../../components/FlowStateDot'
import { FLOW_STATE_WORDS, type FlowItem, type FlowSectionId } from '../../api/flow'
import { useProjectFlow } from '../../hooks/useProjectFlow'
import { useRunGate } from '../../hooks/useRunGate'
import { useUIStore } from '../../store/uiStore'
import { openFlowTarget, openSectionInExpert } from '../../layout/flowNavigation'
import { sectionMeta } from '../../layout/flowSections'

// One flow section in Guided: its name and state, the items that are still to
// do or to check (a Guided user never has a blocker hidden: the field is named
// and one button shows it in Expert, design §3.5), then the section's card.

/** The field an item points at, in words ("Link grid_import · p_nom"). */
export function fieldWords(item: FlowItem): string | null {
  const { component, entity, field } = item.target
  const thing = [component, entity].filter(Boolean).join(' ')
  return [thing, field].filter(Boolean).join(' · ') || null
}

function ItemRow({ item }: { item: FlowItem }) {
  const Icon = item.severity === 'error' ? XCircle : item.severity === 'warning' ? AlertTriangle : Info
  const tone = item.severity === 'error' ? 'text-danger' : item.severity === 'warning' ? 'text-warn' : 'text-muted'
  const field = fieldWords(item)
  return (
    <li data-testid="section-item" data-severity={item.severity}
      className="flex items-start gap-2 text-[12px] text-text">
      <Icon size={13} className={`mt-0.5 shrink-0 ${tone}`} />
      <span className="flex-1">
        {item.message}
        {field && <span className="text-muted"> ({field})</span>}
      </span>
      <button type="button" data-testid="section-item-open"
        onClick={() => openFlowTarget(item.target)}
        title={`Edit ${field ?? 'this'} in Expert; switching back returns you here`}
        className="shrink-0 inline-flex items-center gap-1 text-[11px] text-accent hover:underline">
        Edit in Expert <ArrowRight size={11} />
      </button>
    </li>
  )
}

export function SectionView({ id, children }: { id: FlowSectionId; children?: ReactNode }) {
  const meta = sectionMeta(id)
  const { section, isError } = useProjectFlow()
  const s = section(id)
  const stateless = id === 'readiness' || id === 'results'
  const items = (s?.items ?? []).filter(i => !i.acknowledged)
  return (
    <div data-testid={`section-view-${id}`} className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[11px] text-muted">{meta.number}</span>
        <h2 className="text-[15px] font-semibold text-text">{meta.label}</h2>
        {!stateless && <FlowStateDot state={s?.state ?? null} testId={`section-state-${id}`} />}
        <span className="text-[12px] text-muted" data-testid="section-summary">
          {isError ? 'The flow could not be read from the server.'
            : stateless ? (s?.summary ?? '')
              : `${FLOW_STATE_WORDS[s?.state ?? 'unknown']}${s?.summary ? ` — ${s.summary}` : ''}`}
        </span>
      </div>
      {items.length > 0 && (
        <ul data-testid="section-items"
          className="flex flex-col gap-1.5 rounded border border-border bg-panel px-3 py-2">
          {items.map((it, i) => <ItemRow key={`${it.code}-${it.target.entity ?? ''}-${i}`} item={it} />)}
        </ul>
      )}
      {children}
    </div>
  )
}

/** The card of a section Guided has no bespoke card for yet: what Expert holds,
 *  and the one way there. Never a silent gap (design §3.5). */
export function GenericSectionCard({ id }: { id: FlowSectionId }) {
  const meta = sectionMeta(id)
  const { flow, section } = useProjectFlow()
  const setActiveSection = useUIStore(s => s.setActiveSection)
  const gate = useRunGate('all')
  if (id === 'readiness') {
    return (
      <div data-testid="hub-card-readiness" className="flex flex-col gap-2 rounded border border-border bg-panel p-4">
        {/* The same verdict the Run button uses: blocked only when the server
            would refuse, with the reason by section; gaps that do not block
            show as notes. */}
        {gate.blocked ? (
          <p data-testid="readiness-blocked" className="flex flex-wrap items-center gap-2 text-[12px] text-warn">
            Not ready to run: {gate.reason}
            {gate.blocker && (
              <button type="button" onClick={() => openFlowTarget(gate.blocker!.target)}
                className="inline-flex items-center gap-1 text-accent hover:underline">
                Edit in Expert <ArrowRight size={11} />
              </button>
            )}
          </p>
        ) : (
          <p data-testid="readiness-ready" className="text-[12px] text-text">
            {flow ? 'Ready to run.' : 'Not known yet: the readiness could not be read.'}
            {gate.notes.length > 0 && ` ${gate.notes.length} note${gate.notes.length === 1 ? '' : 's'}: ${gate.notes.map(n => n.message).join('; ')}`}
          </p>
        )}
        <p className="text-[12px] text-muted">
          Each section's state, in flow order. Open one to see what is left in it.
        </p>
        <ul className="flex flex-col gap-1">
          {(flow?.sections ?? []).filter(s => s.state !== null).map(s => (
            <li key={s.section} className="flex items-center gap-2 text-[12px]">
              <FlowStateDot state={s.state} />
              <button type="button" onClick={() => setActiveSection(s.section)}
                className="text-left text-text hover:text-accent">
                {sectionMeta(s.section).number} {s.label}
              </button>
              <span className="text-muted">
                {s.items.filter(i => !i.acknowledged).length > 0
                  ? `${s.items.filter(i => !i.acknowledged).length} open` : ''}
              </span>
            </li>
          ))}
        </ul>
      </div>
    )
  }
  const s = section(id)
  return (
    <div data-testid={`hub-card-${id}`} className="flex flex-col gap-3 rounded border border-border bg-panel p-4">
      <p className="text-[12px] text-muted">
        {s?.summary ? `${s.summary}. ` : ''}{meta.label} is set in Expert for now. Everything it needs
        is listed above when something is missing; you can also ask the assistant.
      </p>
      <div>
        <button type="button" data-testid={`section-open-expert-${id}`} onClick={() => openSectionInExpert(id)}
          className="inline-flex items-center gap-1 rounded border border-border px-2.5 py-1 text-[11px] text-muted hover:border-accent hover:text-accent">
          Edit {meta.label.toLowerCase()} in Expert <ArrowRight size={11} />
        </button>
      </div>
    </div>
  )
}
