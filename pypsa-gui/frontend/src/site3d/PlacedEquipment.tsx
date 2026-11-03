// SC-02d: the placed equipment of a scene-enabled Site, picked for group
// editing or detach. The list is the keyboard path to the same selection
// the canvas makes with Shift+click (useModelProposal.picked). Two or more
// picked: move them all east/north and rotate them about their centre — ONE
// `place_existing` preview. One picked: AssociationPanel loads it into the
// usual "Place equipment" fields. "Detach from scene" previews a `detach`;
// after Apply the receipt offers Undo (`undo_detach`), disabled with its
// reason once the server says the detach can no longer be undone.
import { useEffect, useState } from 'react'
import type { PlaceEntry } from '../api/siteScene'
import NumberField from './NumberField'
import { detachedCopy, editTitle, isNoMove, moveValid, NO_MOVE, type GroupMove } from './groupEdit'
import { useModelProposal } from './useSiteScene'

const primary = 'px-3 py-1 rounded bg-accent text-white font-semibold hover:bg-accent/90 disabled:opacity-50'
const secondary = 'px-3 py-1 rounded border border-border text-text hover:border-accent disabled:opacity-50'
const PICK_HINT = 'Shift+click placed equipment in the view, or tick it here, to move several at once or detach them'
const NOTHING_TO_MOVE = 'Enter a distance or an angle first'
const FIX_FIELDS = 'Fix the fields marked above first'

export default function PlacedEquipment({ siteId, placements, itemName, writable, blockedReason, onPreviewMove, onPreviewDetach, onUndo }: {
  siteId: string
  placements: Readonly<Record<string, PlaceEntry>>
  /** The item's display name, as the Model panel names it (never the raw key). */
  itemName: (key: string) => string
  writable: boolean
  /** Why nothing can be written here (read-only, locked); null when it can. */
  blockedReason: string | null
  onPreviewMove: (keys: string[], move: GroupMove) => void
  onPreviewDetach: (keys: string[]) => void
  onUndo: () => void
}) {
  const picked = useModelProposal(s => s.picked)
  const togglePicked = useModelProposal(s => s.togglePicked)
  const clearPicked = useModelProposal(s => s.clearPicked)
  const lastDetach = useModelProposal(s => s.lastDetach)
  const keys = Object.keys(placements).sort((a, b) => itemName(a).localeCompare(itemName(b)))
  const mine = (picked?.siteId === siteId ? picked.keys : []).filter(k => k in placements)
  const n = mine.length
  const [move, setMove] = useState<GroupMove>(NO_MOVE)
  // Values typed for a group do not carry into one item's fields, or into another group size.
  const group = n >= 2
  useEffect(() => { setMove(NO_MOVE) }, [group, siteId])

  const moveReason = !moveValid(move) ? FIX_FIELDS : isNoMove(move) ? NOTHING_TO_MOVE : blockedReason
  const receipt = lastDetach?.siteId === siteId ? lastDetach : null
  if (keys.length === 0 && !receipt) return null

  return (
    <div className="flex flex-col gap-1.5 border-t border-border pt-2" data-testid="placed-equipment">
      <div className="flex items-center gap-2">
        <span className="text-text font-medium">Placed equipment</span>
        {n > 0 && (
          <>
            <span className="text-[11px] text-muted" data-testid="placed-count">{n} selected</span>
            <button type="button" data-testid="placed-clear" className="text-[11px] text-accent hover:underline"
              onClick={clearPicked}>Clear</button>
          </>
        )}
      </div>
      {keys.length > 0 && (
        // Esc clears the selection only while focus is in this list; it never stops another Esc handler.
        <ul className="flex flex-col gap-0.5" aria-label="Placed equipment" data-testid="placed-list"
          onKeyDown={e => { if (e.key === 'Escape' && n > 0) clearPicked() }}>
          {keys.map(key => (
            <li key={key}>
              <label className="flex items-center gap-1.5 text-[11px] text-text cursor-pointer">
                <input type="checkbox" data-testid={`placed-pick-${key}`} checked={mine.includes(key)}
                  onChange={() => togglePicked(siteId, key)} />
                {itemName(key)}
              </label>
            </li>
          ))}
        </ul>
      )}
      {keys.length > 0 && n === 0 && <span className="text-[11px] text-muted" data-testid="placed-hint">{PICK_HINT}</span>}

      {group && (
        <div className="flex flex-col gap-1.5" data-testid="group-edit">
          <span className="text-text font-medium" data-testid="group-edit-title">{editTitle(n)}</span>
          <div className="flex flex-wrap items-end gap-2">
            <NumberField label="Move east" unit="m" width="w-16" value={move.east} testId="group-east" disabled={!writable}
              error={Number.isFinite(move.east) ? null : 'Enter a number'} onChange={v => setMove(m => ({ ...m, east: v }))} />
            <NumberField label="Move north" unit="m" width="w-16" value={move.north} testId="group-north" disabled={!writable}
              error={Number.isFinite(move.north) ? null : 'Enter a number'} onChange={v => setMove(m => ({ ...m, north: v }))} />
            <NumberField label="Rotate by" unit="°" width="w-16" value={move.rotate} testId="group-rotate" disabled={!writable}
              error={Number.isFinite(move.rotate) ? null : 'Enter a number'} onChange={v => setMove(m => ({ ...m, rotate: v }))} />
          </div>
          <span className="text-[11px] text-muted" data-testid="group-rotate-hint">Turns them together about their centre, clockwise</span>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" data-testid="group-preview" className={primary} disabled={!writable || !!moveReason}
              onClick={() => onPreviewMove(mine, move)}>Preview</button>
          </div>
          {moveReason && <span className="text-[11px] text-muted" data-testid="group-preview-reason">{moveReason}</span>}
        </div>
      )}

      {n > 0 && (
        <div className="flex flex-col gap-1">
          <div>
            <button type="button" data-testid="detach-preview" className={secondary} disabled={!writable || !!blockedReason}
              onClick={() => onPreviewDetach(mine)}>Detach from scene</button>
          </div>
          {blockedReason && <span className="text-[11px] text-muted" data-testid="detach-preview-reason">{blockedReason}</span>}
        </div>
      )}

      {receipt && (
        <div className="flex flex-col gap-0.5" data-testid="detach-receipt">
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-text">{detachedCopy(receipt.count)}</span>
            <button type="button" data-testid="detach-undo" className={secondary}
              disabled={!writable || !!receipt.blocked || !!blockedReason} onClick={onUndo}>Undo</button>
          </div>
          {(receipt.blocked ?? blockedReason) && (
            <span className="text-[11px] text-muted" data-testid="detach-undo-reason">{receipt.blocked ?? blockedReason}</span>
          )}
        </div>
      )}
    </div>
  )
}
