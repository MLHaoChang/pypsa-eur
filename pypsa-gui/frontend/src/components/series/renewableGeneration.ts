// Generate and apply wind/solar profiles in one click (RP-04b, RP-Q2 = A).
// One generation job per technology (the engine takes one), each waited for
// in the pending list, then written with one batch apply: one undo step per
// technology. The weather source and year are the weather bar's, saved in the
// project; the request leaves them out so the backend reads the same value the
// assistant does.
import { useUIStore } from '../../store/uiStore'
import { errorText, profilesApi, type ProfileTechnology, type ReadinessRow } from '../../api/profiles'

export interface GenerationOutcome {
  technology: 'wind' | 'solar'
  generators: string[]
  applied: number
  error?: string
}

/** The rows a bulk or per-row Generate may write: a location, and no measured
 *  file or network-supplied series to protect. `replace` adds the profiles
 *  generated earlier. */
export function readyRows(rows: ReadinessRow[], replace = false): ReadinessRow[] {
  return rows.filter(r => r.coord_source !== 'missing'
    && (r.profile === 'none' || (replace && r.profile === 'generated')))
}

/** Why no row is ready, in words, for the disabled bulk action. */
export function notReadyReason(rows: ReadinessRow[]): string {
  if (rows.length === 0) return 'There is no wind or solar generator in this model.'
  const missing = rows.filter(r => r.coord_source === 'missing').length
  const kept = rows.filter(r => r.profile === 'uploaded' || r.profile === 'network_native').length
  const generated = rows.filter(r => r.profile === 'generated').length
  const parts: string[] = []
  if (missing) parts.push(`${missing} without a location (set the bus coordinates)`)
  if (kept) parts.push(`${kept} with a measured file or the network’s own series, which a default never replaces`)
  if (generated) parts.push(`${generated} already generated (tick “replace series generated earlier” to redo them)`)
  return `No wind or solar generator is ready: ${parts.join(', ')}.`
}

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

/** Wait until the job's artifact is pending, or fail when it left the queue without one. */
export async function waitForJob(project: string, jobId: string, pollMs = 1500, maxPolls = 400): Promise<void> {
  const expert = useUIStore.getState().uiMode === 'expert'
  for (let i = 0; i < maxPolls; i++) {
    const p = await profilesApi.pending(project)
    if (p.pending.some(x => x.job_id === jobId)) return
    if (!p.jobs.some(j => j.id === jobId)) {
      throw new Error('The generation did not finish: see the solve queue for why.')
    }
    await sleep(pollMs)
  }
  throw new Error(`The generation is still running: it will appear ${expert ? 'in Profile Studio’s pending list' : 'as a pending profile you can apply'} when it finishes.`)
}

/** Queue a profile without changing the model, so Tune can compare it first.
 * Source and year stay the project's weather choice, as in one-click Generate. */
export async function generatePending(
  project: string, technology: ProfileTechnology, generators: string[],
  opts: { preset?: string; params?: Record<string, unknown> } = {}, pollMs = 1500,
): Promise<string> {
  const job = await profilesApi.generate(project, { points: [], from_generators: generators, technology, ...opts })
  await waitForJob(project, job.id, pollMs)
  return job.id
}

export async function generateAndApply(
  project: string, rows: ReadinessRow[], pollMs = 1500,
): Promise<GenerationOutcome[]> {
  const out: GenerationOutcome[] = []
  for (const technology of ['wind', 'solar'] as const) {
    const generators = rows.filter(r => r.technology === technology).map(r => r.generator)
    if (generators.length === 0) continue
    try {
      const jobId = await generatePending(project, technology, generators, {}, pollMs)
      const res = await profilesApi.applyBatch(project, jobId)
      out.push({ technology, generators, applied: res.count })
    } catch (e) {
      out.push({ technology, generators, applied: 0, error: errorText(e) })
    }
  }
  return out
}
