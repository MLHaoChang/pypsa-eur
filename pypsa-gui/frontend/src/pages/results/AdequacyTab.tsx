import { useQuery } from '@tanstack/react-query'
import { resultsApi } from '../../api/simulation'
import { useUIStore } from '../../store/uiStore'
import { nk } from '../../utils/queryKeys'
import {
  AdequacyChips, CoptChips,
  type AdequacyReportPayload, type CoptPayload,
} from './adequacy'
import { FrontierPanel } from './FrontierPanel'
import { ReserveMarginPanel } from './ReserveMarginPanel'
import { McPanel } from './McPanel'
import { LoopPanel } from './LoopPanel'

// ── Results → Adequacy ──────────────────────────────────────────────────────
//
// The IA split the Phase-6 record made conditional and Phase 7 triggered: the
// adequacy surfaces used to ride on the Lost load tab, beside the evidence
// they are read against, with the revisit condition recorded verbatim in
// McPanel.tsx — "when the Phase-7 coupling loop or a fourth study lands, this
// tab has tipped and the adequacy surfaces split into a dedicated
// Results→Adequacy tab". The loop landed; this is that tab.
//
// ★ THE INVARIANT THIS TAB EXISTS TO KEEP: there is NO early return here.
// Not on a solve existing, not on a reliability target being set, and above
// all not on lost load — the tab it split from gated its whole body on
// `totals.mwh > 0`, which hid the studies exactly when the plan had succeeded.
// A reliable system is precisely where these surfaces must still render: the
// COPT screening needs no solve at all, the frontier and the MC are studies
// the user starts FROM this tab, and the loop's whole purpose is to be run on
// a plan that already looks fine to the LP. Every panel below therefore mounts
// unconditionally and states its own empty case in its own words.
//
// Reading order is the ANALYSIS order: what standard actually bound, the
// storage-blind screening beside it, the firm-capacity standard that is the
// OTHER thing the last solve may have been held to, the cost-vs-availability
// curve those points sit on, the sampler that answers what the convolution
// cannot, and finally the loop that drives the sampler's verdict back into
// the plan. The reserve margin sits with the standards rather than with the
// studies because it is one: it shaped the plan every study below is measured
// on, and reading a Monte-Carlo LOLE without knowing a margin forced 200 MW
// of peaker into the fleet is reading half the answer.

export default function AdequacyTab() {
  const currentProject = useUIStore(s => s.currentProject)
  // Achieved-vs-target readout (adequacy plan Phase 1 Task 5). 204 → null.
  // Same query keys McPanel and LoopPanel use, so the three surfaces share one
  // round-trip per project rather than issuing their own.
  const { data: adequacy } = useQuery({
    queryKey: nk(currentProject, 'results', 'adequacy'),
    queryFn: () => resultsApi.getAdequacy(),
  })
  // COPT screening — side by side with the proxy; the divergence is the
  // diagnostic (spec §5.3). Needs no solve, so it is often the only number
  // here on a fresh session.
  const { data: copt } = useQuery({
    queryKey: nk(currentProject, 'results', 'copt'),
    queryFn: () => resultsApi.getCopt(),
  })
  const report = (adequacy ?? null) as AdequacyReportPayload | null

  return (
    <div className="flex flex-col h-full overflow-auto p-4 gap-4">
      <header>
        <h3 className="text-[12.5px] font-semibold text-text tracking-[-0.005em]">
          Adequacy
        </h3>
        <p className="text-[11px] text-muted mt-1">
          Reliability targets, screening, the cost-vs-availability frontier,
          sequential Monte Carlo and the planning loop that couples them. The
          engines answer different questions about the same system — where they
          disagree is the diagnostic, not a bug.
        </p>
      </header>

      {report ? (
        <AdequacyChips report={report} />
      ) : (
        // Not an empty tab and not a stub: "no target" is an ANSWER, and the
        // next action differs by why. Everything below still runs.
        <p className="text-[11px] text-muted" data-testid="adequacy-no-target">
          No reliability target was applied to the last solve, so there is no
          achieved-vs-target readout yet. Set{' '}
          <code className="font-mono">ens_cap_permyriad</code> in solver
          settings and re-solve to get one — or run the screening, the frontier
          or the loop below, none of which needs a target to say something
          useful about this network.
        </p>
      )}

      <CoptChips
        copt={(copt ?? null) as CoptPayload | null}
        proxyEnsMwh={report?.metrics?.ens_mwh ?? null}
      />

      <ReserveMarginPanel />
      <FrontierPanel />
      <McPanel />
      <LoopPanel />
    </div>
  )
}
