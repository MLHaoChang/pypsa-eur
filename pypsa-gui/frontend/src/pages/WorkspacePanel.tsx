import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle, ArrowRight, Building2, Clock, GitBranch, Layers,
  LayoutGrid, Lock, LockOpen, Plus, Users,
} from 'lucide-react'
import toast from 'react-hot-toast'
import { projectsApi } from '../api/projects'
import type { ProjectInfo } from '../api/types'
import { useAuth } from '../auth/AuthProvider'
import { useAuthMode } from '../auth/AuthModeProvider'
import AssignMembersDialog from '../layout/AssignMembersDialog'
import { useUIStore } from '../store/uiStore'
import { switchToProject } from '../utils/projectActions'
import { PageBody, PageSection, Btn, Tag } from '../components/PageKit'
import { buildScenarioForest, type ScenarioNode } from './ScenariosPanel'

/**
 * Workspace panel — the workbench's single view of "which project am I in,
 * who else is in it, and how do I get somewhere else".
 *
 * Replaces the old sidebar Project block's single-user framing ("New project /
 * Open from disk / Import / From template"), which is now on the projects home
 * where creating a project actually belongs. Four bands:
 *
 *   1. Identity + state — name, root-vs-branch, organisation, and the edit
 *      lock UP FRONT. Previously the lock only appeared as a banner AFTER a
 *      mutation was refused, which is the wrong moment to learn about it.
 *   2. Scenario tree    — the forest built from `parent_project` pointers,
 *      with branch / compare inline.
 *   3. People           — the assign-members dialog promoted out of a hidden
 *      modal, with the whole-tree ACL stated rather than implied.
 *   4. Move between projects — recents plus the way back to Projects home.
 *
 * Note the caveat rendered in band 1: the backend's active network is
 * process-global, so two people editing DIFFERENT projects against the same
 * backend share in-memory state. The locks make that safe in practice, but the
 * panel says so plainly rather than implying full isolation.
 */
