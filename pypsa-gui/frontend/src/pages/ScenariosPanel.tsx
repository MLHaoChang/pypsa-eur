import { useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { GitBranch, Plus, Trash2, ArrowRight, Layers, ChevronRight } from 'lucide-react'
import { projectsApi } from '../api/projects'
import { networkApi } from '../api/network'
import { useUIStore } from '../store/uiStore'
import { invalidateNetworkQueries, saveProjectQuietly } from '../utils/projectActions'
import { confirmToast } from '../utils/toasts'
import { appLog } from '../store/simulationStore'
import type { ProjectInfo } from '../api/types'
import { PageBody, PageSection, RowGrid, StatCard, Btn, Tag } from '../components/PageKit'

// ── Tree-building helpers ────────────────────────────────────────────────────
// The backend returns a flat list of projects with `parent_project` pointers.
// We reconstruct the forest client-side so the panel can render nested
// children without needing a /tree endpoint. Cheap: linear in the number of
// projects, which is bounded by what fits on the user's disk.

export interface ScenarioNode {
  project: ProjectInfo
  children: ScenarioNode[]
  depth: number
}

export function buildScenarioForest(projects: ProjectInfo[]): ScenarioNode[] {
  // Map by name for O(1) parent lookups.
  const byName = new Map<string, ScenarioNode>()
  for (const p of projects) {
    byName.set(p.name, { project: p, children: [], depth: 0 })
  }
  // First pass — classify nodes into "has valid parent in map" vs "root".
  // Self-parent (A.parent_project === A) is treated as no-parent to avoid
  // a self-cycle that would blow the recursion below. Dangling parent
  // pointers (referencing a name no longer on disk) likewise bubble to the
  // root forest so the scenario doesn't silently disappear.
  const roots: ScenarioNode[] = []
  for (const node of byName.values()) {
    const pp = node.project.parent_project
    const isSelfParent = pp != null && pp === node.project.name
    if (pp && !isSelfParent && byName.has(pp)) {
      byName.get(pp)!.children.push(node)
    } else {
      roots.push(node)
    }
  }
  // Cycle defence: any node not reached from a root after the propagate
  // pass is inside a cycle (mutual A → B → A). Promote one such node per
  // cycle to a root so the cycle's members render at least once — better
  // than silently disappearing from the tree.
  const seen = new Set<string>()
  const propagate = (n: ScenarioNode, d: number) => {
    if (seen.has(n.project.name)) return  // cycle defence; also handles re-walks
    seen.add(n.project.name)
    n.depth = d
    for (const c of n.children) propagate(c, d + 1)
  }
  for (const r of roots) propagate(r, 0)
  for (const node of byName.values()) {
    if (!seen.has(node.project.name)) {
      // Orphaned-by-cycle: detach from parent's children list (so the cycle's
      // own edge doesn't render twice), then treat as root.
      for (const other of byName.values()) {
        other.children = other.children.filter(c => c.project.name !== node.project.name)
      }
      roots.push(node)
      propagate(node, 0)
    }
  }
  // Alphabetise siblings for stable rendering.
  const sortRec = (n: ScenarioNode) => {
    n.children.sort((a, b) => a.project.name.localeCompare(b.project.name))
    for (const c of n.children) sortRec(c)
  }
  roots.sort((a, b) => a.project.name.localeCompare(b.project.name))
  for (const r of roots) sortRec(r)
  return roots
}

// ── Scenario "type" tag ─────────────────────────────────────────────────────
// ProjectInfo has no `type` field — the category (baseline / scenario /
// stress) is encoded as a `[type]` prefix on the scenario_description string,
// so `parseScenType` pulls it back out for the badge and strips it from the
// displayed description.
const SCEN_TYPES = ['baseline', 'scenario', 'stress'] as const
type ScenType = typeof SCEN_TYPES[number]
const SCEN_TYPE_TONE: Record<ScenType, 'accent' | 'purple' | 'warn'> = {
  baseline: 'accent', scenario: 'purple', stress: 'warn',
}
function parseScenType(desc?: string | null): { type: ScenType | null; text: string } {
  if (!desc) return { type: null, text: '' }
  const m = desc.match(/^\[(baseline|scenario|stress)\]\s*([\s\S]*)$/)
  if (m) return { type: m[1] as ScenType, text: m[2] }
  return { type: null, text: desc }
}

// ── Main panel ──────────────────────────────────────────────────────────────

export default function ScenariosPanel() {
  const qc = useQueryClient()
  const currentProject    = useUIStore(s => s.currentProject)
  const setCurrentProject = useUIStore(s => s.setCurrentProject)
  const setProjectName    = useUIStore(s => s.setProjectName)
  const setSlidePanel     = useUIStore(s => s.setSlidePanel)
  const setCompareRailOpen = useUIStore(s => s.setCompareRailOpen)
  const setProjectSwitchInProgress = useUIStore(s => s.setProjectSwitchInProgress)

  const { data: projects = [], isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
    staleTime: 5_000,
    refetchInterval: 10_000,
  })

  const forest = useMemo(() => buildScenarioForest(projects as ProjectInfo[]), [projects])

  // Lineage breadcrumb: walk the parent_project chain from the current
  // project up to its root, root-most first. Used in the panel header so
  // users always see where the active project sits in the tree.
  const lineage = useMemo(() => {
    if (!currentProject) return []
    const byName = new Map((projects as ProjectInfo[]).map(p => [p.name, p]))
    const seen = new Set<string>()
    const chain: ProjectInfo[] = []
    let cursor: string | null | undefined = currentProject
    while (cursor && !seen.has(cursor)) {
      seen.add(cursor)
      const p = byName.get(cursor)
      if (!p) break
      chain.unshift(p)
      cursor = p.parent_project ?? null
    }
    return chain
  }, [currentProject, projects])

  const [creating, setCreating] = useState<{ base: string } | null>(null)
  const [switching, setSwitching] = useState<string | null>(null)

  const switchTo = async (name: string) => {
    if (name === currentProject) {
      toast('Already on this project', { icon: '·' })
      return
    }
    if (switching) return  // drop concurrent clicks while a switch is in flight
    setSwitching(name)
    // Fence autosave for the whole switch: from here until currentProject is
    // updated below, the in-memory network gets swapped to `name` while the
    // store still points at the outgoing project — an autosave tick in that
    // window would write the new network under the old project's folder.
    setProjectSwitchInProgress(true)
    const tId = toast.loading(`Switching to '${name}'…`)
    try {
      // Auto-save the outgoing project so unsaved edits aren't lost.
      // saveProjectQuietly catches and returns false on failure rather than
      // throwing — so we have to check the return value or we'd silently
      // advance to the load and clobber the user's unsaved edits.
      if (currentProject && currentProject !== name) {
        const saved = await saveProjectQuietly(currentProject)
        if (!saved) {
          toast.dismiss(tId)
          const proceed = window.confirm(
            `Couldn't save changes to '${currentProject}'.\n\n` +
            `Switch to '${name}' anyway? Unsaved edits on '${currentProject}' will be lost.`,
          )
          if (!proceed) {
            appLog('WARN', `Switch to '${name}' cancelled — '${currentProject}' did not save`)
            return
          }
        } else {
          // The outgoing project's on-disk state just changed — drop its
          // cached compare-state so an open Compare panel doesn't show
          // pre-save numbers (saveProjectQuietly can't invalidate; it has
          // no QueryClient handle).
          qc.invalidateQueries({ queryKey: ['compare-state', currentProject] })
        }
      }
      await projectsApi.load(name)
      invalidateNetworkQueries(qc)
      setCurrentProject(name)
      setProjectName(name)
      appLog('INFO', `Switched to scenario '${name}'`)
      toast.success(`Now on '${name}'`, { id: tId })
    } catch (e) {
      toast.error(`Could not switch to '${name}'`, { id: tId })
      appLog('ERROR', `Switch to '${name}': ${String((e as Error).message ?? e)}`)
    } finally {
      setSwitching(null)
      setProjectSwitchInProgress(false)
    }
  }

  const deleteMut = useMutation({
    // Returns the full {deleted, failed} response — same shape as Projects.tsx
    // deleteMut so the two pages don't drift.
    mutationFn: (params: { name: string; cascade: boolean }) =>
      projectsApi.delete(params.name, params.cascade),
    onSuccess: async ({ deleted, failed }) => {
      qc.invalidateQueries({ queryKey: ['projects'] })
      // If the active project (or one of its ancestors) was deleted,
      // `currentProject` now dangles — the autosave loop would re-create the
      // deleted directory as a 0-bus shell on its next tick. Reset the
      // in-memory network and clear the active-project markers so nothing
      // resurrects it.
      if (currentProject && deleted.includes(currentProject)) {
        try { await networkApi.resetNetwork() } catch { /* best effort */ }
        setCurrentProject(null)
        setProjectName('Untitled')
        invalidateNetworkQueries(qc)
        appLog('WARN', `Active project '${currentProject}' was deleted — network reset`)
      }
      // Drop cached compare-state for every deleted project.
      for (const n of deleted) {
        qc.removeQueries({ queryKey: ['compare-state', n] })
      }
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
      const e = err as { response?: { status?: number; data?: { detail?: string } } }
      // Only re-prompt for cascade when this was a non-cascade attempt — a
      // 409 on a cascade=true call would otherwise loop the prompt.
      if (e.response?.status === 409 && !params.cascade) {
        const detail = e.response.data?.detail ?? 'has child scenarios'
        confirmToast(
          `${detail} — delete it and all its child scenarios?`,
          () => deleteMut.mutate({ name: params.name, cascade: true }),
          { confirmLabel: 'Delete all', danger: true },
        )
        return
      }
      toast.error(`Delete failed: ${e.response?.data?.detail ?? (err as Error).message}`)
    },
  })

  const projectList = projects as ProjectInfo[]
  const solvedCount = projectList.filter(
    p => p.objective != null && Number.isFinite(p.objective),
  ).length
  const objectives = projectList
    .map(p => p.objective)
    .filter((o): o is number => o != null && Number.isFinite(o))
  const bestObj = objectives.length ? formatObjective(Math.min(...objectives)) : '—'

  return (
    <div className="flex flex-col h-full">
      <PageBody>
        <RowGrid cols={3}>
          <StatCard accent eyebrow="Scenarios" value={projectList.length} sub="project variants" />
          <StatCard eyebrow="Solved" value={solvedCount} sub="have an objective" />
          <StatCard eyebrow="Best objective" value={bestObj} sub="lowest system cost" />
        </RowGrid>

        {/* Lineage breadcrumb — visible only when the active project has at
            least one parent. Each crumb is clickable; clicking switches to
            that ancestor (auto-save current outgoing first). */}
        {lineage.length >= 2 && (
          <div className="flex items-center flex-wrap gap-1.5 text-[11px] bg-bg-2 border border-border rounded-lg px-3 py-2">
            <span className="text-muted font-mono text-[9px] font-bold uppercase tracking-[0.14em]">Lineage</span>
            {lineage.map((p, i) => {
              const isLast = i === lineage.length - 1
              return (
                <span key={p.name} className="flex items-center gap-1.5">
                  {i > 0 && <ChevronRight size={11} className="text-border-3 shrink-0" />}
                  {isLast ? (
                    <span className="font-semibold text-accent">{p.name}</span>
                  ) : (
                    <button
                      onClick={() => switchTo(p.name)}
                      className="text-ink-700 hover:text-accent underline-offset-2 hover:underline transition-colors"
                      title={`Switch to '${p.name}'`}
                    >
                      {p.name}
                    </button>
                  )}
                </span>
              )
            })}
          </div>
        )}

        <PageSection
          title="Scenario tree"
          count={projectList.length}
          hint="variants linked by a parent pointer — switching auto-saves the outgoing"
          right={
            <Btn onClick={() => { setSlidePanel('results'); setCompareRailOpen(true) }} title="Open Results with the comparison rail docked alongside">
              <Layers size={12} /> Compare
            </Btn>
          }
          bodyClassName="p-3"
        >
          {isLoading ? (
            <div className="text-[11.5px] text-muted py-6 text-center">Loading…</div>
          ) : forest.length === 0 ? (
            <div className="text-[11.5px] text-muted py-8 text-center">
              No projects yet. Save a project, then use this panel to branch scenarios from it.
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              {forest.map(root => (
                <ScenarioNodeRow
                  key={root.project.name}
                  node={root}
                  currentProject={currentProject}
                  onSwitch={switchTo}
                  onCreateChild={(base) => setCreating({ base })}
                  onDelete={(name) => deleteMut.mutate({ name, cascade: false })}
                />
              ))}
            </div>
          )}
        </PageSection>
      </PageBody>

      {creating && (
        <CreateScenarioDialog
          base={creating.base}
          onClose={() => setCreating(null)}
          onCreated={(info) => {
            setCreating(null)
            qc.invalidateQueries({ queryKey: ['projects'] })
            // A compare-state for this name could be cached from a prior
            // (cold-miss) compare before the scenario existed — drop it so
            // the Compare panel reads the freshly-written state.
            qc.invalidateQueries({ queryKey: ['compare-state', info.name] })
            toast.success(`Created scenario '${info.name}' from '${creating.base}'`)
            // The new scenario is on disk but the in-memory network is still
            // the parent's state. Don't auto-switch — leave that to the user.
          }}
        />
      )}
    </div>
  )
}

