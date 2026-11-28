// SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
// SPDX-License-Identifier: MIT

/**
 * The one "source run" line under a Results headline, in both modes (control-strategy walkthrough
 * improvement 2, issue 06, #1618-01): which run the figures above come from, when it ran, whether
 * it is out of date, and, when the other mode's run is missing, that in words with one link.
 */
import type { ReactNode } from 'react'
import { Btn } from './PageKit'
import { StatePill } from './ResultState'

export type SourceRun = 'dispatch' | 'study'

const EXPERT_RUN_NAME: Record<SourceRun, string> = {
  dispatch: 'the dispatch solve',
  study: 'the reliability study',
}

/** Guided names the dispatch solve by what it does (UX 34 on #2140): no "solve" in Guided. */
const GUIDED_RUN_NAME: Record<SourceRun, string> = {
  dispatch: 'the optimisation',
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
  /** The other run, when it has not run: named in words, with one link to it ("›"), or, `kind: 'run'`, the
   *  button that runs it in place (no "›"; `disabled` while a run is queued or going: never a second run). */
  missing?: {
    run: SourceRun
    action?: { label: string; onClick: () => void; kind?: 'link' | 'run'; disabled?: boolean }
    /** A run is queued or going (UX 34 on #2140): 'self' when it is this missing run, which then reads
     *  "… is running…"; 'other' when another run is, and this one can run once it finishes. Either way
     *  the line offers no action, never "has not run" beside a greyed button. */
    busy?: 'self' | 'other'
  }
  testId?: string
  /** Guided's words for the runs; Expert's by default. */
  mode?: 'guided' | 'expert'
}

export default function SourceRunLine({ source, at, outOfDate = false, missing, testId = 'source-run', mode = 'expert' }: SourceRunLineProps) {
  const stamp = runStamp(at)
  const RUN_NAME = mode === 'guided' ? GUIDED_RUN_NAME : EXPERT_RUN_NAME
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted" data-testid={testId}>
      {source && <span data-testid={`${testId}-from`}>From {RUN_NAME[source]}{stamp ? `, ${stamp}` : ''}</span>}
      {source && outOfDate && <span data-testid={`${testId}-stale`}><StatePill state="stale" /></span>}
      {missing && (
        // Its own row under the source run, and each "·" bound to the item after it, so no line starts or
        // ends in a "·" when it wraps (UX 34 on #2140).
        <span data-testid={`${testId}-missing`} className="inline-flex basis-full flex-wrap items-center gap-x-2">
          <Sep show={false}>
            {missing.busy === 'self' ? <span>{cap(RUN_NAME[missing.run])} is running…</span>
              : <span>{cap(RUN_NAME[missing.run])} has not run</span>}
          </Sep>
          {missing.busy === 'other' && (
            <Sep show>it can run when the current run finishes</Sep>
          )}
          {missing.action && missing.busy == null && (
            <Sep show>
              {missing.action.kind === 'run' ? (
                <Btn type="button" onClick={missing.action.onClick} disabled={missing.action.disabled}
                  data-testid={`${testId}-action`}>{missing.action.label}</Btn>
              ) : (
                <button type="button" onClick={missing.action.onClick} className="text-link hover:underline"
                  data-testid={`${testId}-action`}>{missing.action.label} ›</button>
              )}
            </Sep>
          )}
        </span>
      )}
    </p>
  )
}

const cap = (words: string) => words.replace(/^t/, 'T')

/** An item with the "·" before it, kept together on one line; the gap sits evenly on both sides of the dot. */
function Sep({ show, children }: { show: boolean; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-x-2 whitespace-nowrap">
      {show && <span aria-hidden="true">·</span>}
      {children}
    </span>
  )
}
