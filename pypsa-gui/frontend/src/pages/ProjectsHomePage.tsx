import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { projectsApi } from '../api/projects'
import type { ProjectInfo } from '../api/types'
import { useAuth } from '../auth/AuthProvider'
import { getPostLoginPath } from '../auth/resume'
import { useUIStore } from '../store/uiStore'
import { formatRelativeTime } from '../utils/projectActions'
import { hasAdminConsoleAccess } from './admin/helpers'
import {
  findProjectByIdentifier,
  isRootProject,
  projectIdentifiers,
  shouldShowResume,
} from './projectsHome'

function projectHref(project: ProjectInfo): string {
  return getPostLoginPath(project.id ?? project.name)
}

function projectMeta(project: ProjectInfo): string {
  const parts = [
    `${project.bus_count} bus${project.bus_count === 1 ? '' : 'es'}`,
    `${project.snapshot_count} snapshot${project.snapshot_count === 1 ? '' : 's'}`,
  ]
  const saved = formatRelativeTime(project.created_at)
  if (saved) parts.push(`Updated ${saved}`)
  return parts.join(' · ')
}

function projectKind(project: ProjectInfo): string {
  return project.parent_project
    ? `Scenario of ${project.parent_project}`
    : 'Root project'
}

export default function ProjectsHomePage() {
  const navigate = useNavigate()
  const lastProjectId = useUIStore(s => s.lastProjectId)
  const { logout, user } = useAuth()
  const { data: projects = [], isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
    staleTime: 10_000,
  })

  const rootProjects = useMemo(
    () => [...projects].filter(isRootProject).sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)),
    [projects],
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

  async function handleLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-screen bg-[#f3f7f2] text-slate-950">
      <div className="mx-auto flex min-h-screen max-w-7xl flex-col gap-6 px-6 py-6 lg:flex-row lg:px-8 lg:py-8">
        <aside className="flex w-full flex-col justify-between gap-8 rounded-[32px] bg-gradient-to-br from-[#173426] via-[#244b36] to-[#3f6d4f] p-8 text-white shadow-[0_24px_80px_rgba(23,52,38,0.22)] lg:max-w-[360px]">
          <div className="space-y-6">
            <div className="space-y-3">
              <p className="text-xs font-semibold uppercase tracking-[0.24em] text-[#dff6e7]">PyPSA GUI</p>
              <h1 className="text-4xl font-semibold tracking-[-0.03em]">Projects</h1>
              <p className="text-sm leading-6 text-[#d6ecd9]">
                Resume your last study, start a fresh root project, or jump into any accessible workspace.
              </p>
            </div>
            <div className="rounded-3xl border border-white/12 bg-white/8 p-5 backdrop-blur-sm">
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[#dff6e7]">Signed in as</p>
              <p className="mt-2 break-all text-sm font-medium text-white">{user?.email ?? 'unknown user'}</p>
            </div>
            <div className="grid gap-3 text-sm text-[#e2f2e6]">
              <div className="rounded-2xl border border-white/12 bg-white/6 p-4 backdrop-blur-sm">
                <div className="font-medium text-white">Resume-first</div>
                <p className="mt-1 text-xs leading-5 text-[#d6ecd9]">
                  The hero only appears when the last project is still accessible to your account.
                </p>
              </div>
              <div className="rounded-2xl border border-white/12 bg-white/6 p-4 backdrop-blur-sm">
                <div className="font-medium text-white">Root-focused</div>
                <p className="mt-1 text-xs leading-5 text-[#d6ecd9]">
                  Scenarios stay available through resume and inside the workbench, while the home list stays uncluttered.
                </p>
              </div>
            </div>
          </div>

          <div className="space-y-3">
            <Link
              className="inline-flex w-full items-center justify-center rounded-2xl bg-white px-4 py-3 text-sm font-medium text-[#173426] transition hover:bg-[#eef7f0]"
              to="/app?mode=new"
            >
              New project
            </Link>
            {hasAdminConsoleAccess(user) && (
              <Link
                className="inline-flex w-full items-center justify-center rounded-2xl border border-white/18 px-4 py-3 text-sm font-medium text-white transition hover:bg-white/8"
                to="/admin"
              >
                Open admin
              </Link>
            )}
            <button
              className="inline-flex w-full items-center justify-center rounded-2xl border border-white/18 px-4 py-3 text-sm font-medium text-white transition hover:bg-white/8"
              onClick={handleLogout}
              type="button"
            >
              Sign out
            </button>
          </div>
        </aside>

        <main className="flex-1 rounded-[32px] border border-[#d6e1d8] bg-white p-6 shadow-[0_20px_60px_rgba(23,52,38,0.10)] md:p-8">
          <div className="space-y-6">
            {showResume && resumeProject && (
              <section className="rounded-[28px] border border-[#d9e6dc] bg-[#f6faf5] p-6">
                <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[#315840]">
                  Continue where you left off
                </p>
                <div className="mt-4 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                  <div className="space-y-2">
                    <h2 className="text-3xl font-semibold tracking-[-0.03em] text-slate-950">
                      {resumeProject.name}
                    </h2>
                    <p className="text-sm leading-6 text-slate-600">
                      {projectKind(resumeProject)} · {projectMeta(resumeProject)}
                    </p>
                  </div>
                  <Link
                    className="inline-flex items-center justify-center rounded-2xl bg-[#234631] px-5 py-3 text-sm font-medium text-white transition hover:bg-[#1b3928]"
                    to={projectHref(resumeProject)}
                  >
                    Resume project
                  </Link>
                </div>
              </section>
            )}

            <section className="space-y-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                <div className="space-y-1">
                  <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[#315840]">Accessible roots</p>
                  <h2 className="text-2xl font-semibold tracking-[-0.02em] text-slate-950">Projects you can open</h2>
                  <p className="text-sm leading-6 text-slate-600">
                    Root projects stay visible here; scenario branches remain available once you resume or open a workspace.
                  </p>
                </div>
                <Link
                  className="inline-flex items-center justify-center rounded-2xl border border-[#cfd9d1] bg-white px-4 py-3 text-sm font-medium text-slate-700 transition hover:border-[#9db4a6] hover:bg-[#f7faf7]"
                  to="/app?mode=new"
                >
                  New project
                </Link>
              </div>

              {isLoading ? (
                <div className="rounded-3xl border border-dashed border-[#d6e1d8] bg-[#fbfdfb] px-5 py-8 text-sm text-slate-500">
                  Loading accessible projects…
                </div>
              ) : rootProjects.length === 0 ? (
                <div className="rounded-3xl border border-dashed border-[#d6e1d8] bg-[#fbfdfb] px-5 py-8 text-sm text-slate-600">
                  No saved root projects are available yet. Start a new project to create your first workspace.
                </div>
              ) : (
                <div className="grid gap-3">
                  {rootProjects.map(project => (
                    <article
                      key={project.id ?? project.name}
                      className="flex flex-col gap-4 rounded-3xl border border-[#d6e1d8] bg-white px-5 py-4 transition hover:border-[#abc0b0] hover:bg-[#fbfdfb] md:flex-row md:items-center md:justify-between"
                    >
                      <div className="space-y-1">
                        <h3 className="text-lg font-semibold tracking-[-0.02em] text-slate-950">{project.name}</h3>
                        <p className="text-sm text-slate-600">{projectMeta(project)}</p>
                      </div>
                      <Link
                        className="inline-flex items-center justify-center rounded-2xl border border-[#cfd9d1] bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition hover:border-[#9db4a6] hover:bg-[#f7faf7]"
                        to={projectHref(project)}
                      >
                        Open project
                      </Link>
                    </article>
                  ))}
                </div>
              )}
            </section>
          </div>
        </main>
      </div>
    </div>
  )
}
