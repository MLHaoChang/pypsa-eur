// An asset's name as a person reads it (UX overhaul S6, finding G1). Guided
// shows only the display name; Expert keeps the raw id on hover, since that
// is what the tables, the assistant and exports use.
import { useUIStore } from '../store/uiStore'
import { displayName } from '../utils/displayName'

export default function AssetName({ id, className }: { id: string; className?: string }) {
  const expert = useUIStore(s => s.uiMode === 'expert')
  const name = displayName(id)
  return <span className={className} title={expert && name !== id ? id : undefined}>{name}</span>
}
