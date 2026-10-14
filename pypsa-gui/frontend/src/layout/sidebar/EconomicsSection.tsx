import { Coins } from 'lucide-react'
import { SItem, usePanelToggle } from './SidebarRows'

// ECONOMICS: the inputs that decide what the model charges (plan
// 2026-10-14-economics-in-expert.md): the rates and price basis, and what
// every asset costs. One panel; its tabs hold the rest.
export default function EconomicsSection({ onCloseModal }: { onCloseModal?: () => void }) {
  const { activeSlidePanel, toggle } = usePanelToggle(onCloseModal)
  return (
    <div>
      <SItem icon={<Coins size={15} />} label="Economics"
        title="Discount rate, money year and price basis, and the overnight cost of every asset."
        active={activeSlidePanel === 'economics'}
        onClick={() => toggle('economics')}
      />
    </div>
  )
}
