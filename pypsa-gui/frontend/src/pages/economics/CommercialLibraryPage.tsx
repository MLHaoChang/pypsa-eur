// The commercial library as one workspace-level page (design C1): managed
// here, picked from inside the Economics editors. After an attach it says where
// the item went and links back to Tariffs & contracts.
import { useUIStore } from '../../store/uiStore'
import LibraryBrowser from '../results/investment/LibraryBrowser'

export default function CommercialLibraryPage() {
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const requestEconomicsTab = useUIStore(s => s.requestEconomicsTab)
  const openInputs = () => {
    requestEconomicsTab('tariffs')
    setSlidePanel('economics')
  }
  return (
    <div className="h-full overflow-auto p-3 flex flex-col gap-3" data-testid="commercial-library-page">
      <p className="text-[11px] text-muted">
        Tariffs, contracts, connection agreements and price series.
      </p>
      <LibraryBrowser onOpenInputs={openInputs} />
    </div>
  )
}
