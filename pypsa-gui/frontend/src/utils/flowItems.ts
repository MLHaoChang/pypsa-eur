import type { FlowItem } from '../api/flow'
import { componentLabel } from './componentLabels'
import { sectionMeta } from '../layout/flowSections'

/** An item that counts: not acknowledged, in scope, and a to-do or to-check
 *  (not an info line). The one filter for the rail, the sidebar badge, the
 *  section view and the readiness count, so they show one number. */
export function isOpenItem(i: FlowItem): boolean {
  return !i.acknowledged && (i as { in_scope?: boolean }).in_scope !== false && i.severity !== 'info'
}

export function countOpen(items: FlowItem[]): { todo: number; check: number } {
  const open = items.filter(isOpenItem)
  return {
    todo: open.filter(i => i.severity === 'error').length,
    check: open.filter(i => i.severity === 'warning').length,
  }
}

/** "2 to do · 1 to check" (the rail's words); empty when nothing is open. */
export function countWords({ todo, check }: { todo: number; check: number }): string {
  return [todo > 0 && `${todo} to do`, check > 0 && `${check} to check`].filter(Boolean).join(' · ')
}

/** The field an item points at, in words: "Connection & grid > Link 'grid_import'". */
export function fieldWords(t: Pick<FlowItem['target'], 'section' | 'component' | 'entity'>): string | null {
  if (!t.component && !t.entity) return null
  const thing = [t.component ? componentLabel(t.component) : null, t.entity ? `'${t.entity}'` : null]
    .filter(Boolean).join(' ')
  return `${sectionMeta(t.section).label} > ${thing}`
}
