// The decision study's React Query definitions, shared by the panel and the
// header's run watcher so both read ONE cache entry per route.
//
// Polling (gate S6 [N5]): GET findings re-reads every option network from
// disk, so it is NEVER polled — `staleTime: Infinity`, no interval, no refetch
// on focus. The run and tornado STATUS routes are polled while they run
// (`decisionModel.pollInterval`); findings are fetched once when a run or a
// tornado ends, and again only after an edit that changes them.
//
// Reads whose refusal is part of the page (404 never run, 409 changed since
// the run) resolve to `{ data, error }` instead of throwing, so the page can
// say which refusal it is.
import type { QueryClient } from '@tanstack/react-query'
import {
  decisionStudiesApi as api, studyError, type DecisionReport, type Findings, type InvestmentCase,
  type RunRecord, type StudyError, type TornadoRecord,
} from '../../api/decisionStudies'

export interface Outcome<T> { data: T | null; error: StudyError | null }

async function outcome<T>(p: Promise<T>): Promise<Outcome<T>> {
  try {
    return { data: await p, error: null }
  } catch (e) {
    return { data: null, error: studyError(e) }
  }
}

/** A status route's "never started" 404 is a null record, not an error. */
async function recordOrNull<T>(p: Promise<T>, neverCode: string): Promise<T | null> {
  try {
    return await p
  } catch (e) {
    if (studyError(e).code === neverCode) return null
    throw e
  }
}

export const studyKey = (project: string, studyId: string) => ['decision', project, studyId] as const

export const dq = {
  study: (p: string, id: string) => ({
    queryKey: [...studyKey(p, id), 'study'],
    queryFn: () => api.get(p, id),
    retry: false,
  }),
  ledger: (p: string, id: string) => ({
    queryKey: [...studyKey(p, id), 'ledger'],
    queryFn: () => api.ledger(p, id),
    retry: false,
  }),
  library: (p: string) => ({
    queryKey: ['decision', p, 'library'],
    queryFn: () => api.library(p),
    staleTime: Infinity,
    retry: false,
  }),
  run: (p: string, id: string) => ({
    queryKey: [...studyKey(p, id), 'run'],
    queryFn: () => recordOrNull<RunRecord>(api.run(p, id), 'study_never_run'),
    retry: false,
  }),
  tornado: (p: string, id: string) => ({
    queryKey: [...studyKey(p, id), 'tornado'],
    queryFn: () => recordOrNull<TornadoRecord>(api.tornado(p, id), 'tornado_never_run'),
    retry: false,
  }),
  findings: (p: string, id: string) => ({
    queryKey: [...studyKey(p, id), 'findings'],
    queryFn: () => outcome<Findings>(api.findings(p, id)),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  }),
  report: (p: string, id: string) => ({
    queryKey: [...studyKey(p, id), 'report'],
    queryFn: () => outcome<DecisionReport>(api.report(p, id)),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  }),
  optionCase: (p: string, id: string, optionId: string) => ({
    queryKey: [...studyKey(p, id), 'case', optionId],
    queryFn: () => outcome<InvestmentCase>(api.optionCase(p, id, optionId)),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  }),
}

/** After an edit or a finished run/tornado: everything derived from the run is re-read once. */
export function invalidateDerived(qc: QueryClient, p: string, id: string): void {
  for (const part of ['findings', 'report', 'case']) {
    void qc.invalidateQueries({ queryKey: [...studyKey(p, id), part] })
  }
}
