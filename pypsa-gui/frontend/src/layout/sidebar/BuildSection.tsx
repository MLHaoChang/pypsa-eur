import { useState } from 'react'
import { ChevronDown, Layers, Wand2 } from 'lucide-react'
import { SItem, usePanelToggle } from './SidebarRows'
import AssetPaletteInline from './AssetPaletteInline'

// BUILD: what is in the network and how far each technology may grow.
export default function BuildSection({ onCloseModal }: { onCloseModal?: () => void }) {
  const [assetsOpen, setAssetsOpen] = useState(false)
  const { activeSlidePanel, toggle } = usePanelToggle(onCloseModal)
  return (
    <div>
      <SItem
        icon={<Layers size={15} />}
        label="Assets"
        rightEl={
          <ChevronDown size={11} className="text-ink-400 shrink-0 transition-transform duration-200"
            style={{ transform: assetsOpen ? 'rotate(0deg)' : 'rotate(-90deg)' }} />
        }
        onClick={() => setAssetsOpen(o => !o)}
      />
      {assetsOpen && <AssetPaletteInline />}
      <SItem icon={<Layers size={15} />} label="Capacity Bounds"
        title="Set per-technology minimum/maximum build limits, optionally different for each investment period (vintages)."
        active={activeSlidePanel === 'capacityBounds'}
        onClick={() => toggle('capacityBounds')}
      />
      <SItem icon={<Wand2 size={15} />} label="Profile Studio"
        title="Generate wind/solar capacity-factor profiles for sites (synthetic or ERA5/atlite) and apply them onto generators' p_max_pu."
        active={activeSlidePanel === 'profiles'}
        onClick={() => toggle('profiles')}
      />
    </div>
  )
}
