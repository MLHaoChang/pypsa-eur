// Economics in Expert (plan 2026-10-14-economics-in-expert.md): one panel for
// the inputs that decide what the model charges, opened from the sidebar's
// ECONOMICS step. Inputs only: the investment case, value flows and the bill
// stay on Results → Investment. IC's editors are embedded as they are (E0
// moved the two wrappers this panel mounts into their own files).
import { useEffect, useState } from 'react'
import { useUIStore } from '../../store/uiStore'
import ContractsEditor from '../results/investment/ContractsEditor'
import FinanceInputsEditor from '../results/investment/FinanceInputsEditor'
import ParticipantsDesigner from '../results/investment/ParticipantsDesigner'
import TariffSection from '../results/investment/TariffSection'
import { SiteConnectionSummary } from './SiteConnectionLink'
import AssetCostsTable from './AssetCostsTable'
import RatesForm from './RatesForm'
import SolveAssumptionsSummary from './SolveAssumptionsSummary'

const TABS = [
  { id: 'basis', label: 'Basis' },
  { id: 'assets', label: 'Asset costs' },
  { id: 'tariffs', label: 'Tariffs & contracts' },
] as const
type TabId = typeof TABS[number]['id']

export function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-[10px] font-semibold uppercase tracking-wide text-muted">{title}</h3>
      {children}
    </section>
  )
}

export default function EconomicsPanel() {
  const [tab, setTab] = useState<TabId>('basis')
  const setSlidePanel = useUIStore(s => s.setSlidePanel)
  const tabRequest = useUIStore(s => s.economicsTabRequest)
  const clearTabRequest = useUIStore(s => s.clearEconomicsTabRequest)
  useEffect(() => {
    if (!tabRequest) return
    setTab(tabRequest)
    clearTabRequest()
  }, [tabRequest, clearTabRequest])
  return (
    <div className="h-full flex flex-col">
      <div role="tablist" aria-label="Economics" className="flex gap-1 px-3 pt-2 border-b border-border">
        {TABS.map(t => (
          <button key={t.id} role="tab" type="button" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
            className={`text-xs px-2 py-1 border-b-2 ${tab === t.id ? 'border-accent text-text' : 'border-transparent text-muted'}`}>
            {t.label}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-auto">
        {tab === 'basis' && (
          <div className="p-3 flex flex-col gap-3">
            <RatesForm />
            <SolveAssumptionsSummary />
            <SiteConnectionSummary />
            <FinanceInputsEditor />
          </div>
        )}
        {tab === 'assets' && <AssetCostsTable />}
        {tab === 'tariffs' && (
          <div className="p-3 flex flex-col gap-4">
            <Block title="Import tariff"><TariffSection /></Block>
            <Block title="Contracts"><ContractsEditor /></Block>
            <Block title="Participants"><ParticipantsDesigner /></Block>
            <p className="text-[11px] text-muted" data-testid="economics-open-library">
              Tariffs and contracts can be taken from the commercial library.{' '}
              <button type="button" className="underline" onClick={() => setSlidePanel('library')}>
                Open the commercial library
              </button>
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
