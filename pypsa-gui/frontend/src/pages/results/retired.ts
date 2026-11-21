// Which extendable assets a solve retired (template-e2e 14, GK B2 (3) d). The rule is
// one function so the six Capacity expansion tables cannot drift apart.

/** Below this many MW (MWh, MVA) a difference is the solver's tolerance, not a change. */
const EPS = 1e-6

/**
 * The size a solve retired from an extendable asset, or null when it retired none:
 * retired ⇔ solved ∧ p_nom_opt < p_nom − ε ∧ p_nom_min < p_nom − ε.
 * An unsolved network serves p_nom_opt = 0, which is no retirement; an asset whose lower
 * bound is its size (an owned plant pinned at nameplate) cannot be retired. A missing
 * lower bound is PyPSA's default, 0. The caller checks the extendable flag.
 */
export function retiredSize(solved: boolean, nom: number, opt: number | null | undefined,
                            min: number | null | undefined): number | null {
  if (!solved || opt == null || !Number.isFinite(opt)) return null
  const lower = min != null && Number.isFinite(min) ? min : 0
  if (!(opt < nom - EPS) || !(lower < nom - EPS)) return null
  return nom - opt
}
