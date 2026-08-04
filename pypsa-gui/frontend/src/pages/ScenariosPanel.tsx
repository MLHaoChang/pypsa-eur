import { useId, useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { GitBranch, Pencil, Plus, Trash2, ArrowRight, Layers, ChevronRight } from 'lucide-react'
import { projectsApi } from '../api/projects'
import { networkApi } from '../api/network'
import { formatApiDetail } from '../api/client'
import { useUIStore } from '../store/uiStore'
import { invalidateNetworkQueries, saveProjectQuietly, switchToProject } from '../utils/projectActions'
import { evaluateMutation } from '../utils/mutationGuard'
import { confirmToast } from '../utils/toasts'
import {
  SCEN_TYPES, SCEN_TYPE_LABEL, resolveScenType, type ScenType,
} from '../utils/scenarioType'
import { appLog } from '../store/simulationStore'
import type { ProjectInfo } from '../api/types'
import { PageBody, PageSection, RowGrid, StatCard, Btn, Tag } from '../components/PageKit'
import { Dialog } from '../components/Dialog'

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
// The encoding (a `[type]` prefix on scenario_description) now lives in
// `utils/scenarioType` because this panel was not the only surface rendering
// a description — it was just the only one that knew to strip the marker.
// Only the tone mapping is panel-local: it names PageKit tags.
const SCEN_TYPE_TONE: Record<ScenType, 'accent' | 'purple' | 'warn'> = {
  baseline: 'accent', scenario: 'purple', stress: 'warn',
}

// ── Cascade-delete prompt ───────────────────────────────────────────────────

/**
 * The confirm-prompt text for "this project still has children".
 *
 * The backend refuses a non-cascade delete with a STRUCTURED 409 detail —
 * `{error_kind, message, descendants}` — and the panel used to interpolate
 * that object straight into a template literal, so the user was asked
 * "[object Object] — delete it and all its child scenarios?".
 *
 * Its `message` field is not the fallback either: it ends with "Pass
 * ?cascade=true to delete recursively", which is a note to an API client, not
 * something to show someone who is looking at the button that does it. So the
 * `descendants` array — which is why the detail is structured in the first
 * place — is what we render, and `message` is never surfaced.
 */
export function describeDescendants(detail: unknown, name: string): string {
  const kids = (detail && typeof detail === 'object' && Array.isArray((detail as { descendants?: unknown }).descendants))
    ? (detail as { descendants: unknown[] }).descendants.filter((d): d is string => typeof d === 'string')
    : []
  if (kids.length === 0) {
    // Shape we don't recognise. Still a real 409, so still ask — just without
    // the names we couldn't read.
    return `'${name}' still has child scenarios. Delete it and all of them?`
  }
  const shown = kids.slice(0, 5).join(', ')
  const rest = kids.length > 5 ? `, +${kids.length - 5} more` : ''
  const noun = kids.length === 1 ? 'child scenario' : 'child scenarios'
  return `'${name}' has ${kids.length} ${noun} (${shown}${rest}). Delete it and all of them?`
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
  // Read-only when another user holds the active project's edit lock (auth
  // mode). Mutating actions (branch a scenario, delete) are gated on it.
  const readOnly = useUIStore(s => s.readOnly)

  const { data: projects = [], isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
    staleTime: 5_000,
    refetchInterval: 10_000,
  })

  const forest = useMemo(() => buildScenarioForest(projects as ProjectInfo[]), [projects])

  // Map project name → stable UUID (when known). Auth-mode routes are
  // UUID-backed; we prefer the id (falling back to the name for legacy
  // single-user mode) for create/delete/switch API calls so those routes
  // resolve unambiguously, while the UI keeps displaying human-friendly names.
  const idByName = useMemo(
    () => new Map((projects as ProjectInfo[]).map(p => [p.name, p.id ?? null])),
    [projects],
  )
  const apiIdFor = (name: string): string => idByName.get(name) || name

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

  // `base` is the human-friendly parent name (header display); `baseId` is the
  // UUID-backed route key (id||name) the createScenario call uses.
  const [creating, setCreating] = useState<{ base: string; baseId: string } | null>(null)
  const [switching, setSwitching] = useState<string | null>(null)
  const [editing, setEditing] = useState<ProjectInfo | null>(null)

  // Shared read-only guard for the panel's mutating actions. Returns true when
  // the action may proceed; otherwise toasts and returns false.
  //
  // The edit lock is held on ONE project — the active one — and protects its
  // in-memory state. It says nothing about a row the lock-holder never loaded:
  // deleting a different project touches only that project's own bundle, and
  // the backend gates it on the ACL (`can_delete_project`), not on any lock.
  // Gating every row on the active project's flag meant holding the lock on A
  // offered to delete B (which the backend then refused), while being
  // read-only on A blocked deleting a B nobody was editing at all.
  const guardMutation = (target: string): boolean => {
    if (target !== currentProject) return true
    const verdict = evaluateMutation(readOnly)
    if (!verdict.allowed) toast.error(verdict.blockedMessage!)
    return verdict.allowed
  }

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
      // Auto-save the outgoing project so unsaved edits aren't lost, with this
      // panel's unique confirm-on-failure UX (the shared switchToProject saves
      // best-effort without prompting). saveProjectQuietly catches + returns
      // false on failure rather than throwing.
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
          // pre-save numbers.
          qc.invalidateQueries({ queryKey: ['compare-state', currentProject] })
        }
      }
      // B8 instant switch: activate (pointer swap) + re-key reactive queries to
      // the target's RETAINED cache. switchToProject re-saves the (just-saved)
      // outgoing project — idempotent — then does the swap; NO component-table
      // refetch (deliberately not invalidateNetworkQueries). Address the target
      // by its UUID when known so the auth-mode activate route resolves it
      // unambiguously (the backend returns the canonical name in `activated`).
      const r = await switchToProject(apiIdFor(name), qc)
      switch (r.status) {
        case 'switched':
          appLog('INFO', `Switched to scenario '${name}'`)
          toast.success(`Now on '${name}'`, { id: tId })
          break
        case 'noop':
          toast.dismiss(tId)
          break
        case 'abort-failed':
          toast.error('Could not abort the running simulation. Try again in a moment.', { id: tId })
          break
        case 'busy-solve':
          toast.error(`Finish or abort the running solve on '${currentProject}' first.`, { id: tId })
          break
        case 'not-found':
          toast.error(`'${name}' no longer exists`, { id: tId })
          break
        case 'error':
          toast.error(`Could not switch to '${name}'`, { id: tId })
          appLog('ERROR', `Switch to '${name}': ${r.message}`)
          break
      }
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
    // deleteMut so the two pages don't drift. `id` is the UUID-backed route key
    // (id||name); `name` is kept for display + the cascade re-prompt.
    mutationFn: (params: { id: string; name: string; cascade: boolean }) =>
      projectsApi.delete(params.id, params.cascade),
    onSuccess: async ({ deleted, failed }) => {
      qc.invalidateQueries({ queryKey: ['projects'] })
      // If the active project (or one of its ancestors) was deleted,
      // `currentProject` now dangles — the autosave loop would re-create the
      // deleted directory as a 0-bus shell on its next tick. Reset the
      // in-memory network and clear the active-project markers so nothing
      // resurrects it.
      if (currentProject && deleted.includes(currentProject)) {
        try { await networkApi.resetNetwork() } catch { /* best effort */ }
        // Capture the deleted project's id before clearing — its cache is now
        // meaningless, so scope the invalidation there (not a global nuke).
        const deletedActive = currentProject
        setCurrentProject(null)
        setProjectName('Untitled')
        invalidateNetworkQueries(qc, deletedActive)
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
      const e = err as { response?: { status?: number; data?: { detail?: unknown } } }
      // Only re-prompt for cascade when this was a non-cascade attempt — a
      // 409 on a cascade=true call would otherwise loop the prompt.
      if (e.response?.status === 409 && !params.cascade) {
        confirmToast(
          describeDescendants(e.response.data?.detail, params.name),
          () => deleteMut.mutate({ id: params.id, name: params.name, cascade: true }),
          { confirmLabel: 'Delete all', danger: true },
        )
        return
      }
      // `formatApiDetail`, not a bare interpolation: FastAPI details are
      // string | validation-array | object, and this endpoint sends an object.
      // `${detail}` on it rendered the literal text "[object Object]".
      toast.error(`Delete failed: ${formatApiDetail(e.response?.data?.detail, (err as Error).message)}`)
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
                  readOnly={readOnly}
                  onSwitch={switchTo}
                  onCreateChild={(base) => { if (guardMutation(base)) setCreating({ base, baseId: apiIdFor(base) }) }}
                  onEdit={(project) => { if (guardMutation(project.name)) setEditing(project) }}
                  onDelete={(name) => { if (guardMutation(name)) deleteMut.mutate({ id: apiIdFor(name), name, cascade: false }) }}
                />
              ))}
            </div>
          )}
        </PageSection>
      </PageBody>

      {editing && (
        <EditScenarioDialog
          project={editing}
          onClose={() => setEditing(null)}
          onSaved={(info) => {
            setEditing(null)
            qc.invalidateQueries({ queryKey: ['projects'] })
            // The compare rail renders the description alongside the KPIs.
            qc.invalidateQueries({ queryKey: ['compare-state', info.name] })
            toast.success(`Updated '${info.name}'`)
          }}
        />
      )}

      {creating && (
        <CreateScenarioDialog
          base={creating.base}
          baseId={creating.baseId}
          baseIsActive={creating.base === currentProject}
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
  // When true, another user holds the active project's edit lock — the
  // mutating affordances (branch, delete) are disabled with a hint.
  readOnly: boolean
  onSwitch: (name: string) => void
  onCreateChild: (base: string) => void
  onEdit: (project: ProjectInfo) => void
  onDelete: (name: string) => void
}

function ScenarioNodeRow({ node, currentProject, readOnly, onSwitch, onCreateChild, onEdit, onDelete }: RowProps) {
  const isCurrent = node.project.name === currentProject
  const indent = node.depth * 16
  const { type: scenType, text: scenText } = resolveScenType(node.project)
  // The registry row outlives a folder deleted in Finder. Switching to one
  // 404s and branching one has nothing to copy, so both are refused here —
  // matching the "+" tab's project picker, which already greys these out.
  const missing = node.project.missing === true
  // The lock is held on the ACTIVE project only; it has no bearing on a row
  // the holder never loaded. See `guardMutation` for the full reasoning.
  const lockedRow = readOnly && isCurrent
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
            {missing && <Tag tone="err">files missing</Tag>}
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
              disabled={missing}
              className={`p-1.5 transition-colors rounded ${
                missing ? 'text-ink-300 cursor-not-allowed' : 'text-muted hover:text-accent'
              }`}
              title={missing
                ? "This project's folder is no longer on disk — it cannot be opened"
                : 'Switch to this scenario (auto-saves outgoing)'}
            >
              <ArrowRight size={13} />
            </button>
          )}
          {/* Branching used to be offered on the ACTIVE row only, because the
              backend once serialised the live in-memory network and a
              non-active base would have carried the wrong topology. It no
              longer does: `_create_scenario_db` copies the BASE's own on-disk
              bundle, precisely so a project can be branched WITHOUT first
              loading it into the process-global network. The restriction
              outlived its reason, and cost a full project switch — reloading
              a 26k-snapshot network — for every branch. */}
          <button
            onClick={() => onCreateChild(node.project.name)}
            disabled={lockedRow || missing}
            className={`p-1.5 transition-colors rounded ${
              lockedRow || missing
                ? 'text-ink-300 cursor-not-allowed'
                : 'text-muted hover:text-accent'
            }`}
            title={missing
              ? "This project's folder is no longer on disk — there is nothing to branch"
              : lockedRow
              ? 'Read-only — another user is editing this project'
              : isCurrent
              ? 'Branch a child scenario from this project (saves it first)'
              : 'Branch a child scenario from this project'}
          >
            <Plus size={13} />
          </button>
          {/* Editable even when the files are gone — the label lives in the
              registry row, which is precisely what is still there. */}
          <button
            onClick={() => onEdit(node.project)}
            disabled={lockedRow}
            className={`p-1.5 transition-colors rounded ${
              lockedRow ? 'text-ink-300 cursor-not-allowed' : 'text-muted hover:text-accent'
            }`}
            title={lockedRow
              ? 'Read-only — another user is editing this project'
              : 'Edit this scenario\u2019s type and description'}
          >
            <Pencil size={13} />
          </button>
          {/* Deletable even when its files are gone: the registry row is
              exactly what wants clearing in that case. */}
          <button
            onClick={() => onDelete(node.project.name)}
            disabled={lockedRow}
            className={`p-1.5 transition-colors rounded ${
              lockedRow ? 'text-ink-300 cursor-not-allowed' : 'text-muted hover:text-danger'
            }`}
            title={lockedRow
              ? 'Read-only — another user is editing this project'
              : 'Delete this scenario'}
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
          readOnly={readOnly}
          onSwitch={onSwitch}
          onCreateChild={onCreateChild}
          onEdit={onEdit}
          onDelete={onDelete}
        />
      ))}
    </>
  )
}

