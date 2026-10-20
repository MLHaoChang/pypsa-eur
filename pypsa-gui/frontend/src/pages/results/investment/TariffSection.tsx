// The Tariff section (IC P3 WP3.7b), moved out of InvestmentTab.tsx unchanged so
// Expert → Economics can mount it too (Economics in Expert, E0).
import { useQuery } from '@tanstack/react-query'
import { commercialApi } from '../../../api/commercial'
import { useUIStore } from '../../../store/uiStore'
import { nk } from '../../../utils/queryKeys'
import MarketPricesBlock from './MarketPricesBlock'
import TariffBuilder from './TariffBuilder'
import { blankTariff } from './tariffModel'

/** The project's import tariff in the builder; a blank one when there is none. */
export default function TariffSection() {
  const project = useUIStore(s => s.currentProject)
  const current = useQuery({ queryKey: nk(project, 'commercial', 'config'),
                             queryFn: () => commercialApi.getCommercial() })
  if (current.isPending) return <p className="text-[11px] text-muted py-2">Loading the tariff…</p>
  // A failed read must not open a blank builder whose save would replace the
  // real tariff (WP3.7b review #11).
  if (current.isError) {
    return <p className="text-[11px] text-warn py-2" data-testid="ic-tariff-error">
      The project's tariff could not be read; reload before editing it.</p>
  }
  const tariff = current.data?.import_tariff
  const ref = current.data?.import_tariff_ref
  return (
    <div className="space-y-2">
      {current.data && <MarketPricesBlock cfg={current.data} />}
      <p className="text-[11px] text-muted">
        {tariff ? `Editing the project's import tariff ${tariff.id}.`
          : 'The project has no import tariff: build one.'}</p>
      {ref && (
        <p className="text-[11px] text-warn" data-testid="ic-tariff-library-ref">
          This tariff is Library item {ref.id} v{ref.version}. Saving it as the project tariff
          detaches it from the Library (the project keeps your edited copy).</p>
      )}
      <TariffBuilder key={tariff?.id ?? 'new'} initial={tariff ?? blankTariff()} />
    </div>
  )
}
