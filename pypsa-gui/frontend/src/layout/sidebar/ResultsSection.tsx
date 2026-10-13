import { TrendingUp } from 'lucide-react'
import { SItem, usePanelToggle } from './SidebarRows'

// RESULTS: what the last solve found.
export default function ResultsSection({ onCloseModal }: { onCloseModal?: () => void }) {
  const { activeSlidePanel, toggle } = usePanelToggle(onCloseModal)
  return (
    <div>
      <SItem icon={<TrendingUp size={15} />} label="Results"
        active={activeSlidePanel === 'results'}
        onClick={() => toggle('results')}
      />
    </div>
  )
}