// ── Shared category picker ──────────────────────────────────────────────────
// Driven off SCEN_TYPES rather than three hand-written <option>s, so the
// create dialog and the edit dialog cannot drift apart, and adding a category
// is one entry in one file.

/**
 * `''` is a real option — "uncategorised" — not a placeholder.
 *
 * The column is nullable and the PATCH route accepts null to clear it, but a
 * select offering only the three categories can never SEND that: every project
 * that passed through this dialog would come out categorised, and a category
 * applied by accident could not be taken off again. It is also what an
 * untouched project genuinely is, so the edit dialog needs it to open
 * truthfully rather than pre-selecting a value the user never chose.
 */
function ScenTypeSelect(
  { value, onChange }: { value: ScenType | ''; onChange: (v: ScenType | '') => void },
) {
  return (
    <label className="block">
      <span className="text-[10px] text-muted">Type</span>
      <select
        value={value}
        onChange={e => onChange(e.target.value as ScenType | '')}
        className="w-full mt-0.5 text-xs bg-bg border border-border rounded px-2 py-1 focus:outline-none focus:border-accent"
      >
        <option value="">— none —</option>
        {SCEN_TYPES.map(t => (
          <option key={t} value={t}>{SCEN_TYPE_LABEL[t]}</option>
        ))}
      </select>
    </label>
  )
}

