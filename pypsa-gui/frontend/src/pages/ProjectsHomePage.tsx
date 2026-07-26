import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { BookOpen, Copy as CopyIcon, FilePlus, Upload } from 'lucide-react'
import toast from 'react-hot-toast'
import { projectsApi, type UnclaimedProject } from '../api/projects'
import { networkApi } from '../api/network'
import type { ProjectInfo } from '../api/types'
import { useAuth } from '../auth/AuthProvider'
import { useAuthMode } from '../auth/AuthModeProvider'
import { redirectAfterLogout } from '../auth/logoutRedirect'
import { getPostLoginPath } from '../auth/resume'
import NewProjectWizard, { type NewProjectTab } from '../layout/NewProjectWizard'
import { useUIStore } from '../store/uiStore'
import { appLog } from '../store/simulationStore'
import { formatRelativeTime, invalidateNetworkQueries } from '../utils/projectActions'
import { hasAdminConsoleAccess } from './admin/helpers'
import {
  countScenarios,
  findProjectByIdentifier,
  formatCount,
  isRootProject,
  projectIdentifiers,
  projectKindLabel,
  scenarioCountLabel,
  shouldShowImportSection,
  shouldShowResume,
  sortProjectsByRecency,
  sortUnclaimedRows,
} from './projectsHome'

// Long enough for the card lift + overlay fade to read as a handoff, short
// enough that it never feels like latency.
const LAUNCH_MS = 320

const PROJECTS_KEY = ['projects']
const UNCLAIMED_KEY = ['projects', 'unclaimed']

const FOCUS_RING
  = 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand-red-soft)] '
  + 'focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--brand-black)]'

const PRIMARY_BUTTON
  = 'inline-flex items-center justify-center gap-2 rounded-[14px] '
  // `image:` hint is load-bearing. Tailwind reads a bare `bg-[var(--x)]` as a
  // background-COLOR, and a gradient is not a valid colour — so the button
  // rendered with no fill at all (near-black label on transparent, plus the
  // shadow glow). The hint forces background-image.
  + 'bg-[image:var(--brand-red-gradient)] px-4 py-2.5 text-sm font-bold '
  + 'text-[var(--brand-on-red)] shadow-[0_14px_34px_rgba(255,82,82,0.28)] transition '
  + 'hover:brightness-105 motion-safe:hover:-translate-y-px '
  + 'disabled:cursor-not-allowed disabled:opacity-60 disabled:shadow-none '
  + `disabled:hover:translate-y-0 ${FOCUS_RING}`

const GHOST_BUTTON
  = 'inline-flex items-center justify-center gap-2 rounded-[14px] border border-white/14 '
  + 'bg-[rgba(18,11,13,0.6)] px-4 py-2.5 text-sm font-medium text-[var(--brand-ink)] transition '
  + `hover:border-[rgba(255,82,82,0.45)] hover:bg-[rgba(26,16,19,0.85)] ${FOCUS_RING}`

const GLASS_PANEL
  = 'rounded-[24px] border border-white/14 bg-[rgba(18,11,13,0.78)] '
  + 'shadow-[0_30px_80px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.08)] '
  + 'backdrop-blur-[26px]'

// The four "Workspace" actions, moved here from the workbench sidebar. They
// map 1:1 onto NewProjectWizard's tabs — the wizard already implements all of
// them, so this band opens it on the right tab rather than reimplementing the
// flows. Creating a project belongs to the workspace, not to whatever network
// happens to be resident in the workbench.
const START_ACTIONS: ReadonlyArray<{
  tab: NewProjectTab
  label: string
  hint: string
  Icon: typeof FilePlus
}> = [
  { tab: 'blank', label: 'New project', hint: 'Start from an empty network', Icon: FilePlus },
  { tab: 'template', label: 'From template', hint: 'Bundled starter networks', Icon: BookOpen },
  { tab: 'file', label: 'Import from disk', hint: '.pypsaproj.zip or .nc', Icon: Upload },
  { tab: 'clone', label: 'Duplicate a project', hint: 'Fork one you can access', Icon: CopyIcon },
]

