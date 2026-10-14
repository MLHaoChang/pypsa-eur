import { Camera, FileText, GitBranch } from 'lucide-react'
import { SItem, usePanelToggle } from './SidebarRows'

// PROJECT (owner decision Q6a; the step keeps the id `reports`): what the
// project keeps and hands on — the scenarios compared side by side, the saved
// snapshots of the network, and the written reports.
export default function ReportsSection({ onCloseModal }: { onCloseModal?: () => void }) {
  const { activeSlidePanel, toggle } = usePanelToggle(onCloseModal)
  return (
    <div>
      <SItem icon={<GitBranch size={15} />} label="Scenarios"
        active={activeSlidePanel === 'scenarios'}
        onClick={() => toggle('scenarios')}
      />
      <SItem icon={<Camera size={15} />} label="Snapshots"
        title="Saved copies of the network: restore one, or compare it with the network you have now."
        active={activeSlidePanel === 'snapshots'}
        onClick={() => toggle('snapshots')}
      />
      <SItem icon={<FileText size={15} />} label="Reports"
        title="Study reports written from the Energy Hub reference design and the adequacy study; read them here and export to Word."
        active={activeSlidePanel === 'reports'}
        onClick={() => toggle('reports')}
      />
    </div>
  )
}
