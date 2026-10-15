// A result tab before the network has been solved (UX template audit H2).
// It used to say "Run a simulation (header → Run LOPF)", a button Guided
// hides, so the tab was a dead end there. Now it names what is missing and
// runs the solve from here, through the header's own run path.
import ResultState from '../../components/ResultState'
import { useUIStore } from '../../store/uiStore'

export default function NotSolved({ what, why }: {
  /** The result, as the subject of a sentence: "Dispatch", "Prices". */
  what: string
  /** An optional second sentence: what the solve has to produce for it. */
  why?: string
}) {
  const requestRunSolve = useUIStore(s => s.requestRunSolve)
  const verb = /s$/.test(what) && !/ss$/.test(what) ? 'come' : 'comes'
  return (
    <div className="p-6">
      <ResultState
        state="not_run"
        reason={`${what} ${verb} from the hour-by-hour solve of the whole network, which has not run yet.${why ? ` ${why}` : ''}`}
        action={{ label: 'Run the solve', onClick: requestRunSolve }}
      />
    </div>
  )
}