interface RowFeedback {
  tone: 'ok' | 'error'
  message: string
  warnings: string[]
}

function projectKey(project: ProjectInfo): string {
  return project.id ?? project.name
}

function projectHref(project: ProjectInfo): string {
  return getPostLoginPath(project.id ?? project.name)
}

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

function errorDetail(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  const message = (error as { message?: unknown })?.message
  return typeof message === 'string' && message.trim() ? message : fallback
}

function Eyebrow({ children, pulse = false }: { children: ReactNode; pulse?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-white/14 bg-[rgba(20,13,15,0.55)] px-3 py-1.5 text-[11px] uppercase tracking-[0.14em] text-[var(--brand-ink-dim)]">
      {pulse && (
        <span
          aria-hidden="true"
          className="h-[7px] w-[7px] rounded-full bg-[var(--brand-red)] shadow-[0_0_10px_rgba(255,82,82,0.9)] motion-safe:animate-pulse"
        />
      )}
      {children}
    </span>
  )
}

function Badge({
  children,
  title,
  tone = 'neutral',
}: {
  children: ReactNode
  title?: string
  tone?: 'neutral' | 'mint' | 'warn'
}) {
  const tones = {
    neutral: 'border-white/12 bg-white/[0.05] text-[var(--brand-ink-dim)]',
    mint: 'border-[rgba(255,82,82,0.35)] bg-[rgba(255,82,82,0.12)] text-[var(--brand-red-soft)]',
    warn: 'border-[rgba(246,198,138,0.35)] bg-[rgba(246,198,138,0.12)] text-[var(--brand-warn)]',
  }
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-medium ${tones[tone]}`}
      title={title}
    >
      {children}
    </span>
  )
}

function StatChips({ project, scenarios }: { project: ProjectInfo; scenarios: number }) {
  const updated = formatRelativeTime(project.created_at)
  const scenarioLabel = scenarioCountLabel(scenarios)
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--brand-ink-dim)]">
      <span className="inline-flex items-center rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1">
        {formatCount(project.bus_count, 'bus', 'buses')}
      </span>
      <span className="inline-flex items-center rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1">
        {formatCount(project.snapshot_count, 'snapshot', 'snapshots')}
      </span>
      {scenarioLabel && (
        <span className="inline-flex items-center rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1">
          {scenarioLabel}
        </span>
      )}
      {updated && <span className="text-[var(--brand-ink-dim)]">Updated {updated}</span>}
    </div>
  )
}

export default function ProjectsHomePage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const lastProjectId = useUIStore(s => s.lastProjectId)
  const setCurrentProject = useUIStore(s => s.setCurrentProject)
  const setProjectName = useUIStore(s => s.setProjectName)
  const addTab = useUIStore(s => s.addTab)
  const { logout, user } = useAuth()
  const { authEnabled } = useAuthMode()
  const [launching, setLaunching] = useState<{ key: string; name: string } | null>(null)
  const [importing, setImporting] = useState<readonly string[]>([])
  const [feedback, setFeedback] = useState<Record<string, RowFeedback>>({})
  const [wizardTab, setWizardTab] = useState<NewProjectTab | null>(null)
  const launchTimer = useRef<number | null>(null)

  const { data: projects = [], isLoading } = useQuery({
    queryKey: PROJECTS_KEY,
    queryFn: projectsApi.list,
    staleTime: 10_000,
  })
  // Resolves to `[]` when the backend has no unclaimed bundles AND when the
  // endpoint is missing (auth disabled) — either way the section stays hidden.
  const { data: unclaimed = [] } = useQuery({
    queryKey: UNCLAIMED_KEY,
    queryFn: projectsApi.listUnclaimed,
    staleTime: 10_000,
    retry: false,
  })

  const rootProjects = useMemo(
    () => sortProjectsByRecency(projects.filter(isRootProject)),
    [projects],
  )
  const unclaimedRows = useMemo(() => sortUnclaimedRows(unclaimed), [unclaimed])
  // A successful import deletes the row it started from, so its confirmation
  // (and any warnings) is rendered at section level instead of on the row.
  const imported = useMemo(
    () => Object.entries(feedback).filter(([, result]) => result.tone === 'ok'),
    [feedback],
  )
  const accessibleIds = useMemo(
    () => projects.flatMap(projectIdentifiers),
    [projects],
  )
  const resumeProject = useMemo(
    () => findProjectByIdentifier(projects, lastProjectId),
    [lastProjectId, projects],
  )
  const showResume = shouldShowResume({ lastId: lastProjectId, accessibleIds })

  useEffect(() => () => {
    if (launchTimer.current !== null) window.clearTimeout(launchTimer.current)
  }, [])

  // Keeps the click a real link navigation for modified clicks (new tab,
  // download, middle click) and only takes over the plain case, where the
  // transition plays before the same href is pushed.
  const openProject = useCallback((event: MouseEvent<HTMLAnchorElement>, project: ProjectInfo) => {
    if (event.defaultPrevented) return
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    const href = projectHref(project)
    event.preventDefault()
    if (prefersReducedMotion()) {
      navigate(href)
      return
    }
    try {
      if (launchTimer.current !== null) window.clearTimeout(launchTimer.current)
      setLaunching({ key: projectKey(project), name: project.name })
      launchTimer.current = window.setTimeout(() => navigate(href), LAUNCH_MS)
    } catch {
      // The transition is decorative — never let it strand the user here.
      navigate(href)
    }
  }, [navigate])

  const importProject = useMutation({
    mutationFn: projectsApi.importUnclaimed,
    onMutate: (name: string) => {
      setImporting(current => [...current, name])
      setFeedback(({ [name]: _dropped, ...rest }) => rest)
    },
    onSuccess: async (result, name) => {
      const extra = Math.max(0, result.claimed.length - 1)
      const scenarios = extra > 0 ? ` with ${formatCount(extra, 'scenario', 'scenarios')}` : ''
      setFeedback(current => ({
        ...current,
        [name]: {
          tone: 'ok',
          message: `Imported “${result.root.name}”${scenarios} into your workspace.`,
          warnings: result.warnings,
        },
      }))
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: PROJECTS_KEY }),
        queryClient.invalidateQueries({ queryKey: UNCLAIMED_KEY }),
      ])
    },
    onError: (error, name) => {
      setFeedback(current => ({
        ...current,
        [name]: {
          tone: 'error',
          message: errorDetail(error, `Could not import “${name}”.`),
          warnings: [],
        },
      }))
    },
    onSettled: (_data, _error, name) => {
      setImporting(current => current.filter(pending => pending !== name))
    },
  })

  // "Blank" tab handler. The wizard's other three tabs drive their own
  // mutations and then call onClose, so this only covers create-empty. Mirrors
  // the sidebar's newProjectMut, minus the save-the-active-project step: on
  // this page nothing is resident in the workbench yet.
  const createBlank = useMutation({
    mutationFn: async (name: string) => {
      await networkApi.resetNetwork()
      await projectsApi.save(name, true) // force: the user explicitly named it
      return name
    },
    onSuccess: (name: string) => {
      invalidateNetworkQueries(queryClient, name)
      addTab(name)
      setCurrentProject(name)
      setProjectName(name)
      setWizardTab(null)
      appLog('INFO', `New project created: ${name}`)
      navigate(getPostLoginPath(name))
    },
    onError: (error: Error) => {
      toast.error(`Failed to create project: ${error.message}`)
      appLog('ERROR', `New project failed: ${error.message}`)
    },
  })

  // The template / file / clone tabs land the project on the backend and then
  // close. Refresh the list so the new root shows up in the grid below rather
  // than leaving the user staring at a stale page.
  const closeWizard = useCallback(() => {
    setWizardTab(null)
    queryClient.invalidateQueries({ queryKey: PROJECTS_KEY })
  }, [queryClient])

  async function handleLogout() {
    await logout()
    redirectAfterLogout()
  }

  return (
    // `data-pypsa-surface="brand-dark"` pins the dark token ramp onto this
    // subtree (see index.css). The projects home is always the dark front door
    // — token-driven children like NewProjectWizard must follow it rather than
    // the user's workbench light/dark preference.
    <div
      className="relative h-dvh overflow-y-auto bg-[var(--brand-black)] text-[var(--brand-ink)] [color-scheme:dark]"
      data-pypsa-surface="brand-dark"
    >
      <style>{`
        @keyframes pypsaFadeIn { from { opacity: 0 } to { opacity: 1 } }
        @keyframes pypsaRise { from { opacity: 0; transform: translateY(10px) } to { opacity: 1; transform: none } }
        .pypsa-fade-in { animation: pypsaFadeIn 160ms ease-out both; }
        .pypsa-rise { animation: pypsaRise 340ms cubic-bezier(0.22, 1, 0.36, 1) both; }
        @media (prefers-reduced-motion: reduce) {
          .pypsa-fade-in, .pypsa-rise { animation: none; }
        }
      `}</style>

      <div aria-hidden="true" className="pointer-events-none fixed inset-0">
        <div className="absolute inset-0 bg-[url('/img/login-bg.jpg')] bg-cover bg-[center_55%] opacity-[0.22] blur-[1px] brightness-[1.25] saturate-[1.15]" />
        <div className="absolute inset-0 bg-[radial-gradient(1100px_700px_at_10%_-10%,rgba(255,82,82,0.12),transparent_62%),linear-gradient(180deg,rgba(10,7,8,0.88)_0%,rgba(10,7,8,0.72)_42%,rgba(10,7,8,0.95)_100%)]" />
        <div className="absolute inset-0 [background-image:linear-gradient(rgba(255,82,82,0.05)_1px,transparent_1px),linear-gradient(90deg,rgba(255,82,82,0.05)_1px,transparent_1px)] [background-size:64px_64px] [mask-image:radial-gradient(900px_620px_at_18%_0%,#000_0%,transparent_78%)]" />
      </div>

      <div className="relative mx-auto flex w-full max-w-6xl flex-col gap-8 px-4 pb-20 pt-6 sm:px-6 sm:pt-8 lg:px-8">
        <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <span
              aria-hidden="true"
              className="grid h-9 w-9 shrink-0 place-items-center rounded-[11px] bg-[linear-gradient(140deg,var(--brand-red),var(--brand-red-deeper)_70%)] text-sm font-black text-[var(--brand-on-red)] shadow-[0_8px_28px_rgba(255,82,82,0.35)]"
            >
              P
            </span>
            <div className="min-w-0 leading-tight">
              <div className="text-[15px] font-bold tracking-[-0.01em]">
                <span className="text-[var(--brand-red)]">PyPSA</span> Studio
              </div>
              <div className="truncate text-xs text-[var(--brand-ink-dim)]">
                {user?.email ?? 'Local session'}
              </div>
            </div>
          </div>
          <nav aria-label="Account" className="flex flex-wrap items-center gap-2">
            <button className={PRIMARY_BUTTON} onClick={() => setWizardTab('blank')} type="button">
              New project
            </button>
            {hasAdminConsoleAccess(user) && (
              <Link className={GHOST_BUTTON} to="/admin">
                Open admin
              </Link>
            )}
            {/* Only when there IS a session. With auth off, logout() is a
                no-op and redirectAfterLogout() lands back here — the button
                just appeared broken. */}
            {authEnabled && (
              <button className={GHOST_BUTTON} onClick={handleLogout} type="button">
                Sign out
              </button>
            )}
          </nav>
        </header>

        <div className="space-y-3">
          <Eyebrow>Your workspace</Eyebrow>
          <h1 className="text-3xl font-semibold tracking-[-0.035em] sm:text-[2.6rem] sm:leading-[1.05]">
            Projects
          </h1>
          <p className="max-w-[52ch] text-sm leading-6 text-[var(--brand-ink-dim)]">
            Pick up the study you were last in, open any root project you can access, or bring in
            work that was saved before accounts existed.
          </p>
        </div>

        {showResume && resumeProject && (
          <section
            aria-labelledby="resume-heading"
            className={`pypsa-rise relative overflow-hidden p-6 sm:p-8 ${GLASS_PANEL}`}
          >
            <div
              aria-hidden="true"
              className="pointer-events-none absolute -right-24 -top-28 h-64 w-64 rounded-full bg-[radial-gradient(circle,rgba(255,82,82,0.20),transparent_70%)]"
            />
            <div className="relative flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
              <div className="min-w-0 space-y-3">
                <Eyebrow pulse>Continue where you left off</Eyebrow>
                <h2
                  className="truncate text-[1.75rem] font-semibold tracking-[-0.035em] sm:text-4xl"
                  id="resume-heading"
                >
                  {resumeProject.name}
                </h2>
                <p className="text-sm text-[var(--brand-ink-dim)]">{projectKindLabel(resumeProject)}</p>
                <StatChips
                  project={resumeProject}
                  scenarios={countScenarios(projects, resumeProject.name)}
                />
              </div>
              <Link
                className={`${PRIMARY_BUTTON} w-full px-6 py-3 lg:w-auto`}
                onClick={event => openProject(event, resumeProject)}
                to={projectHref(resumeProject)}
              >
                Resume project
                <span aria-hidden="true">→</span>
              </Link>
            </div>
          </section>
        )}

        <section aria-labelledby="start-heading" className="space-y-4">
          <div className="space-y-2">
            <Eyebrow>Workspace</Eyebrow>
            <h2 className="text-xl font-semibold tracking-[-0.02em] sm:text-2xl" id="start-heading">
              Start a project
            </h2>
            <p className="max-w-[60ch] text-sm leading-6 text-[var(--brand-ink-dim)]">
              Creating, importing and duplicating all live here rather than in the workbench — a
              project belongs to your workspace, not to whichever network happens to be open.
            </p>
          </div>
          <ul className="grid list-none gap-3 p-0 sm:grid-cols-2 xl:grid-cols-4">
            {START_ACTIONS.map(({ tab, label, hint, Icon }) => (
              <li key={tab}>
                <button
                  className={`group flex h-full w-full flex-col items-start gap-2 rounded-[18px] border border-white/12 bg-[rgba(18,11,13,0.7)] p-4 text-left backdrop-blur-[18px] transition duration-200 hover:border-[rgba(255,82,82,0.4)] hover:bg-[rgba(23,14,17,0.85)] motion-safe:hover:-translate-y-0.5 ${FOCUS_RING}`}
                  onClick={() => setWizardTab(tab)}
                  type="button"
                >
                  <Icon aria-hidden="true" className="text-[var(--brand-red)]" size={18} />
                  <span className="text-sm font-semibold tracking-[-0.01em]">{label}</span>
                  <span className="text-xs leading-5 text-[var(--brand-ink-dim)]">{hint}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="projects-heading" className="space-y-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div className="space-y-2">
              <Eyebrow>Accessible roots</Eyebrow>
              <h2 className="text-xl font-semibold tracking-[-0.02em] sm:text-2xl" id="projects-heading">
                Projects you can open
              </h2>
              <p className="max-w-[60ch] text-sm leading-6 text-[var(--brand-ink-dim)]">
                Root projects are listed here; their scenario branches stay reachable inside the
                workbench.
              </p>
            </div>
            {rootProjects.length > 0 && (
              <span className="text-xs uppercase tracking-[0.14em] text-[var(--brand-ink-dim)]">
                {formatCount(rootProjects.length, 'project', 'projects')}
              </span>
            )}
          </div>

          {/* The grid itself stays out of the live region — announcing every
              card on each refetch would bury the state change. */}
          <p aria-live="polite" className="sr-only">
            {isLoading
              ? 'Loading your projects.'
              : rootProjects.length === 0
                ? 'No saved root projects yet.'
                : `${formatCount(rootProjects.length, 'project', 'projects')} available.`}
          </p>

          <div>
            {isLoading ? (
              <div className="rounded-[20px] border border-dashed border-white/14 bg-[rgba(18,11,13,0.5)] px-5 py-8 text-sm text-[var(--brand-ink-dim)]">
                Loading accessible projects…
              </div>
            ) : rootProjects.length === 0 ? (
              <div className="rounded-[20px] border border-dashed border-white/14 bg-[rgba(18,11,13,0.5)] px-5 py-8 text-sm text-[var(--brand-ink-dim)]">
                No saved root projects yet.{' '}
                <button
                  className={`font-medium text-[var(--brand-red-soft)] underline underline-offset-4 hover:text-[var(--brand-ink)] ${FOCUS_RING} rounded-sm`}
                  onClick={() => setWizardTab('blank')}
                  type="button"
                >
                  Start a new project
                </button>{' '}
                to create your first workspace.
              </div>
            ) : (
              <ul className="grid list-none gap-3 p-0 sm:grid-cols-2 xl:grid-cols-3">
                {rootProjects.map((project) => {
                  const key = projectKey(project)
                  const isLaunching = launching?.key === key
                  return (
                    <li key={key}>
                      <article
                        className={`group relative flex h-full flex-col justify-between gap-4 rounded-[20px] border p-5 backdrop-blur-[18px] transition duration-200 ${
                          isLaunching
                            ? 'border-[rgba(255,82,82,0.55)] bg-[rgba(26,16,19,0.9)] shadow-[0_0_0_1px_rgba(255,82,82,0.35),0_30px_70px_rgba(0,0,0,0.55)] motion-safe:-translate-y-1 motion-safe:scale-[1.015]'
                            : 'border-white/12 bg-[rgba(18,11,13,0.7)] shadow-[0_18px_50px_rgba(0,0,0,0.35)] hover:border-[rgba(255,82,82,0.4)] hover:bg-[rgba(23,14,17,0.82)] motion-safe:hover:-translate-y-0.5'
                        }`}
                      >
                        <div className="space-y-3">
                          <div className="flex items-start justify-between gap-2">
                            <h3 className="min-w-0 break-words text-lg font-semibold tracking-[-0.02em]">
                              {project.name}
                            </h3>
                            <Badge>Root</Badge>
                          </div>
                          <StatChips
                            project={project}
                            scenarios={countScenarios(projects, project.name)}
                          />
                        </div>
                        <span
                          aria-hidden="true"
                          className="inline-flex items-center gap-2 text-sm font-medium text-[var(--brand-red-soft)] transition group-hover:gap-3"
                        >
                          {isLaunching ? 'Opening…' : 'Open project'}
                          <span>→</span>
                        </span>
                        <Link
                          className={`absolute inset-0 rounded-[20px] ${FOCUS_RING}`}
                          onClick={event => openProject(event, project)}
                          to={projectHref(project)}
                        >
                          <span className="sr-only">Open {project.name}</span>
                        </Link>
                      </article>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        </section>

        {shouldShowImportSection(unclaimed, imported.length > 0) && (
          <section aria-labelledby="import-heading" className={`p-5 sm:p-7 ${GLASS_PANEL}`}>
            <div className="space-y-2">
              <Eyebrow>Found on this server</Eyebrow>
              <h2 className="text-xl font-semibold tracking-[-0.02em] sm:text-2xl" id="import-heading">
                Import local projects
              </h2>
              <p className="max-w-[70ch] text-sm leading-6 text-[var(--brand-ink-dim)]">
                These projects were saved before multi-user accounts existed, so they do not belong
                to any workspace yet. Importing one moves it — and its scenarios — into yours.
              </p>
            </div>

            <div aria-live="polite">
              {imported.map(([name, result]) => (
                <div
                  className="mt-4 rounded-[13px] border border-[rgba(255,82,82,0.45)] bg-[rgba(255,255,255,0.35)] px-4 py-3 text-sm leading-6 text-[var(--brand-red-soft)]"
                  key={name}
                >
                  <p>{result.message}</p>
                  {result.warnings.length > 0 && (
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-[var(--brand-warn)]">
                      {result.warnings.map(warning => (
                        <li key={warning}>{warning}</li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>

            {unclaimedRows.length === 0 && (
              <p className="mt-4 text-sm leading-6 text-[var(--brand-ink-dim)]">
                Nothing else is waiting to be imported on this server.
              </p>
            )}

            <ul className="mt-5 grid list-none gap-3 p-0 empty:hidden">
              {unclaimedRows.map((row: UnclaimedProject) => {
                const isPending = importing.includes(row.name)
                const failure = feedback[row.name]?.tone === 'error' ? feedback[row.name] : null
                const scenarioLabel = scenarioCountLabel(row.descendant_names.length)
                return (
                  <li
                    className="rounded-[18px] border border-white/10 bg-[rgba(14,9,11,0.66)] p-4"
                    key={row.name}
                  >
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                      <div className="min-w-0 space-y-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="break-words text-base font-semibold tracking-[-0.01em]">
                            {row.name}
                          </span>
                          {scenarioLabel && <Badge tone="mint">{scenarioLabel}</Badge>}
                          {row.parent_project && (
                            <Badge>Scenario of {row.parent_project}</Badge>
                          )}
                          {!row.has_network && (
                            <Badge
                              title="No network file was found in this project folder."
                              tone="warn"
                            >
                              <span aria-hidden="true">⚠</span> No network file
                            </Badge>
                          )}
                        </div>
                        {row.scenario_description && (
                          <p className="text-sm leading-6 text-[var(--brand-ink-dim)]">{row.scenario_description}</p>
                        )}
                        {!row.has_network && (
                          <p className="text-xs leading-5 text-[var(--brand-warn)]">
                            Only metadata was found on disk — this project will import without a
                            network and open empty.
                          </p>
                        )}
                      </div>
                      <button
                        className={`${PRIMARY_BUTTON} w-full sm:w-auto`}
                        disabled={isPending}
                        onClick={() => importProject.mutate(row.name)}
                        type="button"
                      >
                        {isPending ? 'Importing…' : 'Import'}
                      </button>
                    </div>

                    {failure && (
                      <p
                        className="mt-3 rounded-[13px] border border-[rgba(255,139,139,0.4)] bg-[rgba(90,20,22,0.3)] px-3 py-2.5 text-sm leading-6 text-[var(--brand-danger)]"
                        role="alert"
                      >
                        {failure.message}
                      </p>
                    )}
                  </li>
                )
              })}
            </ul>
          </section>
        )}
      </div>

      {launching && (
        <div
          aria-live="polite"
          className="pypsa-fade-in fixed inset-0 z-50 grid place-items-center bg-[rgba(6,4,5,0.74)] backdrop-blur-[6px]"
          role="status"
        >
          <div className="flex flex-col items-center gap-4 px-6 text-center">
            <span
              aria-hidden="true"
              className="h-9 w-9 rounded-full border-2 border-[rgba(255,82,82,0.25)] border-t-[var(--brand-red)] motion-safe:animate-spin"
            />
            <span className="text-[11px] uppercase tracking-[0.18em] text-[var(--brand-ink-dim)]">Opening</span>
            <span className="max-w-[24ch] break-words text-2xl font-semibold tracking-[-0.02em]">
              {launching.name}
            </span>
          </div>
        </div>
      )}

      {wizardTab && (
        <NewProjectWizard
          existingProjects={projects}
          initialTab={wizardTab}
          isPending={createBlank.isPending}
          onClose={closeWizard}
          onConfirm={name => createBlank.mutate(name)}
        />
      )}
    </div>
  )
}
