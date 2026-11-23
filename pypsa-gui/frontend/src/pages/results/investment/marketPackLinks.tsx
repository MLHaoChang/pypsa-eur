// The market-pack card's links (UX 3 on #1938): the Library, and where the reserves
// show after a solve. "Storage cycling" is an Expert view, so Guided links to it
// through Expert; both come back with Back.
import { RESERVE_SECTION } from '../../../api/reserves'
import { openSectionInExpert, showResultsTab, withReturnFrom } from '../../../layout/flowNavigation'
import { useUIStore } from '../../../store/uiStore'

export function LibraryLink({ children = 'Library' }: { children?: string }) {
  return (
    <button type="button" className="underline" data-testid="mp-open-library"
            onClick={() => withReturnFrom(() => useUIStore.getState().setSlidePanel('library'))}>
      {children}</button>
  )
}

/** "Re-solve, then see the reserves in Results → Storage cycling", as a link. */
export function ResultsWhere() {
  const expert = useUIStore(s => s.uiMode) === 'expert'
  if (expert) {
    return (
      <>Re-solve, then see the reserves in{' '}
        <button type="button" className="underline" data-testid="mp-open-results"
                onClick={() => showResultsTab('storage', { section: RESERVE_SECTION, from: 'Market pack' })}>
          Results → Storage cycling</button>.</>
    )
  }
  return (
    <>Re-solve, then see the reserves in Results → Storage cycling, an Expert view:{' '}
      <button type="button" className="underline" data-testid="mp-open-results"
              onClick={() => withReturnFrom(() => {
                openSectionInExpert('results')
                const ui = useUIStore.getState()
                ui.setSlidePanel('results')
                ui.requestResultsTab('storage', RESERVE_SECTION)
              }, 'Market pack')}>
        open Results in Expert</button>.</>
  )
}
