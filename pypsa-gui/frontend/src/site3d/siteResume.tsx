// SC-07 S5b R5 (UX condition 4, U-a–U-d, U-f): the Resume line in the S2
// companion row. It reads the scene GET's own `my_previews` (GK B J4/J5) and
// shows one line only when there is something to act on; an applied row never
// shows on page load. "Check the 3D site" is a fresh scene read, so it works
// with no assistant: applied reads "was applied", anything else "was not
// applied" with "Prepare it again". Words only: no ids or action keys (U4).
import { useState } from 'react'
import type { ScenePreviewRow } from '../api/siteScene'
import { ask } from '../pages/hubDesign/delegate'

/** A preview's action in words, as the line names it ("a placement"). */
export const ACTION_WORDS: Record<string, string> = {
  generate_native: 'set of generated buildings',
  edit_buildings: 'building change',
  bind_targets: 'mapping to the model',
  place_existing: 'placement',
  rebind: 'new mapping',
  detach: 'removal from the site',
  undo_detach: 'return to the site',
}

export const actionWords = (action: string | undefined) => (action && ACTION_WORDS[action]) || 'change'

/** The row to show (unknown before open, then newest: the list is newest first) and how many more qualify. */
export function resumeRow(rows: ScenePreviewRow[] | undefined): { row: ScenePreviewRow; more: number } | null {
  const acting = (rows ?? []).filter(r => r.status === 'outcome_unknown' || r.status === 'open')
  const row = acting.find(r => r.status === 'outcome_unknown') ?? acting[0]
  return row ? { row, more: acting.length - 1 } : null
}

const more = (n: number) => (n > 0 ? ` (${n} more)` : '')

type Checked = { id: string; result: 'applied' | 'not_applied'; readAt: number }

/** A button with no "›": it acts here and goes nowhere (U-a). */
function ActButton({ label, testid, onClick, busy }: { label: string; testid: string; onClick: () => void; busy?: boolean }) {
  return (
    <button type="button" data-testid={testid} onClick={onClick} disabled={busy}
      className={`whitespace-nowrap ${busy ? 'text-muted' : 'text-accent hover:underline'}`}>{label}</button>
  )
}

export default function SiteResume({ siteName, rows, readAt, recheck }: {
  siteName: string
  rows: ScenePreviewRow[] | undefined
  /** When the scene query last read the server (its `dataUpdatedAt`). */
  readAt: number
  /** A fresh scene read: this owner's rows and its read time, or null when the read failed. */
  recheck: () => Promise<{ rows: ScenePreviewRow[]; readAt: number } | null>
}) {
  const [checking, setChecking] = useState<string | null>(null)
  const [answer, setAnswer] = useState<Checked | null>(null)
  const [failedAt, setFailedAt] = useState<number | null>(null)
  // A check's answer lasts until the next fresh read of the scene (U-b, #1183).
  const checked = answer && answer.readAt === readAt ? answer : null
  const failed = failedAt === readAt
  const pick = resumeRow(rows)
  const site = `the site “${siteName}” in Site 3D`

  const check = async (row: ScenePreviewRow) => {
    setChecking(row.id)
    const fresh = await recheck().catch(() => null)
    setChecking(null)
    if (fresh === null) { setFailedAt(readAt); return }
    const now = fresh.rows.find(r => r.id === row.id)
    // Still unknown because the site could not be read: no answer, never "not applied" (U-c, H6).
    if (now?.status === 'outcome_unknown' && now.receipt_read === false) { setFailedAt(fresh.readAt); return }
    setAnswer({ id: row.id, result: now?.status === 'applied' ? 'applied' : 'not_applied', readAt: fresh.readAt })
  }

  let line: string | null = null
  let action: React.ReactNode = null
  if (checked) {
    const what = actionWords((rows ?? []).find(r => r.id === checked.id)?.action ?? pick?.row.action)
    if (checked.result === 'applied') {
      line = `Checked: your ${what} was applied.`
    } else {
      line = `Checked: your ${what} was not applied.`
      action = <ActButton testid="site-resume-prepare-again" label="Prepare it again"
        onClick={() => ask(`Prepare the ${what} for ${site} again.`)} />
    }
  } else if (pick) {
    const { row, more: n } = pick
    const what = actionWords(row.action)
    if (row.status === 'outcome_unknown') {
      line = failed
        ? `Found: a ${what} whose outcome is not known yet${more(n)}. The check could not read the site.`
        : `Found: a ${what} whose outcome is not known yet${more(n)}.`
      action = <ActButton testid="site-resume-check" busy={checking === row.id}
        label={checking === row.id ? 'Checking…' : 'Check the 3D site'} onClick={() => void check(row)} />
    } else {
      line = `Found: a ${what} you prepared, not applied yet${more(n)}.`
      // The one "›" here (U-a): it moves to the chat dock, prefilled, and sends nothing.
      action = <button type="button" data-testid="site-resume-review" className="whitespace-nowrap text-accent hover:underline"
        onClick={() => ask(`Show me the ${what} I prepared for ${site}, so I can review it.`)}>Review it in chat ›</button>
    }
  }
  if (!line) return null
  return (
    <div className="flex basis-full flex-wrap items-center gap-1.5 text-[11px] text-text" data-testid="site-resume">
      <span role="status" data-testid="site-resume-line">{line}</span>
      {action}
    </div>
  )
}
