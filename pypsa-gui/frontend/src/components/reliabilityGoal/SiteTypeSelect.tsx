// The site type (the archetype pack) every reliability study runs (W3b-R):
// one stored field, chosen on Guided's Site card and at the top of Expert's
// Connection & grid panel. A change saves at once, sending only the site type.
import type { ReactNode } from 'react'
import type { EhArchetype } from '../../api/simulation'
import { useReliabilityGoal } from '../../hooks/useReliabilityGoal'

export const SITE_TYPES: { id: EhArchetype; label: string }[] = [
  { id: 'strong_grid', label: 'Strong — import whenever needed' },
  { id: 'weak_flexible', label: 'Weak or limited' },
  { id: 'off_grid', label: 'None — the site runs as an island' },
]

export const siteTypeLabel = (a: EhArchetype): string =>
  SITE_TYPES.find(t => t.id === a)?.label ?? a

export function SiteTypeSelect({ mode, label, disabled = false, testId }: {
  mode: 'guided' | 'expert'
  label: ReactNode
  disabled?: boolean
  testId: string
}) {
  const { goal, archetype, save } = useReliabilityGoal()
  const source = goal?.site_archetype.source
  const guided = mode === 'guided'
  return (
    <label className={guided ? 'flex flex-col gap-1 text-[12px]' : 'flex flex-col gap-1'}>
      <span className={guided ? 'font-semibold text-text' : 'text-[10px] text-muted'}>{label}</span>
      <select data-testid={testId} value={archetype}
        disabled={disabled || !goal || save.isPending}
        onChange={e => save.mutate({ site_archetype: e.target.value as EhArchetype })}
        className={guided
          ? 'max-w-sm rounded border border-border bg-bg px-2 py-1 text-[12px] text-text'
          : 'max-w-sm px-2 py-1 border border-border rounded text-xs bg-bg focus:outline-none focus:border-accent'}>
        {SITE_TYPES.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
      </select>
      {source && source !== 'stored' && (
        <span data-testid={`${testId}-source`} className="text-[10px] text-muted">
          {source === 'template' ? "The project template's choice" : 'The default'}; choosing one saves it.
        </span>
      )}
      {save.isError && (
        <span data-testid={`${testId}-error`} className="text-[10px] text-warn">
          The site type was not saved. Try again.
        </span>
      )}
    </label>
  )
}