// ── Edit dialog ─────────────────────────────────────────────────────────────

interface EditDialogProps {
  project: ProjectInfo
  onClose: () => void
  onSaved: (info: ProjectInfo) => void
}

/**
 * Change a project's category and description after the fact.
 *
 * Both were write-once: the description was set in the branch dialog and never
 * again, and the category was a `[type]` prefix INSIDE it, so correcting
 * either meant branching a replacement and deleting the original. A scenario
 * that turns out to be the baseline is an ordinary thing to discover halfway
 * through a study.
 */
function EditScenarioDialog({ project, onClose, onSaved }: EditDialogProps) {
  const titleId = useId()
  const initial = resolveScenType(project)
  const [scenType, setScenType] = useState<ScenType | ''>(initial.type ?? '')
  const [description, setDescription] = useState(initial.text)

  const saveMut = useMutation({
    // Only the fields the user actually CHANGED, using the route's partial
    // semantics — an absent key means "leave alone", `null` means "clear".
    //
    // Sending both unconditionally looked simpler and silently destroyed
    // data: a category this build does not recognise (one added server-side,
    // or written by an older importer) resolves to `type: null`, so the select
    // opens on "— none —", and saving a DESCRIPTION-only edit would have
    // cleared the category the user never touched and could not see.
    mutationFn: () => {
      const patch: { description?: string | null; scenario_type?: string | null } = {}
      if (description.trim() !== initial.text.trim()) {
        patch.description = description.trim() || null
      }
      if ((scenType || null) !== initial.type) patch.scenario_type = scenType || null
      return projectsApi.updateScenario(project.id ?? project.name, patch)
    },
    onSuccess: onSaved,
    onError: (err) => {
      const e = err as { response?: { data?: { detail?: unknown } } }
      toast.error(`Could not save: ${formatApiDetail(e.response?.data?.detail, (err as Error).message)}`)
    },
  })

  const dirty = (scenType || null) !== initial.type
    || description.trim() !== initial.text.trim()

  return (
    <Dialog
      open
      onClose={onClose}
      aria-labelledby={titleId}
      z={400}
      panelClassName="bg-bg rounded-lg shadow-2xl w-[440px] max-w-[92vw] border border-border"
    >
      <div className="px-3 py-2 border-b border-border">
        <div id={titleId} className="text-xs font-semibold text-text">
          Edit <span className="text-accent">{project.name}</span>
        </div>
        <div className="text-[10px] text-muted mt-0.5">
          Label only — the network, its results and the scenario tree are untouched.
        </div>
      </div>
      <div className="p-3 space-y-2">
        <ScenTypeSelect value={scenType} onChange={setScenType} />
        <label className="block">
          <span className="text-[10px] text-muted">Description</span>
          <textarea
            value={description}
            maxLength={500}
            rows={2}
            autoFocus
            onChange={e => setDescription(e.target.value)}
            placeholder="What's different about this one?"
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
          onClick={() => { if (dirty && !saveMut.isPending) saveMut.mutate() }}
          disabled={!dirty || saveMut.isPending}
          className="px-3 py-1 text-xs rounded bg-accent text-white disabled:opacity-40 disabled:cursor-not-allowed hover:bg-accent/85 transition-colors"
        >{saveMut.isPending ? 'Saving…' : 'Save'}</button>
      </div>
    </Dialog>
  )
}

