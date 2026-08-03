import { useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { FolderOpen, TriangleAlert } from 'lucide-react'
import { projectsApi } from '../api/projects'
import type { ProjectInfo } from '../api/types'

/**
 * Filterable list of the projects saved on the backend.
 *
 * Extracted so the Sidebar's "Open project" modal and the project-tab strip's
 * "+" both offer the SAME list with the same filter, badges and empty states.
 * They previously did not: the tab strip could only CREATE, so opening a
 * second project alongside the current one meant going to the sidebar.
 *
 * Owns the list and its filter, nothing else — each call site keeps its own
 * dialog chrome, heading and any extra affordances (the sidebar's
 * browse-for-a-file button, for instance).
 */

export interface ProjectPickerProps {
  currentProject: string | null
  onPick: (name: string) => void
  /**
   * Names already open as tabs. Marked "open" and still clickable — picking
   * one switches to that tab, which is friendlier than disabling a row the
   * user just tried to click.
   */
  alreadyOpen?: readonly string[]
  /** Shown when the backend has no projects at all. */
  emptyHint?: ReactNode
  autoFocusFilter?: boolean
}

const fmtDate = (iso: string) => {
  try {
    return new Date(iso).toLocaleString(undefined,
      { dateStyle: 'short', timeStyle: 'short' })
  } catch { return iso }
}

export default function ProjectPicker({
  currentProject, onPick, alreadyOpen = [], emptyHint, autoFocusFilter,
}: ProjectPickerProps) {
  const { data: projects = [], isLoading, isError } = useQuery({
    queryKey: ['projects'],
    queryFn: projectsApi.list,
    refetchOnMount: 'always',
  })
  const [filter, setFilter] = useState('')
  const open = new Set(alreadyOpen)
  const needle = filter.trim().toLowerCase()
  const filtered = (projects as ProjectInfo[])
    .filter(p => p.name.toLowerCase().includes(needle))

  return (
    <>
      <div className="px-4 py-2 border-b border-border shrink-0">
        <input
          type="text"
          value={filter}
          autoFocus={autoFocusFilter}
          onChange={e => setFilter(e.target.value)}
          placeholder="Filter projects…"
          aria-label="Filter projects"
          className="w-full px-2.5 py-1.5 text-xs border border-border rounded
            focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/20"
        />
      </div>
      <div className="flex-1 overflow-y-auto min-h-0">
        {isLoading ? (
          <p className="p-6 text-xs text-muted text-center animate-pulse">Loading projects…</p>
        ) : isError ? (
          <p className="p-6 text-xs text-danger text-center">
            Could not read the project list. Try again in a moment.
          </p>
        ) : filtered.length === 0 ? (
          <p className="p-6 text-xs text-muted text-center">
            {projects.length === 0
              ? (emptyHint ?? 'No projects saved on the backend yet.')
              : 'No projects match this filter.'}
          </p>
        ) : (
          <ul className="divide-y divide-border/40">
            {filtered.map(p => {
              const isCurrent = p.name === currentProject
              const isOpen = open.has(p.name)
              // The registry row survives a folder deleted in Finder, which
              // this app's human-navigable project layout invites. Opening
              // one 404s, so say so instead of letting the user find out.
              const missing = !!p.missing
              return (
                <li key={p.name}>
                  <button
                    type="button"
                    disabled={missing}
                    onClick={e => { e.stopPropagation(); if (!missing) onPick(p.name) }}
                    title={missing
                      ? 'This project’s files are missing from disk'
                      : isOpen ? `Switch to the open '${p.name}' tab` : `Open '${p.name}'`}
                    className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors
                      ${missing ? 'opacity-50 cursor-not-allowed'
                        : isCurrent ? 'bg-accent/5' : 'hover:bg-accent/5'}`}
                  >
                    {missing
                      ? <TriangleAlert size={14} className="text-warn shrink-0" />
                      : <FolderOpen size={14} className={isCurrent ? 'text-accent' : 'text-muted'} />}
                    <div className="flex-1 min-w-0">
                      <p className={`text-xs font-semibold truncate ${isCurrent ? 'text-accent' : 'text-text'}`}>
                        {p.name}
                        {isCurrent && <span className="ml-1.5 text-[9px] font-normal opacity-70">(current)</span>}
                        {!isCurrent && isOpen && <span className="ml-1.5 text-[9px] font-normal opacity-70">(open)</span>}
                      </p>
                      <p className="text-[10px] text-muted truncate">
                        {missing
                          ? 'Files missing from disk'
                          : `${p.bus_count} buses · ${p.snapshot_count} snapshots · ${fmtDate(p.created_at)}`}
                      </p>
                    </div>
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </>
  )
}
