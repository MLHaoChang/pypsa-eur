// The cards in scope whose blocks a flow section holds, as chips on the
// section's row: the Expert sidebar header and the Guided rail (workspace-
// reconciliation §6.1, W3a). Which card holds which section comes from the
// server's registry (`GET …/flow/scope`); nothing renders while it is unread.
import type { FlowSectionId } from '../../api/flow'
import { inScope } from '../../api/flow'
import { useFlowScope } from './useFlowScope'

/** The chips of a section, in card order: the short name and the card's. */
export function useScopeChipCards(section: FlowSectionId): { chip: string, label: string }[] {
  const { registry, scope } = useFlowScope()
  if (!registry || !scope) return []
  return registry.cards
    .filter(c => c.chip && c.sections.includes(section) && inScope(scope, c.id))
    .map(c => ({ chip: c.chip as string, label: c.label }))
}

/** The chip labels for a section, in card order. */
export function useScopeChips(section: FlowSectionId): string[] {
  return useScopeChipCards(section).map(c => c.chip)
}

export function ScopeChips({ section, testId, className = 'inline-flex shrink-0 gap-0.5' }: {
  section: FlowSectionId
  testId?: string
  className?: string
}) {
  const chips = useScopeChipCards(section)
  if (chips.length === 0) return null
  return (
    <span data-testid={testId ?? `scope-chips-${section}`} className={className}>
      {chips.map(c => (
        <span key={c.chip} title={c.label}
          className="rounded bg-accent/10 px-1 text-[9px] font-medium normal-case tracking-normal text-accent">
          {c.chip}
        </span>
      ))}
    </span>
  )
}