// ── Create dialog ───────────────────────────────────────────────────────────

interface DialogProps {
  // Human-friendly parent name for the header display.
  base: string
  // UUID-backed route key (id||name) used for the createScenario API call.
  baseId: string
  // True when `base` is the project currently loaded in the backend, i.e. the
  // one whose in-memory state can be ahead of its files. Drives the flush.
  baseIsActive: boolean
  onClose: () => void
  onCreated: (info: ProjectInfo) => void
}

function CreateScenarioDialog({ base, baseId, baseIsActive, onClose, onCreated }: DialogProps) {
  const titleId = useId()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  // Scenario category — 'baseline' is the canonical reference run; the others
  // are variants. Encoded as a `[type]` prefix on scenario_description since
  // ProjectInfo carries no dedicated field.
  const [scenType, setScenType] = useState<ScenType | ''>('scenario')
  const createMut = useMutation({
    mutationFn: async () => {
      // Flush the base to disk FIRST when it is the loaded project.
      //
      // `POST /{base}/scenarios` copies the base's on-disk BUNDLE — it never
      // touches the in-memory network (that is what lets a non-active project
      // be branched at all). So without this the child forks from the last
      // AUTOSAVE, up to five minutes behind the network on screen, and the
      // divergence is invisible until the user opens the branch much later
      // and finds their edits missing. The dialog claimed the opposite
      // ("saves the current in-memory network") for as long as the copy has
      // read from disk.
      //
      // Only for the active base: for any other row the on-disk bundle IS the
      // project, and there is nothing resident to flush.
      if (baseIsActive) {
        const saved = await saveProjectQuietly(base)
        if (!saved) {
          // Refuse rather than silently branch from stale files — the whole
          // point of the flush. `saveProjectQuietly` also declines while a
          // solve is running, which is the common way to land here.
          throw new Error(
            `Couldn't save '${base}' first, so the scenario would branch from its `
            + `last saved state instead of what's on screen. `
            + `Finish or abort a running solve, then retry.`,
          )
        }
      }
      return projectsApi.createScenario(
        baseId, name.trim(), description.trim() || null, scenType || null,
      )
    },
    onSuccess: onCreated,
    onError: (err) => {
      const e = err as { response?: { data?: { detail?: unknown } } }
      toast.error(`Create failed: ${formatApiDetail(e.response?.data?.detail, (err as Error).message)}`)
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
    <Dialog
      open
      onClose={onClose}
      aria-labelledby={titleId}
      z={400}
      panelClassName="bg-bg rounded-lg shadow-2xl w-[440px] max-w-[92vw] border border-border"
    >
      <div className="px-3 py-2 border-b border-border">
        <div id={titleId} className="text-xs font-semibold text-text">New scenario from <span className="text-accent">{base}</span></div>
        <div className="text-[10px] text-muted mt-0.5">
          {baseIsActive
            ? <>Saves <span className="text-ink-700">{base}</span> first, then copies it into a new project linked to it. Won't auto-switch.</>
            : <>Copies <span className="text-ink-700">{base}</span> as it is on disk into a new project linked to it. Won't auto-switch.</>}
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
            onKeyDown={e => { if (e.key === 'Enter') submit() }}
            placeholder="e.g. high-renewables"
            className="w-full mt-0.5 text-xs bg-bg border border-border rounded px-2 py-1 focus:outline-none focus:border-accent"
          />
          {hint && (
            <span className="text-[10px] text-warn mt-0.5 block">{hint}</span>
          )}
        </label>
        <ScenTypeSelect value={scenType} onChange={setScenType} />
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
    </Dialog>
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
