// The investment case's run line, the same in Guided's Investment tile and
// Expert's Investment case (template-e2e issue 22, slice S2; GK B3 F1, F3):
// what it compares before a run, "Running…" during one, what it compared after,
// and an ordered checklist of the inputs it needs when it is refused.
import type { InvestmentCaseReportPayload } from '../../../api/types'
import { useUIStore } from '../../../store/uiStore'
import { openSiteConnection } from '../../economics/SiteConnectionLink'
import { openPanel } from '../openPanel'
import {
  BEFORE_RUN, comparedLine, COMPARES, couldNotFinish, NEED_ORDER, NEED_TEXT, NEEDS_LEAD, RUNNING, type Need,
} from './investmentCaseNeeds'
import type { InvestmentCaseRun } from './useInvestmentCaseRun'

const OPEN: Record<Need, () => void> = {
  solve: () => useUIStore.getState().requestRunSolve(),
  connection: openSiteConnection,
  cod: () => openPanel('commercial', 'economics'),
  finance: () => openPanel('commercial', 'economics'),
}

/** The server's busy sentence as a sentence ("a frontier study is running — wait for it to finish"). */
const sentence = (t: string) => {
  const s = t.trim()
  return `${s.charAt(0).toUpperCase()}${s.slice(1)}${/[.!?]$/.test(s) ? '' : '.'}`
}

/** Where a need's link goes: the solve is an action; the others open a panel (›). */
const linkLabel = (need: Need) => (need === 'solve' ? NEED_TEXT[need].link : `${NEED_TEXT[need].link} ›`)

export default function InvestmentCaseRunPanel({ run, report, mode, known = [], stateShown = false }: {
  run: InvestmentCaseRun
  /** The stored report; null before a run, undefined while it is not read yet. */
  report: InvestmentCaseReportPayload | null | undefined
  mode: 'guided' | 'expert'
  /** Needs another server read already states (the commercial config's
   *  "no site connection"), listed with the refusal's in one checklist. */
  known?: Need[]
  /** The host already shows "Not run yet" (Guided's figure pill, UX 2 on #1886):
   *  the line before a run says only what the case compares. */
  stateShown?: boolean
}) {
  const { running, starting, start, blocker } = run
  const busy = running || starting
  // A need another read states stays listed while a wait or "could not
  // finish" line shows too (GK B3 4 on #1761): it is still missing.
  const needs = NEED_ORDER.filter(n => known.includes(n)
    || (blocker?.kind === 'needs' && blocker.needs.includes(n)))
  return (
    <div className="space-y-1 text-[11px]" data-testid="ic-run-panel">
      {needs.length > 0 && (
        <div data-testid="ic-needs">
          <p>{NEEDS_LEAD}</p>
          <ol className="list-decimal pl-5">
            {needs.map(need => (
              <li key={need} data-testid={`ic-need-${need}`}>
                {NEED_TEXT[need].item} —{' '}
                <button type="button" className="underline text-accent" onClick={OPEN[need]}>
                  {linkLabel(need)}</button>
              </li>
            ))}
          </ol>
        </div>
      )}
      {blocker?.kind === 'wait' ? (
        <p className="text-warn" data-testid="ic-blocked">{sentence(blocker.text)}</p>
      ) : blocker?.kind === 'unknown' ? (
        <p className="text-danger" data-testid="ic-error">{couldNotFinish(mode, blocker.detail, blocker.internal)}</p>
      ) : needs.length > 0 || running ? null : report ? (
        <p data-testid="ic-compared">{comparedLine(report)}</p>
      ) : report === null && run.loaded ? (
        <p className="text-muted" data-testid="ic-not-run">{stateShown ? COMPARES : BEFORE_RUN}</p>
      ) : null}
      <button type="button" data-testid="ic-run" disabled={busy}
              className="px-2 py-1 rounded bg-accent text-on-accent disabled:opacity-50"
              onClick={start}>
        {running ? RUNNING : blocker?.kind === 'wait' ? 'Try again' : 'Run the investment case'}</button>
    </div>
  )
}
