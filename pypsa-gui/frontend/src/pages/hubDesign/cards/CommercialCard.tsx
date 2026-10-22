import { ArrowRight } from 'lucide-react'
import ContractsEditor from '../../results/investment/ContractsEditor'
import FinanceInputsEditor from '../../results/investment/FinanceInputsEditor'
import ParticipantsDesigner from '../../results/investment/ParticipantsDesigner'
import TariffSection from '../../results/investment/TariffSection'
import RatesForm from '../../economics/RatesForm'
import { Block } from '../../economics/EconomicsPanel'
import { openSectionInExpert } from '../../../layout/flowNavigation'

// Guided section 7, Commercial & parties: the same editors Expert mounts in
// its Economics panel (one editor per value, design §2.2), three decisions
// up front and the rest under "More settings". The commercial shell replaces
// this card when it lands.
export function CommercialCard() {
  return (
    <div data-testid="hub-card-commercial" className="flex flex-col gap-4 rounded border border-border bg-panel p-4">
      <Block title="Import tariff"><TariffSection /></Block>
      <Block title="Contracts"><ContractsEditor /></Block>
      <Block title="Finance inputs"><FinanceInputsEditor /></Block>
      <details data-testid="commercial-more">
        <summary className="cursor-pointer text-[12px] text-muted hover:text-accent">More settings</summary>
        <div className="mt-3 flex flex-col gap-4">
          <Block title="Participants"><ParticipantsDesigner /></Block>
          <RatesForm />
        </div>
      </details>
      <div>
        <button type="button" data-testid="section-open-expert-commercial" onClick={() => openSectionInExpert('commercial')}
          className="inline-flex items-center gap-1 rounded border border-border px-2.5 py-1 text-[11px] text-muted hover:border-accent hover:text-accent">
          Open commercial &amp; parties in Expert <ArrowRight size={11} />
        </button>
      </div>
    </div>
  )
}
