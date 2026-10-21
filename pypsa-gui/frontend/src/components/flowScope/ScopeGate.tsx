// A block a card's sub-option owns, shown only while that sub-option is in
// scope (W4a: deselected means not parameterised). Out of scope it gives way to
// "Outside scope. Add <sub-option>", which adds it in place and brings the saved
// values back; where the site does not offer it, it says why instead. While the
// scope is unread the block shows, so it never flickers away.
import type { ReactNode } from 'react'
import type { ScopeCardId } from '../../api/flow'
import { AddToScope } from './ScopeCards'
import { useFlowScope, useInScope } from './useFlowScope'

export function ScopeGate({ card, option, testId, children }: {
  card: ScopeCardId
  option: string
  testId: string
  children: ReactNode
}) {
  const on = useInScope(card, option)
  const { registry } = useFlowScope()
  if (on) return <>{children}</>
  const opt = registry?.cards.find(c => c.id === card)?.options.find(o => o.id === option)
  if (opt && opt.status !== 'available') {
    return (
      <p data-testid={`${testId}-why`} className="text-[12px] text-muted">
        {opt.reason ? `Not offered for this site: ${opt.reason}` : 'Not offered for this site.'}
      </p>
    )
  }
  return <AddToScope card={card} option={option} testId={testId} />
}
