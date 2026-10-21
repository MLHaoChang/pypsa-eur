// The flow items the scope leaves out (`in_scope: false`, workspace-
// reconciliation §5.2, W4a). They count nowhere: never to do, to check or a
// blocker. Expert Issues lists them collapsed at the bottom ("Not in scope
// (n)"); a Guided section says in one muted line that some exist. Each names
// the card that brings it back and adds it in place.
import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type { FlowItem, FlowSectionId, ScopeCardId } from '../../api/flow'
import { useProjectFlow } from '../../hooks/useProjectFlow'
import { AddCardToScope, cardNotOffered } from './ScopeCards'
import { useFlowScope } from './useFlowScope'

const outOfScope = (items: FlowItem[]) => items.filter(i => i.in_scope === false && !i.acknowledged)

export function NotInScopeGroup() {
  const { flow } = useProjectFlow()
  const { registry } = useFlowScope()
  const [open, setOpen] = useState(false)
  const items = outOfScope((flow?.sections ?? []).flatMap(s => s.items))
  if (items.length === 0) return null
  return (
    <section data-testid="issues-not-in-scope" className="border-t border-border/60">
      <button type="button" data-testid="issues-not-in-scope-toggle" aria-expanded={open}
        onClick={() => setOpen(o => !o)}
        className="flex w-full items-center gap-1.5 px-4 pt-3 pb-1.5 text-[9px] font-bold uppercase tracking-[0.14em] text-ink-600">
        <ChevronDown size={11} aria-hidden style={{ transform: open ? 'none' : 'rotate(-90deg)' }} />
        Not in scope <span className="font-mono text-muted">({items.length})</span>
      </button>
      {open && (
        <ul className="flex flex-col">
          {items.map((it, i) => {
            const card = registry?.cards.find(c => c.id === it.card)
            return (
              <li key={`${it.code}-${it.target.entity ?? ''}-${i}`} data-testid="issues-not-in-scope-row"
                className="flex flex-col items-start gap-1 px-4 py-2 text-[11.5px] text-muted">
                <span>{it.message}</span>
                {card && <AddCardToScope card={card.id} testId={`issues-not-in-scope-add-${card.id}`} />}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

/** Guided: one muted line under a section's items when the scope leaves some
 *  out. Not a count of work: they come back with their card. */
export function SectionNotInScope({ section }: { section: FlowSectionId }) {
  const { section: flowSection } = useProjectFlow()
  const { registry } = useFlowScope()
  const items = outOfScope(flowSection(section)?.items ?? [])
  if (items.length === 0 || !registry) return null
  const cards = [...new Set(items.map(i => i.card).filter((c): c is ScopeCardId => !!c))]
  // Only a card the site offers is promised back; the others say why.
  const addable = cards.filter(c => {
    const def = registry.cards.find(d => d.id === c)
    return def && cardNotOffered(def) === null
  })
  const names = addable.map(c => registry.cards.find(d => d.id === c)?.label ?? c)
  const n = items.length
  const are = `${n} item${n === 1 ? ' is' : 's are'}`
  // None of the cards can be added here: say why, promise nothing, no button.
  const why = cards.map(c => registry.cards.find(d => d.id === c))
    .map(d => (d ? cardNotOffered(d) : null)).find(r => r) ?? ''
  return (
    <div data-testid="section-not-in-scope" className="flex flex-wrap items-center gap-2 text-[11.5px] text-muted">
      <span>
        {names.length > 0
          ? `${are} not in scope: ${n === 1 ? 'it comes' : 'they come'} back if you add ${names.join(' or ')}.`
          : `${are} not in scope here${why ? `: ${why}` : '.'}`}
      </span>
      {addable.map(c => <AddCardToScope key={c} card={c} testId={`section-not-in-scope-add-${c}`} />)}
    </div>
  )
}
