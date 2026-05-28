import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Search, X, FolderOpen, Camera, Save as SaveIcon, FilePlus, Settings2,
  TrendingUp, Clock, Zap, RotateCcw, LayoutDashboard,
  Sun, Moon, Rows2, Rows3, GitBranch, Layers,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { useUIStore } from '../store/uiStore'
import { networkApi } from '../api/network'
import { projectsApi } from '../api/projects'
import { appLog } from '../store/simulationStore'
import { invalidateNetworkQueries, saveProjectQuietly, formatRelativeTime, abortRunningSim } from '../utils/projectActions'
import type { Bus, Generator, Load, Line, Link, StorageUnit, Store, Transformer } from '../api/types'

// ── Types ────────────────────────────────────────────────────────────────────

// Command "kinds" determine the row icon + group heading. They also drive
// the ⌘P pre-filter (only kind='project' commands shown).
type CommandKind = 'action' | 'project' | 'snapshot' | 'asset'

interface Command {
  id: string                    // stable key for the row
  kind: CommandKind
  title: string                 // primary text (matched against query)
  subtitle?: string             // secondary text (also matched)
  hint?: string                 // right-aligned hint (shortcut, last-saved, etc.)
  icon: React.ReactNode
  run: () => void | Promise<void>
}

// Open mode — drives the placeholder + result filter. The 'projects' mode is
// the ⌘P "project switcher" surface; the palette stays a single component but
// shows a focused subset of commands.
export type PaletteMode = 'all' | 'projects'

// ── Public API ───────────────────────────────────────────────────────────────

// Mounted once at the app root. The store owns open/mode state so any
// component (e.g. AppHeader, Sidebar) can trigger the palette without prop
// drilling. Keyboard shortcuts ⌘K / ⌘P live in a separate effect on App.tsx
// — this component just renders when openMode is non-null.
export default function CommandPalette() {
  const openMode = useUIStore(s => s.paletteMode)
  const setOpenMode = useUIStore(s => s.setPaletteMode)

  if (openMode === null) return null
  return <PaletteShell mode={openMode} onClose={() => setOpenMode(null)} />
}

// ── Shell ────────────────────────────────────────────────────────────────────

