// The menu on a Projects home card: Open, Rename, Duplicate, Delete.
// Self-contained: the home page passes the callbacks for Open and Duplicate;
// Rename and Delete use the same routes as the project header and the
// Scenarios page (projectsApi.rename / projectsApi.delete).
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { MoreHorizontal } from 'lucide-react'
import toast from 'react-hot-toast'
import { projectsApi } from '../../api/projects'
import client from '../../api/client'
import { networkApi } from '../../api/network'
import { formatApiDetail } from '../../api/client'
import type { ProjectInfo } from '../../api/types'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { useUIStore } from '../../store/uiStore'
import { appLog } from '../../store/simulationStore'
import { invalidateNetworkQueries } from '../../utils/projectActions'
import { nameClashFrom } from '../../utils/nameClashToast'
import { describeDescendants } from '../ScenariosPanel'

/** The viewer role's row field (project-viewer-role): absent today, so every user sees every item. */
export function canChange(project: ProjectInfo): boolean {
  return (project as ProjectInfo & { my_role?: string }).my_role !== 'viewer'
}

export type CardAction = 'open' | 'rename' | 'duplicate' | 'delete'

/** Items offered on a card. A folder that is gone can only be removed; a viewer gets no menu at all. */
export function cardActions(project: ProjectInfo): CardAction[] {
  if (!canChange(project)) return []
  return project.missing ? ['delete'] : ['open', 'rename', 'duplicate', 'delete']
}

const LABEL: Record<CardAction, string> = { open: 'Open', rename: 'Rename', duplicate: 'Duplicate', delete: 'Delete' }

