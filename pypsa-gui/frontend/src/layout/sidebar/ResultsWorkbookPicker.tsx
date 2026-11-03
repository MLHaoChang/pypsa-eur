import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ioApi, type ResultsWorkbookRun, type ResultsWorkbookView } from '../../api/io'
import { resultsEvidenceApi, runFileRefusal } from '../../api/resultsEvidence'
import { TABS } from '../../pages/Results'
import { CARD_GROUPS } from '../../pages/results/groups'
import { runShort, runWhenPair, useRunStartedAt } from '../../pages/results/runWhen'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'

export interface WorkbookChoice { extra: string[]; all: boolean }
export const WORKBOOK_DEFAULT: WorkbookChoice = { extra: [], all: false }

export function useWorkbookViews(enabled = true) {
  const currentProject = useUIStore(s => s.currentProject)
  return useQuery({
    queryKey: nk(currentProject, 'results', 'workbook-views'),
    queryFn: ioApi.resultsWorkbookViews,
    select: d => d.views,
    enabled,
  })
}

/** The run the workbook holds (the same listing, so no second request). */
export function useWorkbookRun(enabled = true) {
  const currentProject = useUIStore(s => s.currentProject)
  return useQuery({
    queryKey: nk(currentProject, 'results', 'workbook-views'),
    queryFn: ioApi.resultsWorkbookViews,
    select: d => d.run,
    enabled,
  })
}

/** Which run the workbook holds and, while Results shows a past run, that it
 *  is not the one on screen, with that run's kept results to download in place
 *  (LC-03 past-run parity; the one Export chooser, SAVE-1). The download is the
 *  file the assistant's export_run_evidence writes for the same run and section. */
export function WorkbookRunNote({ run }: { run: ResultsWorkbookRun | null | undefined }) {
  const discussed = useUIStore(s => s.discussedRun)
  const pastStartedAt = useRunStartedAt(discussed?.runId)
  const [refused, setRefused] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Without the listing (it failed, as after access loss) the past run's link
  // stays, so a refused download says why in place.
  const past = discussed && discussed.runId !== run?.run_id ? discussed : null
  if (!run && !past) return null
  const [pastWhen, latestWhen] = runWhenPair(pastStartedAt, run?.started_at)
  const latest = run ? (latestWhen ? `from ${latestWhen}` : runShort(run.run_id)) : null
  const pastName = pastWhen ? `the run of ${pastWhen}` : past ? `past ${runShort(past.runId)}` : ''
  const download = async () => {
    if (!past) return
    setBusy(true); setRefused(null)
    try {
      const { blob, filename } = await resultsEvidenceApi.runFile(past.runId, past.section)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = filename; a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setRefused(await runFileRefusal(e, id => TABS.find(t => t.id === id)?.label))
    } finally { setBusy(false) }
  }
  return (
    <div className="mb-1.5" data-testid="workbook-run" data-run-id={run?.run_id ?? ''}>
      {past ? (
        <>
          <p className="text-text" data-testid="workbook-past-run" data-run-id={past.runId}>
            <span title={`Run ${past.runId}`}>Results shows {pastName}</span>{run ? <>: this workbook holds the latest
            run, <span title={`Run ${run.run_id}`}>{latest}</span>.</> : '.'}
          </p>
          <button type="button" data-testid="workbook-past-run-download" disabled={busy}
            data-run-id={past.runId} data-section={past.section ?? ''}
            onClick={() => { void download() }}
            className="text-accent hover:underline disabled:opacity-50">
            Download the kept results of {pastName} (JSON)
          </button>
          {refused && <p role="status" className="mt-1 text-warn" data-testid="workbook-past-run-refused">{refused}</p>}
        </>
      ) : run && (
        <p className="text-muted">Holds the latest run, <span title={`Run ${run.run_id}`}>{latest}</span>.</p>
      )}
    </div>
  )
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
  const { data: run } = useWorkbookRun()
  if (isLoading) return <p className="mt-2 ml-7 text-[10px] text-muted">Loading result tabs…</p>
  if (isError || !data) return (
    <div className="mt-2 ml-7 text-[10px]">
      <WorkbookRunNote run={null} />
      <p className="text-muted">The result tabs could not be listed. Download still works with your cards' tabs.</p>
    </div>
  )

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
      <WorkbookRunNote run={run} />
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
