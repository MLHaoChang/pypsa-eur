// The backend withholds the result figures after an input edit that follows a run: every results reader
// answers 204 with `X-Results-State: out_of_date`. A plain 204 (never run, or cleared) has no such header.
// The api client turns the header into this flag; the empty state a surface shows reads it.
import { create } from 'zustand'

export const RESULTS_STATE_HEADER = 'x-results-state'
export const OUT_OF_DATE = 'out_of_date'

interface ResultsOutOfDateState {
  outOfDate: boolean
  set: (v: boolean) => void
}

export const useResultsOutOfDate = create<ResultsOutOfDateState>(set => ({
  outOfDate: false,
  set: outOfDate => set(s => (s.outOfDate === outOfDate ? s : { outOfDate })),
}))

/** Record what a results reader's answer says: a 204 with the header is out of date, any other 204 is not. */
export function noteResultsResponse(url: string | undefined, status: number, headers: unknown): void {
  if (status !== 204 || !url || !/(^|\/)results\//.test(url)) return
  const h = headers as { get?: (k: string) => unknown } | undefined
  const v = typeof h?.get === 'function' ? h.get(RESULTS_STATE_HEADER) : (headers as Record<string, unknown> | undefined)?.[RESULTS_STATE_HEADER]
  useResultsOutOfDate.getState().set(v === OUT_OF_DATE)
}