export default function WorkspacePanel() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { user } = useAuth()
  const { authEnabled } = useAuthMode()
  const currentProject = useUIStore(s => s.currentProject)
  const readOnly = useUIStore(s => s.readOnly)
  const lockHolderEmail = useUIStore(s => s.lockHolderEmail)
  const recents = useUIStore(s => s.recents)
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const [membersFor, setMembersFor] = useState<string | null>(null)

  const { data: projects = [], isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
    staleTime: 30_000,
  })

  const active = useMemo(
    () => projects.find(p => p.name === currentProject) ?? null,
    [projects, currentProject],
  )

  // The tree ROOT owns membership and the lock — a branch inherits both. Walk
  // parent pointers rather than assuming one level, and bound the walk so a
  // cycle in the data can't hang the panel.
  const root = useMemo(() => {
    if (!active) return null
    const byName = new Map(projects.map(p => [p.name, p]))
    let node: ProjectInfo = active
    for (let hops = 0; hops < 64; hops += 1) {
      const parentName = node.parent_project
      if (!parentName || parentName === node.name) break
      const parent = byName.get(parentName)
      if (!parent) break
      node = parent
    }
    return node
  }, [active, projects])

  // Only the branch the user is in, so the tree stays readable on a workspace
  // with many unrelated roots. The full forest lives on the projects home.
  const forest = useMemo(() => {
    if (!root) return []
    return buildScenarioForest(projects).filter(n => n.project.name === root.name)
  }, [projects, root])

  const recentOthers = useMemo(
    () => recents.filter(name => name !== currentProject).slice(0, 5),
    [recents, currentProject],
  )

  async function open(name: string) {
    try {
      await switchToProject(name, qc)
    } catch (error) {
      toast.error(`Could not open “${name}”: ${(error as Error).message}`)
    }
  }

  if (!currentProject) {
    return (
      <PageBody>
        <PageSection title="No project open">
          <div className="px-4 py-6 text-center">
            <p className="text-xs text-muted mb-4">
              Open a project to see its ownership, lock state, and scenario tree.
            </p>
            <Btn onClick={() => navigate('/projects')} variant="primary">
              <LayoutGrid size={13} /> Go to Projects home
            </Btn>
          </div>
        </PageSection>
      </PageBody>
    )
  }

  const isBranch = Boolean(active?.parent_project && active.parent_project !== active.name)

  return (
    <PageBody>
      {/* ── Band 1: identity + state ──────────────────────────────────── */}
      <PageSection
        title="This project"
        right={(
          <Btn onClick={() => navigate('/projects')} title="Browse every project you can access">
            <LayoutGrid size={13} /> Projects home
          </Btn>
        )}
      >
        <div className="px-4 py-3.5 flex flex-col gap-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[15px] font-semibold text-text tracking-[-0.01em] break-all">
              {currentProject}
            </span>
            <Tag tone={isBranch ? 'purple' : 'accent'}>{isBranch ? 'scenario branch' : 'root'}</Tag>
          </div>

          {isBranch && active?.parent_project && (
            <button
              className="self-start inline-flex items-center gap-1.5 text-[11px] text-muted hover:text-accent transition-colors"
              onClick={() => open(active.parent_project!)}
              type="button"
            >
              <GitBranch size={12} /> branched from {active.parent_project}
            </button>
          )}

          {/* Organisation identity. `AuthUser` carries `org_id` (a UUID) but no
              org NAME, and the only endpoint that resolves one is admin-only —
              so name the signed-in user and their role rather than printing a
              UUID at them. Swap in the org name here if a non-admin lookup
              ever lands. */}
          <div className="flex items-center gap-1.5 text-[11px] text-muted">
            <Building2 className="shrink-0" size={12} />
            <span className="truncate">{user?.email ?? 'Local session'}</span>
            {user?.role && <Tag tone="neutral">{user.role}</Tag>}
            {root && root.name !== currentProject && (
              <span className="text-ink-400 truncate">· tree root {root.name}</span>
            )}
          </div>

          {/* The lock chip, up front. Before this panel the only lock signal
              was a banner AFTER an edit was refused. */}
          <div
            className={`inline-flex items-start gap-2 self-start rounded-[7px] border px-2.5 py-1.5 text-[11px] ${
              readOnly
                ? 'border-warn/40 bg-warn/10 text-warn'
                : 'border-accent/30 bg-accent/10 text-accent'
            }`}
          >
            {readOnly ? <Lock className="mt-px shrink-0" size={12} /> : <LockOpen className="mt-px shrink-0" size={12} />}
            <span>
              {readOnly
                ? lockHolderEmail
                  ? <>Read-only — <span className="font-semibold">{lockHolderEmail}</span> is editing</>
                  : 'Read-only — the edit lock could not be acquired'
                : 'You hold the edit lock'}
            </span>
          </div>

          {/* Stated plainly rather than implied. The backend keeps ONE resident
              pypsa.Network per process. */}
          <p className="flex items-start gap-1.5 text-[10.5px] leading-[1.5] text-ink-400">
            <AlertTriangle className="mt-0.5 shrink-0" size={11} />
            <span>
              The backend holds one active network per process, so people working on
              different projects against the same server share that slot. Edit locks
              keep this safe in practice — but it is not full isolation.
            </span>
          </p>
        </div>
      </PageSection>

      {/* ── Band 2: scenario tree ─────────────────────────────────────── */}
      <PageSection
        title="Scenario tree"
        count={forest.length ? countNodes(forest[0]) : 0}
        right={(
          <Btn
            onClick={() => setSlidePanel('scenarios')}
            title="Full scenario panel — branch, compare, delete"
          >
            <Layers size={13} /> Manage
          </Btn>
        )}
      >
        {isLoading ? (
          <p className="px-4 py-4 text-[11px] text-muted">Loading…</p>
        ) : forest.length === 0 ? (
          <p className="px-4 py-4 text-[11px] text-muted">
            No scenario tree yet — this project has no branches.
          </p>
        ) : (
          <ul className="list-none m-0 p-0">
            {flatten(forest[0]).map(node => (
              <TreeRow
                key={node.project.name}
                current={node.project.name === currentProject}
                node={node}
                onOpen={() => open(node.project.name)}
              />
            ))}
          </ul>
        )}
      </PageSection>

      {/* ── Band 3: people ────────────────────────────────────────────── */}
      <PageSection
        title="People"
        right={authEnabled ? (
          <Btn
            onClick={() => setMembersFor(root?.id ?? root?.name ?? currentProject)}
            title="Add or remove members"
          >
            <Plus size={13} /> Members
          </Btn>
        ) : undefined}
      >
        <div className="px-4 py-3.5 flex flex-col gap-2">
          <MemberList
            enabled={authEnabled}
            projectId={root?.id ?? root?.name ?? currentProject}
          />
          <p className="flex items-start gap-1.5 text-[10.5px] leading-[1.5] text-ink-400">
            <Users className="mt-0.5 shrink-0" size={11} />
            <span>
              Access is granted on the tree root
              {root ? <> (<span className="font-medium text-muted">{root.name}</span>)</> : null}
              {' '}and applies to <span className="font-medium text-muted">every branch under it</span> —
              adding someone here gives them all the scenarios too.
            </span>
          </p>
        </div>
      </PageSection>

      {/* ── Band 4: move between projects ─────────────────────────────── */}
      <PageSection title="Move between projects" count={recentOthers.length}>
        {recentOthers.length === 0 ? (
          <p className="px-4 py-4 text-[11px] text-muted">
            No other recent projects in this workspace yet.
          </p>
        ) : (
          <ul className="list-none m-0 p-0">
            {recentOthers.map(name => (
              <li key={name}>
                <button
                  className="w-full flex items-center gap-2.5 px-4 py-2 text-left hover:bg-accent/5 transition-colors"
                  onClick={() => open(name)}
                  type="button"
                >
                  <Clock className="text-ink-400 shrink-0" size={13} />
                  <span className="flex-1 min-w-0 truncate text-[11.5px] text-text">{name}</span>
                  <ArrowRight className="text-ink-400 shrink-0" size={12} />
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="px-4 py-3 border-t border-border">
          <Btn onClick={() => navigate('/projects')} variant="primary">
            <LayoutGrid size={13} /> Projects home — create, import, duplicate
          </Btn>
        </div>
      </PageSection>

      {membersFor && (
        <AssignMembersDialog
          onClose={() => setMembersFor(null)}
          projectId={membersFor}
          projectLabel={root?.name ?? currentProject}
        />
      )}
    </PageBody>
  )
}

// ── helpers ──────────────────────────────────────────────────────────────────

function countNodes(node: ScenarioNode): number {
  return 1 + node.children.reduce((sum, child) => sum + countNodes(child), 0)
}

/** Depth-first flatten so the tree renders as indented rows. */
function flatten(node: ScenarioNode): ScenarioNode[] {
  return [node, ...node.children.flatMap(flatten)]
}

function TreeRow({ node, current, onOpen }: {
  node: ScenarioNode
  current: boolean
  onOpen: () => void
}) {
  const { project, depth } = node
  const solved = project.objective != null
  return (
    <li>
      <button
        className={`w-full flex items-center gap-2 py-2 pr-4 text-left transition-colors ${
          current ? 'bg-accent/8' : 'hover:bg-accent/5'
        }`}
        disabled={current}
        onClick={onOpen}
        style={{ paddingLeft: 16 + depth * 14 }}
        type="button"
      >
        <GitBranch className={current ? 'text-accent shrink-0' : 'text-ink-400 shrink-0'} size={12} />
        <span className={`flex-1 min-w-0 truncate text-[11.5px] ${current ? 'text-accent font-semibold' : 'text-text'}`}>
          {project.name}
        </span>
        {solved ? <Tag tone="ok">solved</Tag> : <Tag tone="neutral">unsolved</Tag>}
        {current && <span className="text-[9.5px] text-muted">open</span>}
      </button>
    </li>
  )
}

function MemberList({ projectId, enabled }: { projectId: string; enabled: boolean }) {
  // Gated on `enabled` rather than firing and handling the failure: with auth
  // off the endpoint 400s, and the global axios interceptor toasts EVERY error
  // it is not told to skip. Rendering this panel would pop "Project membership
  // requires authentication to be enabled" at a user who never asked about
  // membership. `retry: false` covers the auth-on-but-refused case.
  const { data: members, isError, isLoading } = useQuery({
    queryKey: ['project-members', projectId],
    queryFn: () => projectsApi.getMembers(projectId),
    enabled,
    retry: false,
    staleTime: 15_000,
  })

  if (!enabled) {
    return (
      <p className="text-[11px] text-muted">
        This server runs in single-user mode — every project is yours alone.
        Members appear here once the backend is started with auth enabled.
      </p>
    )
  }
  if (isLoading) return <p className="text-[11px] text-muted">Loading members…</p>
  if (isError) {
    return (
      <p className="text-[11px] text-muted">
        Membership is unavailable for this project.
      </p>
    )
  }
  if (!members || members.length === 0) {
    return <p className="text-[11px] text-muted">No members assigned yet.</p>
  }

  return (
    <ul className="list-none m-0 p-0 flex flex-wrap gap-1.5">
      {members.map(member => (
        <li
          className="inline-flex items-center gap-1.5 rounded-full border border-border bg-panel px-2 py-1 text-[10.5px] text-text"
          key={member.user_id}
        >
          <span
            aria-hidden="true"
            className="grid h-4 w-4 place-items-center rounded-full bg-accent/15 text-[8px] font-bold text-accent"
          >
            {(member.email ?? '?').slice(0, 1).toUpperCase()}
          </span>
          {member.email ?? member.user_id}
        </li>
      ))}
    </ul>
  )
}
