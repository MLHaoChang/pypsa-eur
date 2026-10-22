import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { FileText } from 'lucide-react'
import EquipmentTableImport from './EquipmentTableImport'
import { useUIStore } from '../store/uiStore'
import { nk } from '../utils/queryKeys'

// Section 7's "Fill values from a PDF…" (data-to-model slice 2, owner AI-567b):
// the asset import dialog in its commercial mode, for the import tariff or a
// PPA contract. It ends back on the block it was opened from.
export default function FillFromPdf({ target }: { target: 'tariff' | 'ppa' }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  return (<>
    <button type="button" data-testid={`fill-from-pdf-${target}`} onClick={() => setOpen(true)}
      className="inline-flex items-center gap-1 text-[11px] text-muted hover:text-accent"
      title={`Set ${target === 'tariff' ? 'tariff rates' : 'contract values'} from an uploaded PDF, each shown with its page and quote`}>
      <FileText size={11} /> Fill values from a PDF…
    </button>
    {open && (
      <EquipmentTableImport commercial={target} onClose={() => setOpen(false)} onApplied={() => {
        const project = useUIStore.getState().currentProject
        void qc.invalidateQueries({ queryKey: nk(project, 'commercial') })
        void qc.invalidateQueries({ queryKey: nk(project, 'results') })
        void qc.invalidateQueries({ queryKey: nk(project, 'flow') })
      }} />
    )}
  </>)
}
