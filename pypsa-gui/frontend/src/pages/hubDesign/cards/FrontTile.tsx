// Guided section 10: the cost, reliability and CO2 front (DC review 01, UX 28),
// the same records and the same view the Expert panel shows, with the chosen
// design marked. The study is set up and run in Expert: "Run the study in
// Expert ›" goes there through withReturnFrom, so Back returns to this card.
import { withReturnFrom } from '../../../layout/flowNavigation'
import { useUIStore } from '../../../store/uiStore'
import { FRONT_SECTION } from '../../results/FrontPanel'
import { FrontView, useFrontData } from '../../results/front/FrontView'

export function openFrontInExpert(): void {
  withReturnFrom(() => {
    const ui = useUIStore.getState()
    ui.setUiMode('expert')
    ui.setSlidePanel('results')
    ui.requestResultsTab('adequacy', FRONT_SECTION)
  }, 'Results')
}

export function FrontTile() {
  const { front, eh, pending } = useFrontData()
  const link = (
    <button type="button" data-testid="hub-front-run-in-expert" onClick={openFrontInExpert}
      className="self-start text-[11px] font-medium text-text underline hover:text-accent">
      Run the study in Expert ›
    </button>
  )
  if (pending) return null
  return (
    <section className="flex flex-col gap-1.5 text-[12px]" data-testid="hub-front">
      <span className="font-semibold text-text">Cost, reliability and CO₂ front</span>
      {front || eh ? (
        // No runStudy: the one link below is this tile's way to Expert.
        <FrontView front={front} eh={eh} testId="hub-front-view" />
      ) : (
        <p className="text-[11px] text-muted" data-testid="hub-front-not-run">
          Not run yet: the front re-optimises the design at several reliability levels and CO₂ limits, so you can pick one.
        </p>
      )}
      {link}
    </section>
  )
}
