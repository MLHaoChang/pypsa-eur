import { create } from 'zustand'
import type { FailureInfo, SolverConfig } from '../api/types'

type SimStatus = 'idle' | 'running' | 'completed' | 'failed' | 'aborted'
export type LogLevel = 'INFO' | 'WARN' | 'ERROR'

interface SimulationStore {
  status: SimStatus
  logLines: string[]
  objective: number | null
  solveTime: number | null
  /**
   * Epoch ms when the current solve entered `running`, or null when not
   * running. Set centrally in `setStatus` so the StatusBar can render a live
   * elapsed timer without every caller threading a timestamp. Cleared the
   * moment status leaves `running` (terminal or reset).
   */
  runStartedAt: number | null
  /**
   * Actionable failure card from the last finished solve (null on success /
   * abort / before any run). Set from the SSE `done` payload; cleared when a
   * new run starts and on project switch. Drives the IssuesPanel failure
   * banner + the auto-open-on-failure behaviour.
   */
  lastFailure: FailureInfo | null
  config: SolverConfig
  setStatus: (s: SimStatus) => void
  setLastFailure: (f: FailureInfo | null) => void
  /**
   * Clear the result display (status/objective/solveTime/failure) without
   * touching the log or config — used when a topology edit invalidates the
   * solver dispatch, so the StatusBar stops showing a stale "Completed · €X"
   * for results the backend has already cleared.
   */
  clearResults: () => void
  appendLog: (line: string) => void
  clearLog: () => void
  setResult: (objective: number | null, solveTime: number | null) => void
  setConfig: (c: Partial<SolverConfig>) => void
  /**
   * Wipe the live solve state (status / objective / solveTime / logLines)
   * but PRESERVE `config` — solver config is loaded fresh per project from
   * the backend's solver_config.json on every project switch (see App.tsx
   * recovery flow + Projects.load), so re-using the previous project's
   * solve KPI / log lines is the only cross-project bleed worth clearing
   * here. Without this, loading project B right after solving project A
   * shows A's "Optimal · 1.23 B€" in StatusBar and A's solver log in
   * BottomPanel until B finishes its own solve.
   */
  resetForProjectSwitch: () => void
}

const defaultConfig: SolverConfig = {
  solver_name: 'highs',
  mode: 'lopf',
  transmission_losses: false,
  multi_investment_periods: false,
  solver_options: {},
  extra_functionality_code: '',
  // Modelling assumptions defaults — zero surcharges, no slack, single
  // period — preserves pre-feature LP behaviour out of the box.
  discount_rate: 0.07,
  inflation_rate: 0,
  default_lifetime: 25,
  co2_price: 0,
  co2_price_per_period: {},
  voll: 0,
  investment_periods: [],
  sclopf: false,
  sclopf_include_all_lines: false,
  sclopf_include_all_transformers: false,
  sclopf_voltage_threshold_kv: 0,
  sclopf_extra_lines: [],
  sclopf_extra_transformers: [],
  sclopf_scope: 'horizon',
  // Stage 2 defaults — off by default, auto-pick slack when on.
  run_ac_pf_after_lopf: false,
  ac_pf_slack_bus: '',
  ac_pf_x_tol: 1e-6,
  presolve_enabled: true,
  user_objective_scale: 1,
  auto_objective_scale: false,
  solve_strategy: 'full',
  rolling_horizon: 168,
  rolling_overlap: 24,
  lf_aggregate_future: false,
  lf_k_periods: 8,
  lf_period_length_h: 168,
  lf_cluster_method: 'hierarchical',
  lf_include_extreme: true,
  mip_gap: 0.01,
  mip_time_limit_s: 0,
}

export const useSimulationStore = create<SimulationStore>((set) => ({
  status: 'idle',
  logLines: [],
  objective: null,
  solveTime: null,
  runStartedAt: null,
  lastFailure: null,
  config: defaultConfig,
  // Stamp runStartedAt on the idle/terminal → running transition; keep the
  // existing stamp if already running (so an SSE re-attach mid-solve doesn't
  // reset the clock); clear it on any non-running status. Entering `running`
  // also clears any stale failure card from the previous run.
  setStatus: (s) => set(st => ({
    status: s,
    runStartedAt: s === 'running'
      ? (st.status === 'running' && st.runStartedAt != null ? st.runStartedAt : Date.now())
      : null,
    lastFailure: s === 'running'
      ? (st.status === 'running' ? st.lastFailure : null)
      : st.lastFailure,
  })),
  setLastFailure: (f) => set({ lastFailure: f }),
  // Don't clear logLines — the last solve's log stays useful after an edit.
  clearResults: () => set({
    status: 'idle', objective: null, solveTime: null,
    runStartedAt: null, lastFailure: null,
  }),
  appendLog: (line) => set(st => ({
    logLines: st.logLines.length > 2000
      ? [...st.logLines.slice(-1800), line]
      : [...st.logLines, line],
  })),
  clearLog: () => set({ logLines: [] }),
  setResult: (objective, solveTime) => set({ objective, solveTime }),
  setConfig: (c) => set(st => ({ config: { ...st.config, ...c } })),
  resetForProjectSwitch: () => set({
    status: 'idle',
    logLines: [],
    objective: null,
    solveTime: null,
    runStartedAt: null,
    lastFailure: null,
  }),
}))

/** Call from anywhere — outside React components too. */
export function appLog(level: LogLevel, msg: string): void {
  const ts = new Date().toTimeString().slice(0, 8)  // HH:MM:SS
  useSimulationStore.getState().appendLog(`[${ts}] ${level} ${msg}`)
}