// ── Tree row ────────────────────────────────────────────────────────────────
// Recursive renderer. Pure UI — all mutations are passed in via callbacks.

interface RowProps {
  node: ScenarioNode
  currentProject: string | null
  onSwitch: (name: string) => void
  onCreateChild: (base: string) => void
  onDelete: (name: string) => void
}

function ScenarioNodeRow({ node, currentProject, onSwitch, onCreateChild, onDelete }: RowProps) {
  const isCurrent = node.project.name === currentProject
  const indent = node.depth * 16
  const { type: scenType, text: scenText } = parseScenType(node.project.scenario_description)
  return (
    <>
      <div
        className={`group flex items-center gap-2.5 px-3 py-2.5 rounded-lg border transition-colors ${
          isCurrent
            ? 'border-accent-100 bg-accent-50'
            : 'border-border bg-bg hover:bg-bg-2'
        }`}
        style={{ marginLeft: indent }}
      >
        <GitBranch size={13} className={isCurrent ? 'text-accent shrink-0' : 'text-muted shrink-0'} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-semibold text-text truncate">{node.project.name}</span>
            {isCurrent && <Tag tone="accent">active</Tag>}
            {scenType && <Tag tone={SCEN_TYPE_TONE[scenType]}>{scenType}</Tag>}
          </div>
          {scenText && (
            <div className="text-[11px] text-ink-600 truncate mt-0.5" title={scenText}>
              {scenText}
            </div>
          )}
          <div className="text-[10px] text-muted font-mono mt-0.5">
            {node.project.bus_count} buses · {node.project.snapshot_count} snapshots
            {node.project.objective != null && ` · obj ${formatObjective(node.project.objective)}`}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0 opacity-60 group-hover:opacity-100 transition-opacity">
          {!isCurrent && (
            <button
              onClick={() => onSwitch(node.project.name)}
              className="p-1.5 text-muted hover:text-accent transition-colors rounded"
              title="Switch to this scenario (auto-saves outgoing)"
            >
              <ArrowRight size={13} />
            </button>
          )}
          {/* "Branch a child" is only valid from the ACTIVE project — the
              backend serialises the live in-memory network, so branching
              from a non-active row would carry the wrong topology (and the
              backend now 409s that). Disable on non-active rows with a hint. */}
          <button
            onClick={() => isCurrent && onCreateChild(node.project.name)}
            disabled={!isCurrent}
            className={`p-1.5 transition-colors rounded ${
              isCurrent
                ? 'text-muted hover:text-accent'
                : 'text-ink-300 cursor-not-allowed'
            }`}
            title={isCurrent
              ? 'Branch a child scenario from this project'
              : 'Switch to this scenario first to branch a child from it'}
          >
            <Plus size={13} />
          </button>
          <button
            onClick={() => onDelete(node.project.name)}
            className="p-1.5 text-muted hover:text-danger transition-colors rounded"
            title="Delete this scenario"
          >
            <Trash2 size={13} />
          </button>
        </div>
      </div>
      {node.children.map(child => (
        <ScenarioNodeRow
          key={child.project.name}
          node={child}
          currentProject={currentProject}
          onSwitch={onSwitch}
          onCreateChild={onCreateChild}
          onDelete={onDelete}
        />
      ))}
    </>
  )
}

