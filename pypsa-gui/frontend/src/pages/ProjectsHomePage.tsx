import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { projectsApi, type UnclaimedProject } from '../api/projects'
import type { ProjectInfo } from '../api/types'
import { useAuth } from '../auth/AuthProvider'
import { getPostLoginPath } from '../auth/resume'
import { useUIStore } from '../store/uiStore'
import { formatRelativeTime } from '../utils/projectActions'
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
  = 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#7ef0c0] '
  + 'focus-visible:ring-offset-2 focus-visible:ring-offset-[#07130f]'

const PRIMARY_BUTTON
  = 'inline-flex items-center justify-center gap-2 rounded-[14px] '
  + 'bg-[linear-gradient(135deg,#7ef0c0,#23c78d_55%,#12a774)] px-4 py-2.5 text-sm font-bold '
  + 'text-[#04140e] shadow-[0_14px_34px_rgba(35,199,141,0.28)] transition '
  + 'hover:brightness-105 motion-safe:hover:-translate-y-px '
  + 'disabled:cursor-not-allowed disabled:opacity-60 disabled:shadow-none '
  + `disabled:hover:translate-y-0 ${FOCUS_RING}`

const GHOST_BUTTON
  = 'inline-flex items-center justify-center gap-2 rounded-[14px] border border-white/14 '
  + 'bg-[rgba(8,20,16,0.6)] px-4 py-2.5 text-sm font-medium text-[#eaf3ee] transition '
  + `hover:border-[rgba(53,211,154,0.45)] hover:bg-[rgba(12,30,24,0.85)] ${FOCUS_RING}`

const GLASS_PANEL
  = 'rounded-[24px] border border-white/14 bg-[rgba(8,20,16,0.78)] '
  + 'shadow-[0_30px_80px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.08)] '
  + 'backdrop-blur-[26px]'

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
    <span className="inline-flex items-center gap-2 rounded-full border border-white/14 bg-[rgba(9,24,19,0.55)] px-3 py-1.5 text-[11px] uppercase tracking-[0.14em] text-[#9fb4a9]">
      {pulse && (
        <span
          aria-hidden="true"
          className="h-[7px] w-[7px] rounded-full bg-[#35d39a] shadow-[0_0_10px_rgba(53,211,154,0.9)] motion-safe:animate-pulse"
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
    neutral: 'border-white/12 bg-white/[0.05] text-[#9fb4a9]',
    mint: 'border-[rgba(53,211,154,0.35)] bg-[rgba(53,211,154,0.12)] text-[#8ff0c5]',
    warn: 'border-[rgba(255,180,120,0.35)] bg-[rgba(255,167,79,0.12)] text-[#f6c68a]',
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
    <div className="flex flex-wrap items-center gap-2 text-xs text-[#c6d8ce]">
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
      {updated && <span className="text-[#9fb4a9]">Updated {updated}</span>}
    </div>
  )
}