function PaletteShell({ mode, onClose }: { mode: PaletteMode; onClose: () => void }) {
  const [query, setQuery] = useState('')
  const [selectedIdx, setSelectedIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  // Auto-focus on mount; the modal trap is implicit because pointer events
  // outside the panel hit the backdrop, which dismisses the palette.
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // Reset selection when the filtered set changes — otherwise the highlight
  // can land on an invalid index after typing narrows the list.
  useEffect(() => { setSelectedIdx(0) }, [query, mode])

  // Build commands. useCommands hook pulls from the various caches and
  // returns a flat, already-grouped list. We then fuzzy-filter on the query.
  const all = useCommands(mode)
  const filtered = useMemo(() => fuzzyFilter(all, query), [all, query])

  const onKey = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') { onClose(); return }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIdx(i => Math.min(filtered.length - 1, i + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIdx(i => Math.max(0, i - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const cmd = filtered[selectedIdx]
      if (cmd) {
        void cmd.run()
        onClose()
      }
    }
  }, [filtered, selectedIdx, onClose])

  // Group results by kind for visual separation. Order is fixed to match
  // user expectations: actions first, then projects, then snapshots, then
  // assets (which tend to be numerous).
  const groups = useMemo(() => {
    const order: CommandKind[] = mode === 'projects'
      ? ['project']
      : ['action', 'project', 'snapshot', 'asset']
    const out: Array<{ kind: CommandKind; rows: Command[] }> = []
    for (const k of order) {
      const rows = filtered.filter(c => c.kind === k)
      if (rows.length) out.push({ kind: k, rows })
    }
    return out
  }, [filtered, mode])

  return (
    <div
      data-no-panel-close
      className="fixed inset-0 z-[500] flex items-start justify-center pt-[12vh] bg-black/30"
      onClick={onClose}
    >
      <div
        className="bg-bg rounded-lg shadow-2xl w-[640px] max-w-[92vw] max-h-[70vh] flex flex-col overflow-hidden border border-border"
        onClick={e => e.stopPropagation()}
      >
        {/* Search input row */}
        <div className="flex items-center gap-2 px-3 py-2.5 border-b border-border">
          <Search size={14} className="text-muted shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={onKey}
            placeholder={mode === 'projects'
              ? 'Switch project…'
              : 'Type a command, project, snapshot, or asset name…'}
            className="flex-1 bg-transparent text-sm focus:outline-none placeholder:text-muted/60"
          />
          <button
            onClick={onClose}
            className="text-muted hover:text-text transition-colors shrink-0"
            title="Close (Esc)"
          >
            <X size={14} />
          </button>
        </div>

        {/* Results body */}
        <div className="flex-1 min-h-0 overflow-y-auto">
          {filtered.length === 0 && (
            <div className="text-xs text-muted text-center py-8 px-6">
              No matches. Try a different query.
            </div>
          )}
          {(() => {
            // Pre-compute a flat array index so the keyboard selection across
            // groups maps to the right row in the rendered list.
            let runningIdx = 0
            return groups.map(g => (
              <div key={g.kind}>
                <div className="text-[9px] font-semibold uppercase tracking-wide text-muted/70 px-3 pt-2 pb-1">
                  {GROUP_LABELS[g.kind]}
                </div>
                {g.rows.map(cmd => {
                  const idx = runningIdx++
                  const active = idx === selectedIdx
                  return (
                    <button
                      key={cmd.id}
                      onClick={() => { void cmd.run(); onClose() }}
                      onMouseEnter={() => setSelectedIdx(idx)}
                      className={`w-full flex items-center gap-2.5 px-3 py-1.5 text-left transition-colors ${
                        active ? 'bg-accent/10' : 'hover:bg-panel'
                      }`}
                    >
                      <span className={`shrink-0 ${active ? 'text-accent' : 'text-muted'}`}>
                        {cmd.icon}
                      </span>
                      <span className="flex-1 min-w-0">
                        <span className="block text-[12px] text-text truncate">{cmd.title}</span>
                        {cmd.subtitle && (
                          <span className="block text-[10px] text-muted truncate">{cmd.subtitle}</span>
                        )}
                      </span>
                      {cmd.hint && (
                        <span className="text-[10px] text-muted/70 font-mono shrink-0 ml-2">
                          {cmd.hint}
                        </span>
                      )}
                    </button>
                  )
                })}
              </div>
            ))
          })()}
        </div>

        {/* Footer hint */}
        <div className="flex items-center gap-3 px-3 py-1.5 border-t border-border text-[10px] text-muted/70 font-mono shrink-0">
          <span>↑↓ navigate</span>
          <span>⏎ select</span>
          <span>esc close</span>
          <span className="ml-auto">{filtered.length} result{filtered.length === 1 ? '' : 's'}</span>
        </div>
      </div>
    </div>
  )
}

const GROUP_LABELS: Record<CommandKind, string> = {
  action: 'Actions',
  project: 'Projects',
  snapshot: 'Snapshots',
  asset: 'Assets',
}

// ── Command sources ──────────────────────────────────────────────────────────

