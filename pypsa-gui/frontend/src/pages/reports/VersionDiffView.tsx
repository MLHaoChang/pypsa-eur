// The version diff (WP14, plan §Increment 3): what changed between two
// versions of one report, per section.
//
// Two selects (defaults: the previous version against the latest) drive
// `GET …/{id}/versions/{a}/diff/{b}`; the answer is rendered as a table —
// one row per section with a change tag (an icon AND a word: unchanged /
// changed / added / removed, never colour alone), the source in a → b
// (model prose / from evidence / edited by you), the pending instruction
// and the comments the b version carries. Reached from the viewer's version
// switcher ("Compare…"). The same two versions are one query key, so
// switching back never refetches.
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Equal, GitCompareArrows, Minus, Pencil, Plus, X } from 'lucide-react'
import { getVersionDiff, reportErrorMessage, type VersionChange, type VersionDiffSection } from '../../api/reports'
import { Btn, PageSection, Tag } from '../../components/PageKit'
import { SOURCE_LABEL } from './SectionCard'

export const REPORT_DIFF_KEY = (project: string, reportId: string, a: number, b: number) =>
  ['reports', 'diff', project, reportId, a, b] as const

const CHANGE: Record<VersionChange, { label: string; tone: 'neutral' | 'warn' | 'ok' | 'err'; icon: React.ReactNode }> = {
  unchanged: { label: 'unchanged', tone: 'neutral', icon: <Equal size={10} aria-hidden="true" /> },
  changed: { label: 'changed', tone: 'warn', icon: <Pencil size={10} aria-hidden="true" /> },
  added: { label: 'added', tone: 'ok', icon: <Plus size={10} aria-hidden="true" /> },
  removed: { label: 'removed', tone: 'err', icon: <Minus size={10} aria-hidden="true" /> },
}

function sourceLabel(source: string | null): string {
  if (source == null) return '—'
  return (SOURCE_LABEL as Record<string, string>)[source] ?? source
}

function countBy(sections: VersionDiffSection[]): Record<VersionChange, number> {
  const out: Record<VersionChange, number> = { unchanged: 0, changed: 0, added: 0, removed: 0 }
  for (const s of sections) out[s.change] = (out[s.change] ?? 0) + 1
  return out
}

export function VersionDiffView({
  project, reportId, latestVersion, onClose,
}: {
  project: string
  reportId: string
  latestVersion: number
  onClose?: () => void
}) {
  const versions = Array.from({ length: latestVersion }, (_, i) => latestVersion - i)
  const [a, setA] = useState(Math.max(1, latestVersion - 1))
  const [b, setB] = useState(latestVersion)
  const same = a === b

  const diff = useQuery({
    queryKey: REPORT_DIFF_KEY(project, reportId, a, b),
    queryFn: () => getVersionDiff(project, reportId, a, b),
    enabled: !same,
    retry: false,
  })

  const counts = diff.data ? countBy(diff.data.sections) : null

  return (
    <div data-testid="version-diff">
      <PageSection
        title={<span className="inline-flex items-center gap-1.5"><GitCompareArrows size={13} aria-hidden="true" /> Compare versions</span>}
        hint={counts && (
          <span data-testid="version-diff-summary">
            {counts.changed} changed · {counts.added} added · {counts.removed} removed · {counts.unchanged} unchanged
          </span>
        )}
        right={
          <div className="flex items-center gap-2">
            <label className="inline-flex items-center gap-1.5 text-[11px] text-muted">
              from
              <select
                aria-label="Compare from"
                className="px-2 py-1 text-[11px] border border-border rounded bg-bg text-text"
                value={String(a)}
                onChange={e => setA(Number(e.target.value))}
              >
                {versions.map(v => <option key={v} value={String(v)}>v{v}</option>)}
              </select>
            </label>
            <label className="inline-flex items-center gap-1.5 text-[11px] text-muted">
              to
              <select
                aria-label="Compare to"
                className="px-2 py-1 text-[11px] border border-border rounded bg-bg text-text"
                value={String(b)}
                onChange={e => setB(Number(e.target.value))}
              >
                {versions.map(v => <option key={v} value={String(v)}>v{v}{v === latestVersion ? ' (latest)' : ''}</option>)}
              </select>
            </label>
            {onClose && (
              <Btn onClick={onClose} title="Close the comparison" data-testid="version-diff-close">
                <X size={12} /> Close
              </Btn>
            )}
          </div>
        }
        bodyClassName="p-0"
      >
        {same ? (
          <p className="px-4 py-3 text-[11.5px] text-muted" data-testid="version-diff-same">
            Pick two different versions to compare.
          </p>
        ) : diff.isError ? (
          <p className="px-4 py-3 text-[12px] text-danger" data-testid="version-diff-error">
            {reportErrorMessage(diff.error, 'Could not compare the versions')}
          </p>
        ) : !diff.data ? (
          <p className="px-4 py-3 text-[11.5px] text-muted">Comparing v{a} with v{b}…</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-[11.5px]" data-testid="version-diff-table">
              <thead className="text-muted text-left">
                <tr>
                  <th className="border-b border-border px-3 py-1.5 font-semibold">Section</th>
                  <th className="border-b border-border px-3 py-1.5 font-semibold">Change</th>
                  <th className="border-b border-border px-3 py-1.5 font-semibold">Source v{diff.data.a} → v{diff.data.b}</th>
                  <th className="border-b border-border px-3 py-1.5 font-semibold">Pending instruction</th>
                  <th className="border-b border-border px-3 py-1.5 font-semibold">Comments</th>
                </tr>
              </thead>
              <tbody>
                {diff.data.sections.map(s => {
                  const c = CHANGE[s.change] ?? CHANGE.unchanged
                  return (
                    <tr key={s.section_id} data-testid={`diff-row-${s.section_id}`} data-change={s.change} className="align-top">
                      <td className="border-b border-border px-3 py-1.5 text-text">{s.heading}</td>
                      <td className="border-b border-border px-3 py-1.5">
                        <span data-testid="diff-change" className="inline-flex">
                          <Tag tone={c.tone}><span className="inline-flex items-center gap-1">{c.icon}{c.label}</span></Tag>
                        </span>
                      </td>
                      <td className="border-b border-border px-3 py-1.5 text-muted" data-testid="diff-source">
                        {sourceLabel(s.source_a)} → {sourceLabel(s.source_b)}
                      </td>
                      <td className="border-b border-border px-3 py-1.5 text-text" data-testid="diff-pending">
                        {s.pending_instruction?.trim() ? s.pending_instruction : '—'}
                      </td>
                      <td className="border-b border-border px-3 py-1.5 text-muted" data-testid="diff-comments">
                        {s.comments.length > 0 ? (
                          <ul className="list-disc pl-4">{s.comments.map((x, i) => <li key={i}>{x}</li>)}</ul>
                        ) : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </PageSection>
    </div>
  )
}
