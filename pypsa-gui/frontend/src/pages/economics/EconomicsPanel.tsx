// Economics in Expert (plan 2026-10-14-economics-in-expert.md): one panel for
// the inputs that decide what the model charges, opened from the sidebar's
// ECONOMICS step. Inputs only: the investment case, value flows and the bill
// stay on Results → Investment. IC's editors are embedded as they are.
// E1: Basis (rates, money year and price basis) and Asset costs; E2 adds the
// site connection and the Tariffs & contracts and Library tabs.
import { useState } from 'react'
import FinanceInputsEditor from '../results/investment/FinanceInputsEditor'
import AssetCostsTable from './AssetCostsTable'
import RatesForm from './RatesForm'

const TABS = [
  { id: 'basis', label: 'Basis' },
  { id: 'assets', label: 'Asset costs' },
] as const
type TabId = typeof TABS[number]['id']

export default function EconomicsPanel() {
  const [tab, setTab] = useState<TabId>('basis')
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
            <FinanceInputsEditor />
          </div>
        )}
        {tab === 'assets' && <AssetCostsTable />}
      </div>
    </div>
  )
}
