import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Save, FolderOpen, Trash2, GitCompare, X } from 'lucide-react'
import { projectsApi } from '../api/projects'
import { networkApi } from '../api/network'
import type { ProjectInfo } from '../api/types'
import { useUIStore } from '../store/uiStore'
import toast from 'react-hot-toast'
import { confirmToast } from '../utils/toasts'

function fmt(iso: string) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
}

function CompareDrawer({ a, b, onClose }: { a: ProjectInfo; b: ProjectInfo; onClose: () => void }) {
  const fields: { label: string; key: keyof ProjectInfo }[] = [
    { label: 'Buses', key: 'bus_count' },
    { label: 'Snapshots', key: 'snapshot_count' },
    { label: 'Objective (€)', key: 'objective' },
    { label: 'Created', key: 'created_at' },
    { label: 'Solver config', key: 'has_solver_config' },
  ]

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ background: 'rgba(0,0,0,0.6)' }}>
      <div className="bg-panel border border-border rounded-lg w-[560px] max-h-[80vh] overflow-auto">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
          <GitCompare size={16} className="text-accent" />
          <span className="text-sm font-semibold text-text">Compare Projects</span>
          <button onClick={onClose} className="ml-auto text-muted hover:text-text transition-colors">
            <X size={16} />
          </button>
        </div>
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border">
              <th className="px-4 py-2 text-left text-muted font-semibold">Field</th>
              <th className="px-4 py-2 text-left text-accent font-semibold">{a.name}</th>
              <th className="px-4 py-2 text-left font-semibold" style={{ color: '#3fb950' }}>{b.name}</th>
            </tr>
          </thead>
          <tbody>
            {fields.map(f => {
              const av = a[f.key]
              const bv = b[f.key]
              const diff = typeof av === 'number' && typeof bv === 'number' && av !== bv
              return (
                <tr key={f.key} className="border-b border-border/40">
                  <td className="px-4 py-2 text-muted">{f.label}</td>
                  <td className={`px-4 py-2 font-mono ${diff ? 'text-warn' : 'text-text'}`}>
                    {av == null ? '—' : typeof av === 'boolean' ? (av ? 'yes' : 'no') : typeof av === 'number' ? av.toFixed(2) : String(av)}
                  </td>
                  <td className={`px-4 py-2 font-mono ${diff ? 'text-warn' : 'text-text'}`}>
                    {bv == null ? '—' : typeof bv === 'boolean' ? (bv ? 'yes' : 'no') : typeof bv === 'number' ? bv.toFixed(2) : String(bv)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function Projects() {
  const qc = useQueryClient()
  const { currentProject, setCurrentProject, setProjectName } = useUIStore()
  const { data: projects = [], isLoading } = useQuery({ queryKey: ['projects'], queryFn: projectsApi.list })
  const [compareSel, setCompareSel] = useState<string[]>([])
  const [showCompare, setShowCompare] = useState(false)
  const [saveName, setSaveName] = useState('')

  const saveMut = useMutation({
    mutationFn: async (name: string) => {
      // Assert identity only when re-saving the ACTIVE project (the in-memory
      // network is bound to it); a Save-As under a new name omits expect so the
      // backend allows the write. This page always makes `name` the active
      // project on success (setCurrentProject below), so rebind=true so the
      // backend follows — otherwise a Save-As would leave the backend bound to
      // the old project and the next save would 409 until reload.
      const expect = name === currentProject ? name : undefined
      const res = await projectsApi.save(name, false, true, expect, true)
      // Flush the diagram layout AFTER the project directory exists on disk.
      // Without this, a freshly-dragged bus position saved through the
      // Projects-page Save button would be silently dropped because the
      // canvas's layout.json never reaches the server.
      try {
        const { flushPendingLayoutToServer } = await import('./TopologyCanvas')
        await flushPendingLayoutToServer(res.saved)
      } catch { /* layout flush is best-effort */ }
      return res
    },
    onSuccess: (_data, name) => {
      qc.invalidateQueries({ queryKey: ['projects'] })
      // Saving rewrites the project's on-disk state — drop its cached
      // compare-state so an open Compare panel doesn't show stale numbers.
      qc.invalidateQueries({ queryKey: ['compare-state', name] })
      setSaveName('')
      setCurrentProject(name)
      toast.success('Project saved')
    },
    onError: () => toast.error('Save failed'),
  })

  const loadMut = useMutation({
    mutationFn: (name: string) => projectsApi.load(name),
    onSuccess: (_data, name) => {
      qc.invalidateQueries()
      setCurrentProject(name)
      toast.success('Project loaded')
    },
    onError: () => toast.error('Load failed'),
  })

  const deleteMut = useMutation({
    mutationFn: (params: { name: string; cascade: boolean }) =>
      projectsApi.delete(params.name, params.cascade),
    onSuccess: async ({ deleted, failed }) => {
      qc.invalidateQueries({ queryKey: ['projects'] })
      // If the active project (or an ancestor) was deleted, clear the
      // dangling `currentProject` + reset the network so the autosave loop
      // can't resurrect the deleted directory.
      if (currentProject && deleted.includes(currentProject)) {
        try { await networkApi.resetNetwork() } catch { /* best effort */ }
        setCurrentProject(null)
        setProjectName('Untitled')
        qc.invalidateQueries()
      }
      for (const n of deleted) qc.removeQueries({ queryKey: ['compare-state', n] })
      if (failed.length > 0) {
        toast.error(`Could not delete: ${failed.join(', ')} (file lock?)`)
      }
      if (deleted.length > 0) {
        toast.success(deleted.length > 1
          ? `Deleted '${deleted[0]}' + ${deleted.length - 1} more`
          : `Deleted '${deleted[0]}'`)
      }
    },
    onError: (err, params) => {
      // Phase 8 introduced cascade-delete: the backend refuses (409) when
      // the target has scenarios pointing at it. Mirror the same retry-prompt
      // UX that ScenariosPanel uses so this legacy page isn't a one-way trap.
      const e = err as { response?: { status?: number; data?: { detail?: string } } }
      if (e.response?.status === 409 && !params.cascade) {
        const detail = e.response.data?.detail ?? `${params.name} has child scenarios`
        if (window.confirm(`${detail}\n\nDelete recursively?`)) {
          deleteMut.mutate({ name: params.name, cascade: true })
        }
        return
      }
      toast.error(`Delete failed: ${e.response?.data?.detail ?? (err as Error).message}`)
    },
  })

  const toggleCompare = (name: string) => {
    setCompareSel(prev =>
      prev.includes(name) ? prev.filter(n => n !== name) : prev.length < 2 ? [...prev, name] : [prev[1], name]
    )
  }

  const compareProjects = compareSel.length === 2
    ? projects.filter((p: ProjectInfo) => compareSel.includes(p.name))
    : null

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Save bar */}
      <div className="shrink-0 border-b border-border px-4 py-3 flex items-center gap-3">
        <input
          className="bg-bg border border-border rounded px-3 py-1.5 text-sm text-text placeholder-muted focus:outline-none focus:border-accent w-56"
          placeholder="Project name…"
          value={saveName}
          onChange={e => setSaveName(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && saveName.trim()) saveMut.mutate(saveName.trim()) }}
        />
        <button
          onClick={() => saveName.trim() && saveMut.mutate(saveName.trim())}
          disabled={!saveName.trim()}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-accent/20 text-accent border border-accent/40 rounded text-sm hover:bg-accent/30 transition-colors disabled:opacity-40"
        >
          <Save size={14} /> Save Current Network
        </button>
        {compareSel.length === 2 && (
          <button
            onClick={() => setShowCompare(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-panel border border-border rounded text-sm text-text hover:border-accent transition-colors ml-auto"
          >
            <GitCompare size={14} /> Compare Selected
          </button>
        )}
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {isLoading ? (
          <div className="p-8 text-center text-muted text-sm animate-pulse">Loading projects…</div>
        ) : projects.length === 0 ? (
          <div className="p-8 text-center text-muted text-sm">
            No saved projects yet. Use the save bar above to create one.
          </div>
        ) : (
          <table className="w-full text-xs border-collapse">
            <thead className="sticky top-0 bg-panel border-b border-border">
              <tr>
                <th className="px-3 py-2 text-left text-muted font-semibold w-8">
                  <span className="text-[10px]">Cmp</span>
                </th>
                <th className="px-3 py-2 text-left text-muted font-semibold">Name</th>
                <th className="px-3 py-2 text-left text-muted font-semibold">Created</th>
                <th className="px-3 py-2 text-right text-muted font-semibold">Buses</th>
                <th className="px-3 py-2 text-right text-muted font-semibold">Snapshots</th>
                <th className="px-3 py-2 text-right text-muted font-semibold">Objective</th>
                <th className="px-3 py-2 text-left text-muted font-semibold w-28">Actions</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p: ProjectInfo, i: number) => (
                <tr
                  key={p.name}
                  className={`border-b border-border/40 ${i % 2 === 0 ? 'bg-bg' : 'bg-panel/60'}
                    ${compareSel.includes(p.name) ? 'bg-accent/5 border-accent/20' : 'hover:bg-accent/5'} transition-colors`}
                >
                  <td className="px-3 py-2">
                    <input
                      type="checkbox"
                      className="accent-accent cursor-pointer"
                      checked={compareSel.includes(p.name)}
                      onChange={() => toggleCompare(p.name)}
                    />
                  </td>
                  <td className="px-3 py-2 text-text font-semibold">{p.name}</td>
                  <td className="px-3 py-2 text-muted">{fmt(p.created_at)}</td>
                  <td className="px-3 py-2 text-right font-mono text-text">{p.bus_count}</td>
                  <td className="px-3 py-2 text-right font-mono text-text">{p.snapshot_count}</td>
                  <td className="px-3 py-2 text-right font-mono text-text">
                    {p.objective != null ? p.objective.toExponential(3) : '—'}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex gap-1">
                      <button
                        onClick={() => loadMut.mutate(p.name)}
                        title="Load"
                        className="flex items-center gap-0.5 px-1.5 py-1 rounded border border-border text-muted hover:text-accent hover:border-accent transition-colors"
                      >
                        <FolderOpen size={11} />
                      </button>
                      <button
                        onClick={() => confirmToast(
                          `Delete project "${p.name}"?`,
                          () => deleteMut.mutate({ name: p.name, cascade: false }),
                          { confirmLabel: 'Delete', danger: true },
                        )}
                        title="Delete"
                        className="flex items-center gap-0.5 px-1.5 py-1 rounded border border-border text-muted hover:text-danger hover:border-danger transition-colors"
                      >
                        <Trash2 size={11} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showCompare && compareProjects?.length === 2 && (
        <CompareDrawer a={compareProjects[0]} b={compareProjects[1]} onClose={() => setShowCompare(false)} />
      )}
    </div>
  )
}
