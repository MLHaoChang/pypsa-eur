import { useNavigate, useLocation } from 'react-router-dom'
import { Play, Circle, X, Loader } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { useSimulationStore } from '../store/simulationStore'
import { useUIStore } from '../store/uiStore'
import { networkApi } from '../api/network'
import { simulationApi } from '../api/simulation'
import { saveProjectQuietly } from '../utils/projectActions'
import { nk } from '../utils/queryKeys'

const BREADCRUMBS: Record<string, string> = {
  '/topology': 'Network / Topology',
  '/generation': 'Network / Generation',
  '/loads': 'Network / Loads',
  '/timeseries': 'Network / Time Series',
  '/simulation': 'Simulation',
  '/results': 'Results',
  '/io': 'Import / Export',
  '/projects': 'Projects',
}

export default function TopBar() {
  const navigate = useNavigate()
  const location = useLocation()
  const { status, setStatus, clearLog } = useSimulationStore()
  const { requestBottomTab, currentProject } = useUIStore()
  const { data: meta } = useQuery({ queryKey: nk(currentProject, 'meta'), queryFn: networkApi.getMeta, refetchInterval: 10000 })

  async function handleRun() {
    if (status === 'running') {
      await simulationApi.abort()
      setStatus('aborted')
      return
    }
    clearLog()
    // Pre-run snapshot — see AppHeader.handleRun for the rationale. Save
    // the clean network before flipping status to 'running' so
    // saveProjectQuietly's "skip if running" guard doesn't trip.
    if (currentProject) {
      await saveProjectQuietly(currentProject)
    }
    setStatus('running')
    // Pop the bottom panel open on the Log tab so the SSE stream is visible
    // the moment the user clicks Run. BottomPanel watches this request and
    // also un-collapses itself + restores its last-set height.
    requestBottomTab('Log')
    navigate('/simulation')
    try {
      await simulationApi.run()
    } catch (err) {
      setStatus('failed')
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
                    void handleRun()
                  } catch { /* ignore */ }
                }}
              >
                Force restart
              </button>
            </div>
          ),
          { duration: 10000 },
        )
      }
    }
  }

  const btnClass = {
    idle: 'bg-success hover:bg-success/80 text-bg',
    completed: 'bg-success hover:bg-success/80 text-bg',
    aborted: 'bg-success hover:bg-success/80 text-bg',
    running: 'bg-warn hover:bg-warn/80 text-bg animate-pulse',
    failed: 'bg-danger hover:bg-danger/80 text-bg',
  }[status] ?? 'bg-success hover:bg-success/80 text-bg'

  const btnIcon = status === 'running' ? <Loader size={14} className="animate-spin" /> : <Play size={14} />
  const btnLabel = status === 'running' ? 'Running…' : status === 'failed' ? 'Failed' : 'Run'

  return (
    <header className="flex items-center gap-4 px-4 h-12 bg-bg border-b border-border shrink-0 shadow-sm">
      <span className="text-muted text-sm font-mono truncate flex-1">
        {meta?.name ?? 'Untitled Project'}
        <span className="mx-2 text-border">·</span>
        <span className="text-xs">{BREADCRUMBS[location.pathname] ?? ''}</span>
      </span>

      <button
        onClick={handleRun}
        className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-semibold transition-all ${btnClass}`}
        title={status === 'running' ? 'Abort simulation' : 'Run optimization'}
      >
        {btnIcon}
        {btnLabel}
      </button>
    </header>
  )
}
