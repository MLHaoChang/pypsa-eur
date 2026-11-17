// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

/**
 * The one "source run" line under a Results headline, in both modes (control-strategy walkthrough
 * improvement 2, issue 06, #1618-01): which run the figures above come from, when it ran, whether
 * it is out of date, and, when the other mode's run is missing, that in words with one link.
 */
import { StatePill } from './ResultState'

export type SourceRun = 'dispatch' | 'study'

const RUN_NAME: Record<SourceRun, string> = {
  dispatch: 'the dispatch solve',
  study: 'the reliability study',
}

/** "2026-11-17 15:42" in local time, from an ISO string or epoch seconds (the study records time.time());
 *  null for a missing or unreadable stamp. */
export function runStamp(at: string | number | null | undefined): string | null {
  if (at == null || at === '') return null
  const d = new Date(typeof at === 'number' ? at * 1000 : at)
  if (Number.isNaN(d.getTime())) return null
  const p = (v: number) => String(v).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export interface SourceRunLineProps {
  /** The run the figures above come from; null when neither has run. */
  source: SourceRun | null
  at?: string | number | null
  outOfDate?: boolean
  /** The other run, when it has not run: named in words, with one link to it. */
  missing?: { run: SourceRun; action?: { label: string; onClick: () => void } }
  testId?: string
}

export default function SourceRunLine({ source, at, outOfDate = false, missing, testId = 'source-run' }: SourceRunLineProps) {
  const stamp = runStamp(at)
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted" data-testid={testId}>
      {source && <span data-testid={`${testId}-from`}>From {RUN_NAME[source]}{stamp ? `, ${stamp}` : ''}</span>}
      {source && outOfDate && <span data-testid={`${testId}-stale`}><StatePill state="stale" /></span>}
      {missing && (
        <span data-testid={`${testId}-missing`}>
          {source ? '· ' : ''}{RUN_NAME[missing.run].replace(/^t/, 'T')} has not run
          {missing.action && (
            <>
              {' · '}
              <button type="button" onClick={missing.action.onClick} className="text-accent hover:underline"
                data-testid={`${testId}-action`}>{missing.action.label} ›</button>
            </>
          )}
        </span>
      )}
    </p>
  )
}
