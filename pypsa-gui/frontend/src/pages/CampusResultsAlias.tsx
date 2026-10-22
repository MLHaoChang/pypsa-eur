// The Grid study results have one home: Results › AC power flow & grid (W10 R2,
// UX-flow rule 1). The `campusResults` panel stays only as an address, so the
// "Next: results" link, the run toast, the Guided "Open the grid checks" and the
// sidebar row keep working; opening it shows the matching Results tab instead.
import { useEffect } from 'react'
import { useUIStore } from '../store/uiStore'

export default function CampusResultsAlias({ tab = 'grid_code' }: { tab?: 'grid_code' | 'electrical_design' }) {
  useEffect(() => {
    const ui = useUIStore.getState()
    ui.setSlidePanel('results')
    ui.requestResultsTab(tab)
  }, [tab])
  return null
}
