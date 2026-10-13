import { BarChart2, Clock } from 'lucide-react'
import { SItem, usePanelToggle } from './SidebarRows'

// TIME: the time steps and periods the model covers, and the profiles on them.
export default function TimeSection({ onCloseModal }: { onCloseModal?: () => void }) {
  const { activeSlidePanel, toggle } = usePanelToggle(onCloseModal)
  return (
    <div>
      <SItem icon={<Clock size={15} />} label="Model Horizon"
        title="Define the snapshots (time steps) and investment periods/years the optimisation plans over."
        active={activeSlidePanel === 'horizon'}
        onClick={() => toggle('horizon')}
      />
      <SItem icon={<BarChart2 size={15} />} label="Time Series"
        active={activeSlidePanel === 'timeseries'}
        onClick={() => toggle('timeseries')}
      />
    </div>
  )
}
