// The cards in scope whose blocks a flow section holds, as chips on the
// section's row: the Expert sidebar header and the Guided rail (workspace-
// reconciliation §6.1, W3a). Which card holds which section comes from the
// server's registry (`GET …/flow/scope`); nothing renders while it is unread.
import type { FlowSectionId } from '../../api/flow'
import { inScope } from '../../api/flow'
import { useFlowScope } from './useFlowScope'

/** The chip labels for a section, in card order. */
export function useScopeChips(section: FlowSectionId): string[] {
  const { registry, scope } = useFlowScope()
  if (!registry || !scope) return []
  return registry.cards
    .filter(c => c.chip && c.sections.includes(section) && inScope(scope, c.id))
    .map(c => c.chip as string)
}

export function ScopeChips({ section, testId }: { section: FlowSectionId; testId?: string }) {
  const chips = useScopeChips(section)
  if (chips.length === 0) return null
  return (
    <span data-testid={testId ?? `scope-chips-${section}`} className="inline-flex shrink-0 gap-0.5">
      {chips.map(c => (
        <span key={c} className="rounded bg-accent/10 px-1 text-[9px] font-medium normal-case tracking-normal text-accent">
          {c}
        </span>
      ))}
    </span>
  )
}