// Aggregates commands from various places — store actions, network queries,
// project/snapshot queries. Each `useQuery` is keyed exactly like the
// consumers in the rest of the app so we hit cached results when the user
// opens the palette during normal browsing.
function useCommands(mode: PaletteMode): Command[] {
  const qc = useQueryClient()
  // Per-field zustand selectors instead of `useUIStore()` for the whole store.
  // The bare hook subscribes the component to *every* slice — meaning unrelated
  // updates (canvasMode, sidebarMode, resultsSnapshotIdx, …) re-render the
  // palette and rebuild the entire command list (N asset rows). With selectors
  // we only re-render on the slices we actually read.
  const currentProject       = useUIStore(s => s.currentProject)
  const setCurrentProject    = useUIStore(s => s.setCurrentProject)
  const setProjectName       = useUIStore(s => s.setProjectName)
  const setSelectedComponent = useUIStore(s => s.setSelectedComponent)
  const setHighlightedComponent = useUIStore(s => s.setHighlightedComponent)
  const openRightPanel       = useUIStore(s => s.openRightPanel)
  const setSlidePanel        = useUIStore(s => s.setSlidePanel)
  const setCompareRailOpen   = useUIStore(s => s.setCompareRailOpen)
  const markProjectSaved     = useUIStore(s => s.markProjectSaved)
  const theme                = useUIStore(s => s.theme)
  const density              = useUIStore(s => s.density)
  const toggleTheme          = useUIStore(s => s.toggleTheme)
  const toggleDensity        = useUIStore(s => s.toggleDensity)

  // Project list — shared cache with App.tsx + Sidebar.
  const { data: projects = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
    staleTime: 10_000,
  })

  // Snapshots for the active project — empty list while no project is open.
  const { data: snapshots = [] } = useQuery({
    queryKey: ['snapshots-list', currentProject],
    queryFn: () => currentProject
      ? projectsApi.listSnapshots(currentProject)
      : Promise.resolve([]),
    enabled: !!currentProject,
    staleTime: 5_000,
  })

  // Asset queries — only fetched when user opens palette in 'all' mode so
  // first-load isn't paying for them. Each component class is a separate
  // query so they hit the React Query cache shared with the asset tables.
  const enableAssets = mode === 'all'
  const { data: buses = [] }        = useQuery({ queryKey: ['buses'],         queryFn: networkApi.getBuses,         enabled: enableAssets, staleTime: 30_000 })
  const { data: generators = [] }   = useQuery({ queryKey: ['generators'],    queryFn: networkApi.getGenerators,    enabled: enableAssets, staleTime: 30_000 })
  const { data: loads = [] }        = useQuery({ queryKey: ['loads'],         queryFn: networkApi.getLoads,         enabled: enableAssets, staleTime: 30_000 })
  const { data: lines = [] }        = useQuery({ queryKey: ['lines'],         queryFn: networkApi.getLines,         enabled: enableAssets, staleTime: 30_000 })
  const { data: links = [] }        = useQuery({ queryKey: ['links'],         queryFn: networkApi.getLinks,         enabled: enableAssets, staleTime: 30_000 })
  const { data: storageUnits = [] } = useQuery({ queryKey: ['storage_units'], queryFn: networkApi.getStorageUnits,  enabled: enableAssets, staleTime: 30_000 })
  const { data: stores = [] }       = useQuery({ queryKey: ['stores'],        queryFn: networkApi.getStores,        enabled: enableAssets, staleTime: 30_000 })
  const { data: transformers = [] } = useQuery({ queryKey: ['transformers'],  queryFn: networkApi.getTransformers,  enabled: enableAssets, staleTime: 30_000 })

  return useMemo(() => {
    const cmds: Command[] = []

    // ── Actions ──────────────────────────────────────────────────────────
    if (mode === 'all') {
      cmds.push(
        {
          id: 'act-save',
          kind: 'action',
          title: 'Save project',
          subtitle: currentProject ?? 'Will prompt for a name',
          hint: 'Ctrl S',
          icon: <SaveIcon size={14} />,
          run: async () => {
            if (!currentProject) {
              toast('Use header Save button — it prompts for a name')
              return
            }
            await saveProjectQuietly(currentProject, true)
            qc.invalidateQueries({ queryKey: ['undoInfo'] })
            qc.invalidateQueries({ queryKey: ['projects'] })
            toast.success(`Saved · ${currentProject}`)
          },
        },
        {
          id: 'act-overview',
          kind: 'action',
          title: 'Open project overview',
          subtitle: 'KPI summary, recent activity, issues count',
          icon: <LayoutDashboard size={14} />,
          run: () => setSlidePanel('overview'),
        },
        {
          id: 'act-scenarios',
          kind: 'action',
          title: 'Open scenarios panel',
          subtitle: 'Branch / switch between scenario variants of this project',
          icon: <GitBranch size={14} />,
          run: () => setSlidePanel('scenarios'),
        },
        {
          id: 'act-compare',
          kind: 'action',
          title: 'Compare scenarios',
          subtitle: 'Side-by-side KPI / dispatch / objective diff',
          icon: <Layers size={14} />,
          run: () => { setSlidePanel('results'); setCompareRailOpen(true) },
        },
        {
          id: 'act-snapshots',
          kind: 'action',
          title: 'Open snapshots panel',
          icon: <Camera size={14} />,
          run: () => setSlidePanel('snapshots'),
        },
        {
          id: 'act-results',
          kind: 'action',
          title: 'Open results panel',
          icon: <TrendingUp size={14} />,
          run: () => setSlidePanel('results'),
        },
        {
          id: 'act-solver',
          kind: 'action',
          title: 'Open solver settings',
          icon: <Settings2 size={14} />,
          run: () => setSlidePanel('simparams'),
        },
        {
          id: 'act-horizon',
          kind: 'action',
          title: 'Open model horizon',
          icon: <Clock size={14} />,
          run: () => setSlidePanel('horizon'),
        },
        {
          id: 'act-issues',
          kind: 'action',
          title: 'Open issues panel',
          subtitle: 'Validate the current network without solving',
          icon: <Zap size={14} />,
          run: () => setSlidePanel('issues'),
        },
        {
          id: 'act-newproj',
          kind: 'action',
          title: 'New project',
          hint: 'reset network',
          icon: <FilePlus size={14} />,
          run: async () => {
            // Same path as the header's "New" button — clears state and
            // strips the active project marker.
            await networkApi.resetNetwork()
            setCurrentProject(null)
            setProjectName('Untitled')
            invalidateNetworkQueries(qc)
            toast.success('New empty project — give it a name and save')
          },
        },
        {
          id: 'act-toggle-theme',
          kind: 'action',
          // Title describes the NEXT state so the user understands what
          // pressing Enter will do, not just what's currently selected.
          title: theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme',
          subtitle: `Currently: ${theme}`,
          icon: theme === 'light' ? <Moon size={14} /> : <Sun size={14} />,
          run: () => {
            toggleTheme()
            toast.success(`${theme === 'light' ? 'Dark' : 'Light'} theme on`)
          },
        },
        {
          id: 'act-toggle-density',
          kind: 'action',
          title: density === 'comfortable' ? 'Switch to compact density' : 'Switch to comfortable density',
          subtitle: `Currently: ${density}`,
          icon: density === 'comfortable' ? <Rows3 size={14} /> : <Rows2 size={14} />,
          run: () => {
            toggleDensity()
            toast.success(`${density === 'comfortable' ? 'Compact' : 'Comfortable'} density on`)
          },
        },
      )
    }

    // ── Projects ─────────────────────────────────────────────────────────
    // All known projects on disk, plus the active one if missing (defensive).
    for (const p of projects) {
      const isCurrent = p.name === currentProject
      cmds.push({
        id: `proj-${p.name}`,
        kind: 'project',
        title: p.name,
        subtitle: isCurrent ? 'Currently open' : 'Switch to this project',
        hint: formatRelativeTime(p.created_at) ?? undefined,
        icon: <FolderOpen size={14} className={isCurrent ? 'text-accent' : ''} />,
        run: async () => {
          if (isCurrent) {
            toast('Already on this project', { icon: '·' })
            return
          }
          // Mirror Sidebar.handleOpenPick — auto-save outgoing, load incoming.
          const tId = toast.loading(`Opening '${p.name}'…`)
          try {
            // Abort any in-flight solve first; otherwise the lock-holding
            // worker would make both save_project and load_project time
            // out at axios' 30s limit.
            const stopped = await abortRunningSim()
            if (!stopped) {
              toast.error('Could not abort the running simulation. Try again in a moment.', { id: tId })
              return
            }
            if (currentProject && currentProject !== p.name) {
              await saveProjectQuietly(currentProject)
            }
            await projectsApi.load(p.name)
            invalidateNetworkQueries(qc)
            setCurrentProject(p.name)
            setProjectName(p.name)
            appLog('INFO', `Opened '${p.name}' via command palette`)
            toast.success(`Opened '${p.name}'`, { id: tId })
          } catch (e) {
            toast.error(`Could not open '${p.name}'`, { id: tId })
            appLog('ERROR', `Palette open '${p.name}': ${String((e as Error).message ?? e)}`)
          }
        },
      })
    }

    if (mode === 'projects') return cmds

    // ── Snapshots (active project only) ──────────────────────────────────
    for (const s of snapshots) {
      cmds.push({
        id: `snap-${s.id}`,
        kind: 'snapshot',
        title: s.label || s.id,
        subtitle: `${s.bus_count} buses · ${s.snapshot_count} snapshots${s.has_results ? ' · solved' : ''}`,
        hint: formatRelativeTime(s.created_at) ?? undefined,
        icon: <RotateCcw size={14} />,
        run: async () => {
          if (!currentProject) return
          const tId = toast.loading(`Restoring '${s.label || s.id}'…`)
          try {
            await projectsApi.restoreSnapshot(currentProject, s.id)
            invalidateNetworkQueries(qc)
            qc.invalidateQueries({ queryKey: ['snapshots-list', currentProject] })
            markProjectSaved(currentProject)
            toast.success(`Restored '${s.label || s.id}'`, { id: tId })
          } catch (e) {
            toast.error('Restore failed', { id: tId })
            appLog('ERROR', `Palette restore: ${String((e as Error).message ?? e)}`)
          }
        },
      })
    }

    // ── Assets ───────────────────────────────────────────────────────────
    // Aggregate everything into typed rows. We capture the type + name so
    // selecting an asset row navigates the existing UI (right-panel
    // selection + the relevant bottom-tab).
    const pushAsset = (type: string, name: string, sub: string) => {
      cmds.push({
        id: `asset-${type}-${name}`,
        kind: 'asset',
        title: name,
        subtitle: `${type} · ${sub}`,
        icon: <span className="text-[10px] font-mono">{type[0]}</span>,
        run: () => {
          setSelectedComponent({ type, name })
          openRightPanel()
          setHighlightedComponent({ type, name })
        },
      })
    }
    for (const b of buses as Bus[]) {
      pushAsset('Bus', b.name, `${b.v_nom ?? '?'} kV${b.carrier && b.carrier !== 'AC' ? ` · ${b.carrier}` : ''}`)
    }
    for (const g of generators as Generator[]) {
      pushAsset('Generator', g.name, `${g.carrier ?? '?'} · ${g.p_nom ?? 0} MW @ ${g.bus}`)
    }
    for (const l of loads as Load[]) {
      pushAsset('Load', l.name, `${l.p_set ?? 0} MW @ ${l.bus}`)
    }
    for (const ln of lines as Line[]) {
      pushAsset('Line', ln.name, `${ln.bus0} → ${ln.bus1}`)
    }
    for (const k of links as Link[]) {
      pushAsset('Link', k.name, `${k.bus0} → ${k.bus1}${k.carrier ? ` · ${k.carrier}` : ''}`)
    }
    for (const su of storageUnits as StorageUnit[]) {
      pushAsset('Storage', su.name, `${su.p_nom ?? 0} MW · ${su.max_hours ?? '?'} h @ ${su.bus}`)
    }
    for (const st of stores as Store[]) {
      pushAsset('Store', st.name, `${st.e_nom ?? 0} MWh @ ${st.bus}`)
    }
    for (const t of transformers as Transformer[]) {
      pushAsset('Transformer', t.name, `${t.bus0} → ${t.bus1}`)
    }

    return cmds
  }, [
    mode, projects, snapshots,
    buses, generators, loads, lines, links, storageUnits, stores, transformers,
    qc, currentProject,
    // Setters are stable references from zustand so they don't trigger
    // re-runs in practice, but listing them documents the closure's surface.
    setCurrentProject, setProjectName, setSelectedComponent,
    setHighlightedComponent, openRightPanel, setSlidePanel, setCompareRailOpen, markProjectSaved,
    theme, density, toggleTheme, toggleDensity,
  ])
}

