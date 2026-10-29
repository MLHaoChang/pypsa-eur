import { useQuery } from '@tanstack/react-query'
import { ioApi, type ResultsWorkbookView } from '../../api/io'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'

export interface WorkbookChoice { extra: string[]; all: boolean }
export const WORKBOOK_DEFAULT: WorkbookChoice = { extra: [], all: false }

/** The result tabs the "Results workbook (Excel)" export will hold (RW-TABS-1): the tabs of
 *  the cards switched on for the project are always in (ticked, locked); the user ticks more,
 *  or "All result tabs". The same list in Guided and Expert. */
export function ResultsWorkbookPicker({ value, onChange }: {
  value: WorkbookChoice; onChange: (v: WorkbookChoice) => void
}) {
  const currentProject = useUIStore(s => s.currentProject)
  const { data, isLoading, isError } = useQuery({
    queryKey: nk(currentProject, 'results', 'workbook-views'),
    queryFn: ioApi.resultsWorkbookViews,
  })
  if (isLoading) return <p className="text-[10px] text-muted">Loading result tabs…</p>
  if (isError || !data) return <p className="text-[10px] text-muted">The result tabs could not be listed. Download still works with your cards' tabs.</p>

  const cards = data.reduce<Map<string, { label: string; views: ResultsWorkbookView[] }>>((m, v) => {
    const g = m.get(v.card) ?? { label: v.card_label, views: [] }
    g.views.push(v); m.set(v.card, g)
    return m
  }, new Map())
  const toggle = (id: string) => onChange({
    ...value, extra: value.extra.includes(id) ? value.extra.filter(x => x !== id) : [...value.extra, id],
  })

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
              const locked = v.selected
              return (
                <label key={v.view_id} title={locked ? 'Included by your cards' : undefined}
                  className={`flex items-center gap-1.5 ${locked ? 'text-muted' : 'text-text cursor-pointer'}`}>
                  <input type="checkbox" className="accent-accent"
                    checked={locked || value.all || value.extra.includes(v.view_id)}
                    disabled={locked || value.all}
                    onChange={() => toggle(v.view_id)} />
                  {v.label}
                </label>
              )
            })}
          </div>
        ))}
      </div>
    </fieldset>
  )
}
