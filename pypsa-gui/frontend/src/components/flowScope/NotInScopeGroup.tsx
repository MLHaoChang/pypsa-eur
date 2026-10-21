// Expert Issues' "Not in scope (n)" (workspace-reconciliation §5.2, W4a): the
// flow items the scope leaves out (`in_scope: false`). Collapsed, at the bottom,
// and in no count or badge: an out-of-scope item never counts, is never to
// check and never blocks. Each row says which card brings it back, and adds it
// in place where that is possible.
import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import type { FlowItem, ScopeCardDef } from '../../api/flow'
import { useProjectFlow } from '../../hooks/useProjectFlow'
import { AddToScope } from './ScopeCards'
import { useFlowScope } from './useFlowScope'

/** The sub-option that brings a card back: the example's first available
 *  one, else the card's first available one. */
function bringBack(card: ScopeCardDef | undefined): string | null {
  if (!card) return null
  const available = card.options.filter(o => o.status === 'available').map(o => o.id)
  return available.find(o => card.intended.includes(o)) ?? available[0] ?? null
}

export function NotInScopeGroup() {
  const { flow } = useProjectFlow()
  const { registry } = useFlowScope()
  const [open, setOpen] = useState(false)
  const items: FlowItem[] = (flow?.sections ?? []).flatMap(s => s.items).filter(i => i.in_scope === false)
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
            const option = bringBack(card)
            return (
              <li key={`${it.code}-${it.target.entity ?? ''}-${i}`} data-testid="issues-not-in-scope-row"
                className="flex flex-col gap-1 px-4 py-2 text-[11.5px] text-muted">
                <span>{it.message}</span>
                {card && <span className="text-[10.5px]">Comes back with {card.label}.</span>}
                {card && option && (
                  <AddToScope card={card.id} option={option}
                    testId={`issues-not-in-scope-add-${card.id}`} />
                )}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
