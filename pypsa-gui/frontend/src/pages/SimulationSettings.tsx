import { useEffect, useRef, useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { Play, Square, CheckCircle, XCircle, Circle } from 'lucide-react'
import { simulationApi, createLogStream } from '../api/simulation'
import { useSimulationStore } from '../store/simulationStore'
import { useUIStore } from '../store/uiStore'
import { saveProjectQuietly } from '../utils/projectActions'
import toast from 'react-hot-toast'

const SOLVERS = ['highs', 'gurobi', 'cplex', 'glpk', 'scip', 'xpress', 'copt', 'mosek']
// Standalone-PF (`mode = 'pf'`) was removed from the workflow — the AC PF
// stage now runs as an auto-chain after LOPF (or via the standalone
// /api/simulation/run_ac_pf trigger driven from SolverSettings). The
// backend's `update_solver_config` still coerces incoming `pf` to `lopf`
// for backward-compat on legacy projects, but the user can no longer
// reach that mode from the picker — so we don't surface it as an
// option here either (it would round-trip to `lopf` on save and
// confuse the user).
const MODES = [
  { value: 'lopf', label: 'LOPF — Linear OPF' },
]
const FREQS = ['h', '3h', '6h', 'D']

function SolverBadge({ name, available }: { name: string; available: boolean | undefined }) {
  return (
    <div className={`flex items-center gap-1.5 px-2 py-1 rounded border text-xs transition-colors
      ${available === true ? 'border-success/50 text-success bg-success/5' :
        available === false ? 'border-border text-muted' : 'border-border text-muted animate-pulse'}`}
    >
      {available === true ? <CheckCircle size={10} /> : available === false ? <XCircle size={10} /> : <Circle size={10} />}
      {name}
    </div>
  )
}

export default function SimulationSettings() {
  const { status, logLines, objective, solveTime, config, setStatus, appendLog, setConfig, setResult } = useSimulationStore()
  const { requestBottomTab, currentProject } = useUIStore()
  const logRef = useRef<HTMLDivElement>(null)
  // Hydrate localConfig from the store and coerce legacy `mode='pf'`
  // to `'lopf'`. SolverSettings does the same coercion (see its
  // hydration effect) — without this mirror, a legacy project loaded
  // here first would render no selected mode-radio because the 'pf'
  // option no longer exists in MODES. The backend also coerces on
  // save, so this is purely cosmetic.
  const [localConfig, setLocalConfig] = useState(
    () => (config.mode as string) === 'pf'
      ? { ...config, mode: 'lopf' as const }
      : config,
  )
  const esCleanupRef = useRef<(() => void) | null>(null)

  const { data: solverAvail } = useQuery({
    queryKey: ['check_solvers'],
    queryFn: simulationApi.checkSolvers,
    staleTime: 30_000,
  })

  // Server-side capability flag. user_code_enabled controls whether the
  // extra_functionality_code field can run. When false (the default), the
  // textarea is disabled with a banner explaining how to opt in.
  const { data: caps } = useQuery({
    queryKey: ['capabilities'],
    queryFn: simulationApi.getCapabilities,
    staleTime: 60_000,
  })
  const userCodeAllowed = caps?.user_code_enabled !== false

  const { data: simStatus } = useQuery({
    queryKey: ['simulation_status'],
    queryFn: simulationApi.getStatus,
    refetchInterval: status === 'running' ? 2000 : false,
  })
  useEffect(() => {
    if (simStatus && !simStatus.running && status === 'running') {
      setStatus(simStatus.condition === 'ok' ? 'completed' : 'failed')
      setResult(simStatus.objective, simStatus.solve_time)
    }
  }, [simStatus, status, setStatus, setResult])

  const saveMut = useMutation({
    mutationFn: () => simulationApi.updateSolverConfig(localConfig),
    onSuccess: () => { setConfig(localConfig); toast.success('Config saved') },
  })

  const runMut = useMutation({
    mutationFn: async () => {
      saveMut.mutate()
      // Pre-run snapshot — see AppHeader.handleRun for the rationale.
      // Runs before /run flips the worker into 'running' so the in-memory
      // network is still in its pre-LP clean state when serialised.
      if (currentProject) {
        await saveProjectQuietly(currentProject)
      }
      return simulationApi.run()
    },
    onMutate: () => {
      setStatus('running')
      // Surface the live log in the bottom panel — useful if the user
      // navigates away from this page mid-solve, the log keeps streaming
      // and stays one click away.
      requestBottomTab('Log')
      const cleanup = createLogStream(
        (line) => appendLog(line),
        (data) => {
          const d = data as { objective?: number; solve_time?: number }
          setResult(d.objective ?? null, d.solve_time ?? null)
          setStatus('completed')
        },
        // SSE error recovery — see AppHeader for the same pattern. Without
        // this, a backend crash mid-solve strands the UI in 'running' forever.
        (reason) => {
          setStatus('failed')
          toast.error(`Log stream lost: ${reason}`)
          esCleanupRef.current = null
        },
      )
      esCleanupRef.current = cleanup
    },
    onError: (err) => {
      setStatus('failed')
      esCleanupRef.current?.()
      esCleanupRef.current = null
      const code = (err as { response?: { status?: number } })?.response?.status
      if (code === 409) {
        toast(
          (t) => (
            <div className="flex items-center gap-3">
              <span>Previous simulation still running.</span>
              <button
                className="px-2 py-0.5 rounded bg-accent text-white text-xs hover:opacity-90"
                onClick={async () => {
                  toast.dismiss(t.id)
                  try {
                    await simulationApi.forceReset()
                    runMut.mutate()
                  } catch { /* ignore */ }
                }}
              >
                Force restart
              </button>
            </div>
          ),
          { duration: 10000 },
        )
      } else {
        toast.error('Simulation failed to start')
      }
    },
  })

  const abortMut = useMutation({
    mutationFn: () => simulationApi.abort(),
    onSuccess: () => { setStatus('idle'); esCleanupRef.current?.(); esCleanupRef.current = null },
  })

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [logLines])

  useEffect(() => () => { esCleanupRef.current?.() }, [])

  const update = (field: string, value: unknown) => setLocalConfig(prev => ({ ...prev, [field]: value }))

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Two-column settings */}
      <div className="flex flex-1 overflow-hidden min-h-0">
        {/* Left — Snapshot / Investment */}
        <div className="w-72 shrink-0 border-r border-border overflow-y-auto p-3 space-y-4">
          <section>
            <p className="text-xs font-semibold text-muted mb-2">Solver</p>
            <div className="flex flex-wrap gap-1.5 mb-3">
              {SOLVERS.map(s => (
                <button
                  key={s}
                  onClick={() => update('solver_name', s)}
                  className={`px-2 py-0.5 rounded border text-xs transition-colors ${
                    localConfig.solver_name === s ? 'border-accent text-accent bg-accent/10' : 'border-border text-muted hover:text-text'
                  }`}
                >
                  {s}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-muted mb-1.5">Availability</p>
            <div className="flex flex-wrap gap-1.5">
              {SOLVERS.map(s => <SolverBadge key={s} name={s} available={solverAvail?.[s]} />)}
            </div>
          </section>

          <section>
            <p className="text-xs font-semibold text-muted mb-2">Problem Type</p>
            <div className="space-y-1">
              {MODES.map(m => (
                <label key={m.value} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    className="accent-accent"
                    name="mode"
                    value={m.value}
                    checked={localConfig.mode === m.value}
                    onChange={() => update('mode', m.value)}
                  />
                  <span className="text-xs text-text">{m.label}</span>
                </label>
              ))}
            </div>
          </section>

          <section>
            <p className="text-xs font-semibold text-muted mb-2">Options</p>
            <label
              className={`flex items-center gap-2 mb-1.5 ${localConfig.sclopf ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
              title={localConfig.sclopf ? 'SCLOPF cannot model losses (LODF assumes a lossless DC network).' : ''}
            >
              <input
                type="checkbox"
                className="accent-accent"
                checked={localConfig.transmission_losses && !localConfig.sclopf}
                disabled={localConfig.sclopf}
                onChange={e => update('transmission_losses', e.target.checked)}
              />
              <span className="text-xs text-text">Transmission losses</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                className="accent-accent"
                checked={localConfig.multi_investment_periods}
                onChange={e => update('multi_investment_periods', e.target.checked)}
              />
              <span className="text-xs text-text">Multi-investment periods</span>
            </label>
          </section>

          <section>
            <p className="text-xs font-semibold text-muted mb-2">Solver Options (JSON)</p>
            <textarea
              className="w-full bg-bg border border-border rounded px-2 py-1.5 text-xs font-mono text-text focus:outline-none focus:border-accent resize-none"
              rows={4}
              value={JSON.stringify(localConfig.solver_options, null, 2)}
              onChange={e => {
                try { update('solver_options', JSON.parse(e.target.value)) } catch { /* ignore invalid JSON while typing */ }
              }}
            />
          </section>

          <section>
            <p className="text-xs font-semibold text-muted mb-2">Extra Functionality</p>
            <p className="text-[10px] text-muted mb-1">fn signature: <code className="font-mono">extra_functionality(n, snapshots)</code></p>
            {!userCodeAllowed && (
              <p className="text-[10px] text-warn bg-warn/10 border border-warn/30 rounded px-2 py-1 mb-1.5">
                Disabled by operator. Set <code className="font-mono">PYPSA_GUI_ALLOW_USER_CODE=1</code> before
                starting the backend to enable. The field runs arbitrary Python in-process — only enable on
                trusted single-user / localhost deployments.
              </p>
            )}
            <textarea
              className="w-full bg-bg border border-border rounded px-2 py-1.5 text-xs font-mono text-text focus:outline-none focus:border-accent resize-none disabled:opacity-50 disabled:cursor-not-allowed"
              rows={6}
              disabled={!userCodeAllowed}
              value={localConfig.extra_functionality_code}
              onChange={e => update('extra_functionality_code', e.target.value)}
              placeholder="def extra_functionality(n, snapshots):\n    pass"
            />
          </section>

          <button
            onClick={() => saveMut.mutate()}
            className="w-full py-1.5 bg-panel border border-border rounded text-xs text-text hover:border-accent transition-colors"
          >
            Save Config
          </button>
        </div>

        {/* Right — Run panel + log */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Run controls */}
          <div className="shrink-0 border-b border-border px-4 py-3 flex items-center gap-3">
            <button
              onClick={() => runMut.mutate()}
              disabled={status === 'running'}
              className="flex items-center gap-2 px-4 py-2 bg-success/20 text-success border border-success/40 rounded text-sm font-semibold hover:bg-success/30 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Play size={14} /> Run Optimization
            </button>
            <button
              onClick={() => abortMut.mutate()}
              disabled={status !== 'running'}
              className="flex items-center gap-2 px-4 py-2 bg-danger/20 text-danger border border-danger/40 rounded text-sm font-semibold hover:bg-danger/30 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Square size={14} /> Abort
            </button>
            <div className="flex items-center gap-2 ml-2">
              <span className={`w-2 h-2 rounded-full ${
                status === 'running' ? 'bg-warn animate-pulse' :
                status === 'completed' ? 'bg-success' :
                status === 'failed' ? 'bg-danger' : 'bg-muted'
              }`} />
              <span className="text-xs text-muted capitalize">{status}</span>
            </div>
            {objective != null && (
              <span className="text-xs text-text ml-auto">
                Objective: <span className="font-mono text-accent">{objective.toExponential(4)}</span>
                {solveTime != null && <span className="text-muted ml-3">{solveTime.toFixed(1)}s</span>}
              </span>
            )}
          </div>

          {/* Log terminal */}
          <div
            ref={logRef}
            className="flex-1 overflow-y-auto bg-canvas font-mono text-xs text-text p-3 space-y-0.5 select-text"
            style={{ scrollBehavior: 'smooth' }}
          >
            {logLines.length === 0 ? (
              <span className="text-muted">Waiting for simulation output…</span>
            ) : (
              logLines.map((line, i) => (
                <div
                  key={i}
                  className={`whitespace-pre-wrap leading-5 ${
                    line.includes('ERROR') || line.includes('error') ? 'text-danger' :
                    line.includes('WARNING') || line.includes('warning') ? 'text-warn' :
                    line.includes('INFO') ? 'text-text' : 'text-muted'
                  }`}
                >
                  {line}
                </div>
              ))
            )}
            {status === 'running' && <div className="text-warn animate-pulse">▋</div>}
          </div>

          {/* Log toolbar */}
          <div className="shrink-0 border-t border-border px-3 py-1 flex items-center gap-3">
            <span className="text-[10px] text-muted">{logLines.length} lines</span>
            <button
              onClick={() => useSimulationStore.getState().clearLog()}
              className="text-[10px] text-muted hover:text-text transition-colors ml-auto"
            >
              Clear
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
