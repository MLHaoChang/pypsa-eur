// The reliability goal as `GET /projects/{name}/flow/reliability_goal` returns
// it (W3b-R), for tests. Nothing stored by default: each field is its default.
import type { GoalSource, ReliabilityGoal } from '../../api/flow'

type Over = {
  target_lole_h?: [number | null, GoalSource]
  ens_cap_permyriad?: [number | null, GoalSource]
  mc_draws?: [number, GoalSource]
  site_archetype?: [ReliabilityGoal['site_archetype']['value'], GoalSource]
}

export function goal(over: Over = {}): ReliabilityGoal {
  const f = <T,>(v: [T, GoalSource] | undefined, d: T) =>
    v ? { value: v[0], source: v[1] } : { value: d, source: 'default' as GoalSource }
  return {
    target_lole_h: f(over.target_lole_h, null),
    ens_cap_permyriad: f(over.ens_cap_permyriad, null),
    mc_draws: f(over.mc_draws, 500),
    site_archetype: f(over.site_archetype, 'strong_grid' as const),
  }
}
