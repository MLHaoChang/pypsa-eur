// The investment case's run line, the same in Guided's Investment tile and
// Expert's Investment case (template-e2e issue 22, slice S2; GK B3 F1, F3):
// what it compares before a run, "Running…" during one, what it compared after,
// and an ordered checklist of the inputs it needs when it is refused.
import type { InvestmentCaseReportPayload } from '../../../api/types'
import { useUIStore } from '../../../store/uiStore'
import { openSiteConnection } from '../../economics/SiteConnectionLink'
import { openPanel } from '../openPanel'
import {
  BEFORE_RUN, comparedLine, couldNotFinish, NEED_TEXT, NEEDS_LEAD, RUNNING, type Need,
} from './investmentCaseNeeds'
import type { InvestmentCaseRun } from './useInvestmentCaseRun'

const OPEN: Record<Need, () => void> = {
  solve: () => useUIStore.getState().requestRunSolve(),
  connection: openSiteConnection,
  cod: () => openPanel('commercial', 'economics'),
  finance: () => openPanel('commercial', 'economics'),
}

/** Where a need's link goes: the solve is an action; the others open a panel (›). */
const linkLabel = (need: Need) => (need === 'solve' ? NEED_TEXT[need].link : `${NEED_TEXT[need].link} ›`)

export default function InvestmentCaseRunPanel({ run, report, mode }: {
  run: InvestmentCaseRun
  /** The stored report; null before a run, undefined while it is not read yet. */
  report: InvestmentCaseReportPayload | null | undefined
  mode: 'guided' | 'expert'
}) {
  const { running, starting, start, blocker } = run
  const busy = running || starting
  return (
    <div className="space-y-1 text-[11px]" data-testid="ic-run-panel">
      {blocker?.kind === 'needs' ? (
        <div data-testid="ic-needs">
          <p>{NEEDS_LEAD}</p>
          <ol className="list-decimal pl-5">
            {blocker.needs.map(need => (
              <li key={need} data-testid={`ic-need-${need}`}>
                {NEED_TEXT[need].item}{' '}
                <button type="button" className="underline text-accent" onClick={OPEN[need]}>
                  {linkLabel(need)}</button>
              </li>
            ))}
          </ol>
        </div>
      ) : blocker?.kind === 'wait' ? (
        <p className="text-warn" data-testid="ic-blocked">Wait: {blocker.text}</p>
      ) : blocker?.kind === 'unknown' ? (
        <p className="text-danger" data-testid="ic-error">{couldNotFinish(mode, blocker.detail)}</p>
      ) : running ? null : report ? (
        <p data-testid="ic-compared">{comparedLine(report)}</p>
      ) : report === null && run.loaded ? (
        <p className="text-muted" data-testid="ic-not-run">{BEFORE_RUN}</p>
      ) : null}
      <button type="button" data-testid="ic-run" disabled={busy}
              className="px-2 py-1 rounded bg-accent text-on-accent disabled:opacity-50"
              onClick={start}>
        {running ? RUNNING : 'Run the investment case'}</button>
    </div>
  )
}
