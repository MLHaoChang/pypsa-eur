// Small shared pieces of the decision study's pages. Words come from
// `utils/decisionVocabulary.ts`; these only lay them out.
import type { ReactNode } from 'react'
import { AlertTriangle, Info } from 'lucide-react'
import type { StudyError } from '../../api/decisionStudies'
import { CHIP_LABELS, errorCopy, helpFor, type ChipState } from '../../utils/decisionVocabulary'

const CHIP_TONE: Record<ChipState, string> = {
  done: 'bg-success/10 text-success border-success/30',
  customised: 'bg-success/10 text-success border-success/30',
  using_defaults: 'bg-panel text-muted border-border',
  not_started: 'bg-panel text-muted border-border',
  needs_attention: 'bg-warn/10 text-warn border-warn/30',
  rerun_needed: 'bg-warn/10 text-warn border-warn/30',
  stale: 'bg-warn/10 text-warn border-warn/30',
  running: 'bg-accent/10 text-accent border-accent/30',
  not_established: 'bg-panel text-muted border-border',
  unavailable: 'bg-panel text-muted border-border',
}

export function Chip({ state, label }: { state: ChipState; label?: string }) {
  return (
    <span data-chip={state}
      className={`inline-flex items-center rounded-full border px-2 py-px text-[10.5px] font-medium ${CHIP_TONE[state]}`}>
      {label ?? CHIP_LABELS[state]}
    </span>
  )
}

export function Card({ title, right, children }: { title: ReactNode; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-[10px] border border-border bg-bg overflow-hidden">
      <header className="flex items-center justify-between gap-3 px-4 py-2.5 border-b border-border bg-bg-2">
        <h3 className="text-[12.5px] font-semibold text-text">{title}</h3>
        {right}
      </header>
      <div className="p-4 flex flex-col gap-2.5 text-[12px] text-text">{children}</div>
    </section>
  )
}

export function Banner({ tone = 'info', title, children, testId }: {
  tone?: 'info' | 'warn' | 'danger'
  title: ReactNode
  children?: ReactNode
  testId?: string
}) {
  const cls = tone === 'warn' ? 'bg-warn/10 border-warn/30 text-warn'
    : tone === 'danger' ? 'bg-danger/10 border-danger/30 text-danger'
    : 'bg-panel border-border text-text'
  const Icon = tone === 'info' ? Info : AlertTriangle
  return (
    <div role={tone === 'info' ? 'note' : 'alert'} data-testid={testId}
      className={`flex items-start gap-2 rounded border px-3 py-2 text-[12px] ${cls}`}>
      <Icon size={13} className="shrink-0 mt-0.5" aria-hidden="true" />
      <div className="flex flex-col gap-1 text-text">
        <strong className="font-semibold">{title}</strong>
        {children}
      </div>
    </div>
  )
}

/** A typed refusal in words: what happened, the next action, and the backend's own message. */
export function Refusal({ error, testId }: { error: StudyError; testId?: string }) {
  const copy = errorCopy(error.code)
  return (
    <Banner tone="warn" title={copy.title} testId={testId ?? 'refusal'}>
      <span>{copy.action}</span>
      {error.message && <span className="text-[11px] text-muted">{error.message}</span>}
    </Banner>
  )
}

/** Codes with their human sentences (tariff's own first, then the study's). */
export function CodeList({ codes, tariffHelp, testId }: {
  codes: readonly string[]
  tariffHelp?: Record<string, string> | null
  testId?: string
}) {
  if (!codes.length) return null
  return (
    <ul className="flex flex-col gap-1.5" data-testid={testId}>
      {codes.map(code => {
        const h = helpFor(code, tariffHelp)
        return (
          <li key={code} data-code={code} className="flex flex-col">
            <span>{h.text}</span>
            <span className="font-mono text-[10px] text-muted">
              {code}{h.source === 'tariff' ? ' · from the tariff (its author’s wording, not checked by the study)' : ''}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

export function Button({ children, onClick, disabled, kind = 'secondary', title, testId }: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  kind?: 'primary' | 'secondary' | 'danger'
  title?: string
  testId?: string
}) {
  const cls = kind === 'primary' ? 'bg-accent text-white hover:bg-accent/90 border-accent'
    : kind === 'danger' ? 'border-danger/40 text-danger hover:bg-danger/10'
    : 'border-border text-text hover:border-accent hover:text-accent'
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title} data-testid={testId}
      className={`px-3 py-1.5 rounded border text-[12px] font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${cls}`}>
      {children}
    </button>
  )
}