// ── Create dialog ───────────────────────────────────────────────────────────

interface DialogProps {
  base: string
  onClose: () => void
  onCreated: (info: ProjectInfo) => void
}

function CreateScenarioDialog({ base, onClose, onCreated }: DialogProps) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  // Scenario category — 'baseline' is the canonical reference run; the others
  // are variants. Encoded as a `[type]` prefix on scenario_description since
  // ProjectInfo carries no dedicated field.
  const [scenType, setScenType] = useState<ScenType>('scenario')
  const createMut = useMutation({
    mutationFn: () => {
      const desc = description.trim()
      const tagged = `[${scenType}] ${desc}`.trim()
      return projectsApi.createScenario(base, name.trim(), tagged)
    },
    onSuccess: onCreated,
    onError: (err) => {
      const e = err as { response?: { status?: number; data?: { detail?: string } } }
      toast.error(`Create failed: ${e.response?.data?.detail ?? (err as Error).message}`)
    },
  })

  // Mirrors backend _PROJECT_NAME_RE = ^[A-Za-z0-9_\-. ]{1,64}$ so the user
  // sees the constraint before the network round-trip. Backend re-validates.
  const NAME_RE = /^[A-Za-z0-9_\-. ]{1,64}$/
  const trimmed = name.trim()
  const matchesRe = NAME_RE.test(trimmed)
  const valid = trimmed.length > 0 && trimmed !== base && matchesRe
  const submit = () => { if (valid && !createMut.isPending) createMut.mutate() }
  const hint = trimmed.length === 0
    ? null
    : trimmed === base
    ? 'Scenario name must differ from its base.'
    : !matchesRe
    ? 'Allowed: letters, digits, underscore, hyphen, dot, space (max 64).'
    : null

  return (
    <div
      className="fixed inset-0 z-[400] flex items-center justify-center bg-black/30"
      onClick={onClose}
    >
      <div
        className="bg-bg rounded-lg shadow-2xl w-[440px] max-w-[92vw] border border-border"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-3 py-2 border-b border-border">
          <div className="text-xs font-semibold text-text">New scenario from <span className="text-accent">{base}</span></div>
          <div className="text-[10px] text-muted mt-0.5">
            Saves the current in-memory network as a new project linked to its base. Won't auto-switch.
          </div>
        </div>
        <div className="p-3 space-y-2">
          <label className="block">
            <span className="text-[10px] text-muted">Scenario name</span>
            <input
              type="text"
              value={name}
              maxLength={64}
              autoFocus
              onChange={e => setName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') submit(); else if (e.key === 'Escape') onClose() }}
              placeholder="e.g. high-renewables"
              className="w-full mt-0.5 text-xs bg-bg border border-border rounded px-2 py-1 focus:outline-none focus:border-accent"
            />
            {hint && (
              <span className="text-[10px] text-warn mt-0.5 block">{hint}</span>
            )}
          </label>
          <label className="block">
            <span className="text-[10px] text-muted">Type</span>
            <select
              value={scenType}
              onChange={e => setScenType(e.target.value as ScenType)}
              className="w-full mt-0.5 text-xs bg-bg border border-border rounded px-2 py-1 focus:outline-none focus:border-accent"
            >
              <option value="baseline">Baseline — canonical reference run</option>
              <option value="scenario">Scenario — a named variant</option>
              <option value="stress">Stress test</option>
            </select>
          </label>
          <label className="block">
            <span className="text-[10px] text-muted">Description (optional)</span>
            <textarea
              value={description}
              maxLength={500}
              rows={2}
              onChange={e => setDescription(e.target.value)}
              placeholder="What's different from the base?"
              className="w-full mt-0.5 text-xs bg-bg border border-border rounded px-2 py-1 resize-y focus:outline-none focus:border-accent"
            />
          </label>
        </div>
        <div className="flex justify-end gap-1.5 px-3 py-2 border-t border-border">
          <button
            onClick={onClose}
            className="px-3 py-1 text-xs border border-border rounded text-muted hover:text-text hover:border-text/40 transition-colors"
          >Cancel</button>
          <button
            onClick={submit}
            disabled={!valid || createMut.isPending}
            className="px-3 py-1 text-xs rounded bg-accent text-white disabled:opacity-40 disabled:cursor-not-allowed hover:bg-accent/85 transition-colors"
          >{createMut.isPending ? 'Creating…' : 'Create'}</button>
        </div>
      </div>
    </div>
  )
}

// ── Small formatters ────────────────────────────────────────────────────────

function formatObjective(v: number): string {
  if (!Number.isFinite(v)) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e9) return `€${(v / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `€${(v / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return `€${(v / 1e3).toFixed(2)}k`
  return `€${v.toFixed(0)}`
}
