// What a workbook would create, in the workbook's own sheet words
// (RAW-NAMES-1): rows per sheet, what was left out and assumed, warnings,
// suggestions, the costs worked out again and what a workbook never carries.
// Long lists fold after five behind "(N more) Show all". No hash is shown.
import { useState, type ReactNode } from 'react'
import type { WorkbookContents } from '../../api/workbook'
import { HEADINGS, SHOWN_AT_FIRST, assumedValue } from './workbookWords'

function Fold<T>({ items, testId, row }: { items: T[]; testId: string; row: (item: T, i: number) => ReactNode }) {
  const [open, setOpen] = useState(false)
  const folded = items.length > SHOWN_AT_FIRST && !open
  return (
    <>
      <ul className="flex flex-col gap-0.5" data-testid={testId}>
        {(folded ? items.slice(0, SHOWN_AT_FIRST) : items).map((it, i) => <li key={i}>{row(it, i)}</li>)}
      </ul>
      {items.length > SHOWN_AT_FIRST && (
        <button type="button" data-testid={`${testId}-toggle`} aria-expanded={open}
          className="self-start text-text" onClick={() => setOpen(o => !o)}>
          {open
            ? <span className="underline">Show fewer</span>
            : <><span className="text-muted">({items.length - SHOWN_AT_FIRST} more)</span> <span className="underline">Show all</span></>}
        </button>
      )}
    </>
  )
}

function Section({ title, testId, children }: { title: string; testId: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1" data-testid={testId}>
      <h3 className="text-[10px] font-bold uppercase tracking-wider text-muted">{title}</h3>
      {children}
    </section>
  )
}

/** A model-file preview has no workbook-only parts: no re-derived costs, no "not carried" list. */
type Shown = Pick<WorkbookContents, 'report'> & Partial<Pick<WorkbookContents, 'rederived' | 'not_carried'>>

export function WorkbookReport({ contents, headings = HEADINGS, testId = 'workbook-report' }: {
  contents: Shown
  headings?: Record<keyof typeof HEADINGS, string>
  testId?: string
}) {
  const { report, rederived = [], not_carried: notCarried = [] } = contents
  const H = headings
  const counts = Object.entries(report.counts).filter(([, n]) => n > 0)
  return (
    <div className="flex flex-col gap-3 text-[11px]" data-testid={testId}>
      <Section title={H.holds} testId="workbook-counts">
        <p className="text-text">
          {counts.map(([sheet, n], i) => (
            <span key={sheet}>{i > 0 && ' · '}{sheet} {n.toLocaleString('en')}</span>
          ))}
        </p>
      </Section>
      {report.unmapped.length > 0 && (
        <Section title={`${H.unmapped} (${report.unmapped.length})`} testId="workbook-unmapped">
          <Fold items={report.unmapped} testId="workbook-unmapped-list"
            row={u => <><span className="text-text">{u.source_item}</span><span className="text-muted">: {u.reason}</span></>} />
        </Section>
      )}
      {report.assumed.length > 0 && (
        <Section title={`${H.assumed} (${report.assumed.length})`} testId="workbook-assumed">
          <Fold items={report.assumed} testId="workbook-assumed-list"
            row={a => (
              <span className="text-muted">
                <span className="text-text">{a.asset}</span> · {a.parameter} · {assumedValue(a.parameter, a.value)} · {a.why}
              </span>
            )} />
        </Section>
      )}
      {report.warnings.length > 0 && (
        <Section title={H.warnings} testId="workbook-warnings">
          <Fold items={report.warnings} testId="workbook-warnings-list" row={w => <span className="text-warn">{w}</span>} />
        </Section>
      )}
      {report.suggestions.length > 0 && (
        <Section title={H.suggestions} testId="workbook-suggestions">
          <Fold items={report.suggestions} testId="workbook-suggestions-list"
            row={s => <span className="text-muted"><span className="text-text">{s.target}</span> · {s.parameter}: {s.why}</span>} />
        </Section>
      )}
      {rederived.length > 0 && (
        <Section title={H.rederived} testId="workbook-rederived">
          <span className="text-muted">Worked out from their cost parts.</span>
          <Fold items={rederived} testId="workbook-rederived-list" row={r => <span className="text-text">{r}</span>} />
        </Section>
      )}
      {notCarried.length > 0 && (
        <Section title={H.notCarried} testId="workbook-not-carried">
          <Fold items={notCarried} testId="workbook-not-carried-list" row={t => <span className="text-muted">{t}</span>} />
        </Section>
      )}
    </div>
  )
}
