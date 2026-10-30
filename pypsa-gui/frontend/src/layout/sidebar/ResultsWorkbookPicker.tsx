import { useQuery } from '@tanstack/react-query'
import { ioApi, type ResultsWorkbookView } from '../../api/io'
import { TABS } from '../../pages/Results'
import { CARD_GROUPS } from '../../pages/results/groups'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'

export interface WorkbookChoice { extra: string[]; all: boolean }
export const WORKBOOK_DEFAULT: WorkbookChoice = { extra: [], all: false }

export function useWorkbookViews(enabled = true) {
  const currentProject = useUIStore(s => s.currentProject)
  return useQuery({
    queryKey: nk(currentProject, 'results', 'workbook-views'),
    queryFn: ioApi.resultsWorkbookViews,
    enabled,
  })
}

// The Results panel's own words: a tab's label and a card's heading.
const tabLabel = (v: ResultsWorkbookView) => TABS.find(t => t.id === v.view_id)?.label ?? v.label
const cardLabel = (v: ResultsWorkbookView) => CARD_GROUPS.find(g => g.id === v.card)?.label ?? v.card_label

/** Why Download is off, in words, or null when the workbook holds at least one tab. */
export function workbookBlock(views: ResultsWorkbookView[] | undefined, choice: WorkbookChoice): string | null {
  if (!views) return null  // still loading, or the list failed: the route decides at download
  if (!views.some(v => v.available)) return 'No results yet: run the model, then export.'
  const included = views.some(v => v.available && (v.selected || choice.all || choice.extra.includes(v.view_id)))
  return included ? null : 'Tick at least one result tab.'
}

/** The result tabs the "Results workbook (Excel)" export will hold (RW-TABS-1): the tabs of
 *  the cards switched on for the project are always in (ticked, locked); the user ticks more,
 *  or "All result tabs". A tab with no data in this solve is shown off, with its reason.
 *  The same list in Guided and Expert. */
export function ResultsWorkbookPicker({ value, onChange }: {
  value: WorkbookChoice; onChange: (v: WorkbookChoice) => void
}) {
  const { data, isLoading, isError } = useWorkbookViews()
  if (isLoading) return <p className="mt-2 ml-7 text-[10px] text-muted">Loading result tabs…</p>
  if (isError || !data) return <p className="mt-2 ml-7 text-[10px] text-muted">The result tabs could not be listed. Download still works with your cards' tabs.</p>

  const cards = data.reduce<Map<string, { label: string; views: ResultsWorkbookView[] }>>((m, v) => {
    const g = m.get(v.card) ?? { label: cardLabel(v), views: [] }
    g.views.push(v); m.set(v.card, g)
    return m
  }, new Map())
  const toggle = (id: string) => onChange({
    ...value, extra: value.extra.includes(id) ? value.extra.filter(x => x !== id) : [...value.extra, id],
  })
  const block = workbookBlock(data, value)

  return (
    <fieldset className="mt-2 ml-7 text-[10px]" data-testid="workbook-picker">
      <legend className="sr-only">Result tabs in the workbook</legend>
      <p className="text-muted mb-1.5">
        One sheet per tab, plus Overview. Your cards' tabs are included; tick more if you need them.
      </p>
      <label className="flex items-center gap-2 mb-1.5 font-semibold text-text cursor-pointer">
        <input type="checkbox" className="accent-accent" checked={value.all}
          onChange={e => onChange({ ...value, all: e.target.checked })} />
        All result tabs
      </label>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1.5">
        {[...cards.entries()].map(([id, g]) => (
          <div key={id}>
            <p className="font-semibold text-muted uppercase tracking-wide text-[9px]">{g.label}</p>
            {g.views.map(v => {
              const locked = v.available && (v.selected || value.all)
              return (
                <label key={v.view_id}
                  title={!v.available ? v.reason ?? undefined : v.selected ? 'Included by your cards' : undefined}
                  className={`flex flex-col ${v.available && !locked ? 'text-text cursor-pointer' : 'text-muted'}`}>
                  <span className="flex items-center gap-1.5">
                    <input type="checkbox" className="accent-accent"
                      checked={v.available && (v.selected || value.all || value.extra.includes(v.view_id))}
                      disabled={!v.available || locked}
                      onChange={() => toggle(v.view_id)} />
                    {tabLabel(v)}
                  </span>
                  {!v.available && <span className="ml-5 text-[9px]">{v.reason}</span>}
                </label>
              )
            })}
          </div>
        ))}
      </div>
      {block && <p role="status" className="mt-2 text-warn" data-testid="workbook-block">{block}</p>}
    </fieldset>
  )
}