export default function ProjectsHomePage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const lastProjectId = useUIStore(s => s.lastProjectId)
  const { logout, user } = useAuth()
  const [launching, setLaunching] = useState<{ key: string; name: string } | null>(null)
  const [importing, setImporting] = useState<readonly string[]>([])
  const [feedback, setFeedback] = useState<Record<string, RowFeedback>>({})
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

  async function handleLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="relative h-dvh overflow-y-auto bg-[#07130f] text-[#eaf3ee] [color-scheme:dark]">
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
        <div className="absolute inset-0 bg-[radial-gradient(1100px_700px_at_10%_-10%,rgba(53,211,154,0.12),transparent_62%),linear-gradient(180deg,rgba(7,19,15,0.88)_0%,rgba(7,19,15,0.72)_42%,rgba(7,19,15,0.95)_100%)]" />
        <div className="absolute inset-0 [background-image:linear-gradient(rgba(53,211,154,0.05)_1px,transparent_1px),linear-gradient(90deg,rgba(53,211,154,0.05)_1px,transparent_1px)] [background-size:64px_64px] [mask-image:radial-gradient(900px_620px_at_18%_0%,#000_0%,transparent_78%)]" />
      </div>

      <div className="relative mx-auto flex w-full max-w-6xl flex-col gap-8 px-4 pb-20 pt-6 sm:px-6 sm:pt-8 lg:px-8">
        <header className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <span
              aria-hidden="true"
              className="grid h-9 w-9 shrink-0 place-items-center rounded-[11px] bg-[linear-gradient(140deg,#35d39a,#0e8f66_70%)] text-sm font-black text-[#04140e] shadow-[0_8px_28px_rgba(53,211,154,0.35)]"
            >
              P
            </span>
            <div className="min-w-0 leading-tight">
              <div className="text-[15px] font-bold tracking-[-0.01em]">PyPSA Studio</div>
              <div className="truncate text-xs text-[#9fb4a9]">
                {user?.email ?? 'Local session'}
              </div>
            </div>
          </div>
          <nav aria-label="Account" className="flex flex-wrap items-center gap-2">
            <Link className={PRIMARY_BUTTON} to="/app?mode=new">
              New project
            </Link>
            {hasAdminConsoleAccess(user) && (
              <Link className={GHOST_BUTTON} to="/admin">
                Open admin
              </Link>
            )}
            <button className={GHOST_BUTTON} onClick={handleLogout} type="button">
              Sign out
            </button>
          </nav>
        </header>

        <div className="space-y-3">
          <Eyebrow>Your workspace</Eyebrow>
          <h1 className="text-3xl font-semibold tracking-[-0.035em] sm:text-[2.6rem] sm:leading-[1.05]">
            Projects
          </h1>
          <p className="max-w-[52ch] text-sm leading-6 text-[#9fb4a9]">
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
              className="pointer-events-none absolute -right-24 -top-28 h-64 w-64 rounded-full bg-[radial-gradient(circle,rgba(53,211,154,0.20),transparent_70%)]"
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
                <p className="text-sm text-[#9fb4a9]">{projectKindLabel(resumeProject)}</p>
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

        <section aria-labelledby="projects-heading" className="space-y-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div className="space-y-2">
              <Eyebrow>Accessible roots</Eyebrow>
              <h2 className="text-xl font-semibold tracking-[-0.02em] sm:text-2xl" id="projects-heading">
                Projects you can open
              </h2>
              <p className="max-w-[60ch] text-sm leading-6 text-[#9fb4a9]">
                Root projects are listed here; their scenario branches stay reachable inside the
                workbench.
              </p>
            </div>
            {rootProjects.length > 0 && (
              <span className="text-xs uppercase tracking-[0.14em] text-[#9fb4a9]">
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
              <div className="rounded-[20px] border border-dashed border-white/14 bg-[rgba(8,20,16,0.5)] px-5 py-8 text-sm text-[#9fb4a9]">
                Loading accessible projects…
              </div>
            ) : rootProjects.length === 0 ? (
              <div className="rounded-[20px] border border-dashed border-white/14 bg-[rgba(8,20,16,0.5)] px-5 py-8 text-sm text-[#9fb4a9]">
                No saved root projects yet.{' '}
                <Link
                  className={`font-medium text-[#8ff0c5] underline underline-offset-4 hover:text-[#b7f7db] ${FOCUS_RING} rounded-sm`}
                  to="/app?mode=new"
                >
                  Start a new project
                </Link>{' '}
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
                            ? 'border-[rgba(53,211,154,0.55)] bg-[rgba(12,30,24,0.9)] shadow-[0_0_0_1px_rgba(53,211,154,0.35),0_30px_70px_rgba(0,0,0,0.55)] motion-safe:-translate-y-1 motion-safe:scale-[1.015]'
                            : 'border-white/12 bg-[rgba(8,20,16,0.7)] shadow-[0_18px_50px_rgba(0,0,0,0.35)] hover:border-[rgba(53,211,154,0.4)] hover:bg-[rgba(10,26,20,0.82)] motion-safe:hover:-translate-y-0.5'
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
                          className="inline-flex items-center gap-2 text-sm font-medium text-[#8ff0c5] transition group-hover:gap-3"
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
              <p className="max-w-[70ch] text-sm leading-6 text-[#9fb4a9]">
                These projects were saved before multi-user accounts existed, so they do not belong
                to any workspace yet. Importing one moves it — and its scenarios — into yours.
              </p>
            </div>

            <div aria-live="polite">
              {imported.map(([name, result]) => (
                <div
                  className="mt-4 rounded-[13px] border border-[rgba(53,211,154,0.45)] bg-[rgba(24,92,66,0.35)] px-4 py-3 text-sm leading-6 text-[#8ff0c5]"
                  key={name}
                >
                  <p>{result.message}</p>
                  {result.warnings.length > 0 && (
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-[#f6c68a]">
                      {result.warnings.map(warning => (
                        <li key={warning}>{warning}</li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>

            {unclaimedRows.length === 0 && (
              <p className="mt-4 text-sm leading-6 text-[#9fb4a9]">
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
                    className="rounded-[18px] border border-white/10 bg-[rgba(6,17,13,0.66)] p-4"
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
                          <p className="text-sm leading-6 text-[#9fb4a9]">{row.scenario_description}</p>
                        )}
                        {!row.has_network && (
                          <p className="text-xs leading-5 text-[#f6c68a]">
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
                        className="mt-3 rounded-[13px] border border-[rgba(255,139,139,0.4)] bg-[rgba(120,32,32,0.3)] px-3 py-2.5 text-sm leading-6 text-[#ff8b8b]"
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
          className="pypsa-fade-in fixed inset-0 z-50 grid place-items-center bg-[rgba(4,12,9,0.74)] backdrop-blur-[6px]"
          role="status"
        >
          <div className="flex flex-col items-center gap-4 px-6 text-center">
            <span
              aria-hidden="true"
              className="h-9 w-9 rounded-full border-2 border-[rgba(53,211,154,0.25)] border-t-[#35d39a] motion-safe:animate-spin"
            />
            <span className="text-[11px] uppercase tracking-[0.18em] text-[#9fb4a9]">Opening</span>
            <span className="max-w-[24ch] break-words text-2xl font-semibold tracking-[-0.02em]">
              {launching.name}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