// ── Fuzzy filter ─────────────────────────────────────────────────────────────

// Lightweight subsequence-with-bonus scorer. Avoids pulling in a fuse.js-class
// dependency for a feature this small — we already match VS Code-style well
// enough on the typical 50-200-row command set. Score 0 = no match.
function score(needle: string, hay: string): number {
  if (!needle) return 1
  const n = needle.toLowerCase()
  const h = hay.toLowerCase()
  // Substring is the cheapest strong signal.
  const idx = h.indexOf(n)
  if (idx !== -1) {
    // Earlier matches and word-boundary matches score higher.
    const wordStart = idx === 0 || /[\s_-]/.test(h[idx - 1] ?? '')
    return 1000 - idx + (wordStart ? 500 : 0) + (n.length * 5)
  }
  // Fallback: subsequence match. Each character contributes a smaller
  // score; consecutive matches get a bonus.
  let s = 0
  let hi = 0
  let lastHit = -2
  for (let ni = 0; ni < n.length; ni++) {
    while (hi < h.length && h[hi] !== n[ni]) hi++
    if (hi === h.length) return 0
    s += hi - lastHit === 1 ? 8 : 2
    lastHit = hi
    hi++
  }
  return s
}

function fuzzyFilter(cmds: Command[], query: string): Command[] {
  if (!query.trim()) {
    // No query: keep the original order — actions first, then projects, etc.
    // (Final grouping in the renderer handles section ordering anyway.)
    return cmds
  }
  return cmds
    .map(c => ({ c, s: Math.max(score(query, c.title), score(query, c.subtitle ?? '') * 0.6) }))
    .filter(x => x.s > 0)
    .sort((a, b) => b.s - a.s)
    .slice(0, 50)
    .map(x => x.c)
}
