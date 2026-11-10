import { useEffect, useState } from 'react'
import type { FlowHeld, FlowSectionId } from '../../api/flow'
import { openFlowTargetInPlace, openSectionInExpert, openTimeSeriesFor, withReturnFrom } from '../../layout/flowNavigation'
import { FOLD_AFTER, groupTitle, heldFigure, heldGroups, heldHome, heldKind, heldRest } from './holdWords'

// What a Guided section holds (ui-modern 05, finding 7): one row per carrier,
// load, asset or time series, with one "Edit ›" that opens the row's editor
// over the card. Read-only: the list is the flow's `holds`, so its length is
// the count in the section's heading.

function edit(section: FlowSectionId, h: FlowHeld): void {
  const home = heldHome(section, h)
  if (home.kind === 'series') openTimeSeriesFor(home.type, home.name, { field: home.field })
  else if (home.kind === 'drawer') openFlowTargetInPlace(home.target)
  // No Guided editor: Expert, and "Back to <section>" brings the card back.
  else withReturnFrom(() => openSectionInExpert(section))
}

function Row({ section, h }: { section: FlowSectionId; h: FlowHeld }) {
  const figure = heldFigure(section, h)
  return (
    <li data-testid="section-held" className="flex items-baseline gap-2 py-0.5 text-[12px] text-text">
      <span className="min-w-0 flex-1 truncate" title={h.entity}>{h.entity}</span>
      <span className="shrink-0 text-muted">{heldKind(h)}</span>
      {figure && <span className="shrink-0 text-muted">{figure}</span>}
      <button type="button" data-testid="section-held-edit"
        aria-label={`Edit ${h.entity}${section === 'profiles' && figure ? `, ${figure}` : ''}`}
        onClick={() => edit(section, h)}
        className="shrink-0 text-[11px] text-muted hover:text-accent">
        Edit ›
      </button>
    </li>
  )
}

function Group({ section, title, rows }: { section: FlowSectionId; title: string | null; rows: FlowHeld[] }) {
  const [all, setAll] = useState(false)
  const shown = all || rows.length <= FOLD_AFTER ? rows : rows.slice(0, FOLD_AFTER)
  const more = rows.length - shown.length
  return (
    <div data-testid="section-held-group">
      {title && <p className="pb-0.5 text-[11px] font-medium text-muted">{title} ({rows.length})</p>}
      <ul className="flex flex-col divide-y divide-border">
        {shown.map(h => <Row key={`${h.component}:${h.entity}:${h.field ?? ''}`} section={section} h={h} />)}
      </ul>
      {more > 0 && (
        <button type="button" data-testid="section-held-show-all" onClick={() => setAll(true)}
          className="mt-1 text-[11px] text-muted hover:text-accent">
          ({more} more) Show all
        </button>
      )}
    </div>
  )
}

/** A placeholder the size of a short list, shown only when the read takes a moment. */
function Loading() {
  const [show, setShow] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setShow(true), 200)
    return () => clearTimeout(t)
  }, [])
  return (
    <div data-testid="section-held-loading" role="status" aria-busy="true" aria-label="Loading what this section holds"
      className="flex min-h-[4.5rem] flex-col gap-2">
      {show && [0, 1, 2].map(i => <div key={i} className="h-4 animate-pulse rounded bg-border motion-reduce:animate-none" />)}
    </div>
  )
}

export function SectionHoldings({ id, holds, total, pending, failed, deferred = false }: {
  id: FlowSectionId; holds: FlowHeld[] | undefined; total?: number; pending: boolean; failed: boolean
  deferred?: boolean
}) {
  // A failed refetch keeps the last good list on screen.
  if (failed && !holds) {
    return <p data-testid="section-held-failed" className="text-[12px] text-muted">
      What this section holds could not be read. Its editor is still in Expert.
    </p>
  }
  // A deferred flow (a run holds the project) carries no list until the check runs again.
  if (!pending && !holds && deferred) {
    return <p data-testid="section-held-deferred" className="text-[12px] text-muted">
      The list shows again once the project check has run.
    </p>
  }
  if (pending || !holds) return <Loading />
  if (holds.length === 0) {
    return <p data-testid="section-held-empty" className="text-[12px] text-muted">Nothing here yet.</p>
  }
  const groups = heldGroups(holds)
  const rest = heldRest(holds.length, total)
  return (
    <div data-testid="section-held-list" className="flex flex-col gap-3">
      {groups.map(g => (
        <Group key={g.component} section={id} rows={g.rows}
          title={groups.length > 1 ? groupTitle(g.component) : null} />
      ))}
      {rest && <p data-testid="section-held-rest" className="text-[11px] text-muted">{rest}</p>}
    </div>
  )
}