export function ProjectCardMenu({ project, onOpen, onRename, onDuplicate, onDelete }: {
  project: ProjectInfo
  onOpen: () => void
  onRename: () => void
  onDuplicate: () => void
  onDelete: () => void
}) {
  const [open, setOpen] = useState(false)
  const button = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const actions = cardActions(project)

  useEffect(() => {
    if (!open) return
    menu.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus()
    const away = (e: MouseEvent) => {
      const t = e.target as Node
      if (!menu.current?.contains(t) && !button.current?.contains(t)) setOpen(false)
    }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [open])

  if (actions.length === 0) return null

  const close = (refocus: boolean) => { setOpen(false); if (refocus) button.current?.focus() }
  const items = () => Array.from(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])
  const onKey = (e: KeyboardEvent) => {
    const list = items()
    const i = list.indexOf(document.activeElement as HTMLElement)
    if (e.key === 'Escape') { e.preventDefault(); close(true) }
    else if (e.key === 'ArrowDown') { e.preventDefault(); list[(i + 1) % list.length]?.focus() }
    else if (e.key === 'ArrowUp') { e.preventDefault(); list[(i - 1 + list.length) % list.length]?.focus() }
    else if (e.key === 'Home') { e.preventDefault(); list[0]?.focus() }
    else if (e.key === 'End') { e.preventDefault(); list[list.length - 1]?.focus() }
    else if (e.key === 'Tab') setOpen(false)
  }
  const run: Record<CardAction, () => void> = { open: onOpen, rename: onRename, duplicate: onDuplicate, delete: onDelete }

  return (
    <div className="relative z-10" onKeyDown={onKey}>
      <button ref={button} type="button" data-testid={`project-menu-button-${project.name}`}
        aria-haspopup="menu" aria-expanded={open} aria-label={`Actions for ${project.name}`}
        onClick={() => setOpen(o => !o)}
        className="rounded-md p-1 text-[var(--brand-ink-dim)] hover:text-[var(--brand-ink)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--brand-red-soft)]">
        <MoreHorizontal size={18} aria-hidden="true" />
      </button>
      {open && (
        <div ref={menu} role="menu" aria-label={`Actions for ${project.name}`}
          className="absolute right-0 top-full z-20 mt-1 min-w-[9rem] rounded-lg border border-[var(--brand-line)] bg-[var(--brand-surface-2)] py-1 shadow-xl">
          {actions.map(a => (
            <button key={a} type="button" role="menuitem" tabIndex={-1} data-testid={`project-menu-${a}`}
              onClick={() => { close(false); run[a]() }}
              className={`block w-full px-3 py-1.5 text-left text-sm hover:bg-[var(--brand-line)] focus:bg-[var(--brand-line)] focus:outline-none ${
                a === 'delete' ? 'text-[var(--brand-danger)]' : 'text-[var(--brand-ink)]'}`}>
              {LABEL[a]}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/** The refusal for a taken name, in words (curly quotes, no Python repr). */
export function renameRefusal(detail: string, wanted: string): string {
  const taken = nameClashFrom(detail)
  return taken !== null || /already exists/i.test(detail)
    ? `A project named “${taken ?? wanted}” already exists. Pick another name.`
    : detail
}

/** Replaces the card title while renaming. Enter saves, Esc cancels. */
export function InlineRename({ project, onDone }: { project: ProjectInfo; onDone: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState(project.name)
  const [error, setError] = useState<string | null>(null)
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { ref.current?.focus(); ref.current?.select() }, [])

  const save = useMutation({
    // Same route as projectsApi.rename; the refusal shows inline, so no global toast.
    mutationFn: (to: string) => client.post<ProjectInfo>(
      `/projects/${encodeURIComponent(project.id || project.name)}/rename`,
      { new_name: to }, { skipErrorToast: true }).then(r => r.data),
    onSuccess: (_r, to) => {
      const s = useUIStore.getState()
      if (s.currentProject === project.name) s.renameProject(project.name, to)
      qc.invalidateQueries({ queryKey: ['projects'] })
      qc.invalidateQueries({ queryKey: ['changelog'] })
      toast.success(`Renamed to “${to}”`)
      onDone()
    },
    onError: (e, to) => {
      const detail = (e as { response?: { data?: { detail?: unknown } }; message?: string }).response?.data?.detail
      setError(renameRefusal(formatApiDetail(detail, (e as Error).message || 'Rename failed'), to))
    },
  })
  const submit = () => {
    const to = name.trim()
    if (!to) { setError('Type a name for the project.'); return }
    if (to === project.name) { onDone(); return }
    save.mutate(to)
  }

  return (
    <div className="relative z-10 min-w-0 flex-1">
      <input ref={ref} value={name} aria-label={`New name for ${project.name}`} data-testid="project-rename-input"
        aria-invalid={error != null} disabled={save.isPending}
        onChange={e => { setName(e.target.value); setError(null) }}
        onKeyDown={e => {
          if (e.key === 'Enter') { e.preventDefault(); submit() }
          if (e.key === 'Escape') { e.preventDefault(); onDone() }
        }}
        className="w-full rounded-md border border-[var(--brand-line)] bg-transparent px-2 py-1 text-lg font-semibold" />
      {error && <p role="alert" data-testid="project-rename-error" className="mt-1 text-xs text-[var(--brand-danger)]">{error}</p>}
    </div>
  )
}

/** Delete with a confirm step: same route and wording as Scenarios, plus a note when it is the open project. */
export function DeleteProjectDialog({ project, onClose }: { project: ProjectInfo | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [cascade, setCascade] = useState<string | null>(null)
  const name = project?.name ?? ''
  const isOpenProject = useUIStore(s => s.currentProject) === name && name !== ''
  const base = project?.missing
    ? `Remove the registry entry for '${name}'? Its files are already gone.`
    : `Delete '${name}'? This removes its files from disk.`
  const message = cascade ?? (isOpenProject ? `${base} It is the open project, so it closes now.` : base)

  const del = useMutation({
    mutationFn: (withKids: boolean) => projectsApi.delete(project!.id || project!.name, withKids),
    onSuccess: async ({ deleted, failed }) => {
      qc.invalidateQueries({ queryKey: ['projects'] })
      const s = useUIStore.getState()
      if (s.currentProject && deleted.includes(s.currentProject)) {
        try { await networkApi.resetNetwork() } catch { /* best effort */ }
        const gone = s.currentProject
        s.setCurrentProject(null)
        s.setProjectName('Untitled')
        invalidateNetworkQueries(qc, gone)
        appLog('WARN', `Active project '${gone}' was deleted — network reset`)
      }
      for (const n of deleted) qc.removeQueries({ queryKey: ['compare-state', n] })
      if (failed.length > 0) toast.error(`Could not delete: ${failed.join(', ')} (file lock?)`)
      if (deleted.length > 0) {
        toast.success(deleted.length > 1 ? `Deleted '${deleted[0]}' + ${deleted.length - 1} more` : `Deleted '${deleted[0]}'`)
      }
      setCascade(null); onClose()
    },
    onError: (err, withKids) => {
      const e = err as { response?: { status?: number; data?: { detail?: unknown } } }
      if (e.response?.status === 409 && !withKids) { setCascade(describeDescendants(e.response.data?.detail, name)); return }
      setCascade(null); onClose()
      toast.error(`Delete failed: ${formatApiDetail(e.response?.data?.detail, (err as Error).message)}`)
    },
  })

  return (
    <ConfirmDialog
      open={project != null}
      title={cascade ? 'Delete project and descendants' : 'Delete project'}
      message={message}
      confirmLabel={cascade ? 'Delete all' : 'Delete'}
      danger
      pending={del.isPending}
      onConfirm={() => del.mutate(cascade != null)}
      onCancel={() => { setCascade(null); onClose() }}
    />
  )
}
