import { X } from 'lucide-react'
import { openSectionInExpert } from '../layout/flowNavigation'
import { sectionMeta } from '../layout/flowSections'
import { fieldWords } from '../utils/flowItems'
import { useFieldLabels } from '../hooks/useFieldLabels'
import { useUIStore } from '../store/uiStore'
import { useGuideStore } from '../catalogue/guideStore'
import { GuidePanel } from '../catalogue/GuideDrawer'

/** Guided: a section item's field, opened in place over the card (design
 *  §3.5: Guided never sends the user out of their mode to finish a required
 *  input). The body is the editor the field already has: the component's own
 *  Properties, or the panel the item points at. `children` is that editor.
 *
 *  The Parameter guide opens here, in place of the editor, with "Back to <field>"
 *  (spec expert-catalogue §6.1: one drawer, never a second layer). The editor stays
 *  mounted, hidden, so unsaved input survives the visit. */
export function FieldDrawer({ children }: { children: (t: NonNullable<ReturnType<typeof useUIStore.getState>['fieldDrawer']>) => React.ReactNode }) {
  const target = useUIStore(s => s.fieldDrawer)
  const labels = useFieldLabels()
  const close = useUIStore(s => s.setFieldDrawer)
  const guideOpen = useGuideStore(s => s.view !== null)
  const closeGuide = useGuideStore(s => s.closeGuide)
  if (!target) return null
  const label = sectionMeta(target.section).label
  const fieldName = (target.field ? labels[target.field] : null) ?? target.entity ?? label
  return (
    <div data-testid="field-drawer" data-no-panel-close
      className="fixed bottom-0 right-0 top-11 z-[60] flex w-[min(520px,100%)] flex-col border-l border-border bg-bg shadow-xl">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border bg-bg-2 px-3">
        <span className="min-w-0 flex-1 truncate text-[12px] font-semibold text-text">
          {fieldWords(target, labels) ?? label}
        </span>
        <button type="button" data-testid="field-drawer-expert"
          onClick={() => openSectionInExpert(target.section)}
          className="text-[11px] text-muted hover:text-accent hover:underline">
          Open in Expert
        </button>
        <button type="button" data-testid="field-drawer-close" aria-label="Close" onClick={() => close(null)}
          className="text-muted hover:text-text"><X size={14} /></button>
      </div>
      <div hidden={guideOpen} className="flex min-h-0 flex-1 flex-col overflow-y-auto [&>aside]:!w-full">{children(target)}</div>
      {guideOpen && <GuidePanel backTo={{ label: `Back to ${fieldName}`, onBack: () => closeGuide() }} />}
    </div>
  )
}
