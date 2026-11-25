// Guided section 10: the cost, reliability and CO2 front (DC review 01, UX 28),
// the same records and the same view the Expert panel shows, with the chosen
// design marked, read-only. The study is set up and run, and a design chosen,
// in Expert: "Run the study in Expert ›" goes there through withReturnFrom,
// so "Back to Cost, reliability and CO₂ front" returns to this card.
import { withReturnFrom } from '../../../layout/flowNavigation'
import { useUIStore } from '../../../store/uiStore'
import { FRONT_SECTION } from '../../results/FrontPanel'
import { FrontView, useFrontData } from '../../results/front/FrontView'

/** The tile's title, which the Back pill names ("Back to Cost, reliability and CO₂ front"). */
export const FRONT_TILE_TITLE = 'Cost, reliability and CO₂ front'

export function openFrontInExpert(): void {
  withReturnFrom(() => {
    const ui = useUIStore.getState()
    ui.setUiMode('expert')
    ui.setSlidePanel('results')
    ui.requestResultsTab('adequacy', FRONT_SECTION)
  }, FRONT_TILE_TITLE)
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
      <span className="font-semibold text-text">{FRONT_TILE_TITLE}</span>
      {front || eh ? (
        // Read-only (plan GO): a design is chosen in Expert, through the one link below,
        // which is also this tile's way to run the study again.
        <FrontView front={front} eh={eh} testId="hub-front-view" readOnly />
      ) : (
        <p className="text-[11px] text-muted" data-testid="hub-front-not-run">
          Not run yet: the front re-optimises the design at several reliability levels and CO₂ limits, so you can pick one.
        </p>
      )}
      {link}
    </section>
  )
}
