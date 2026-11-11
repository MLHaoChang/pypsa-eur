// Which stage of a solve the log has reached, read from the backend's
// "[PHASE]" lines (the backend sends no structured progress). A later marker
// wins and the stage never goes back; lines that match no marker keep it.
export const SOLVE_STAGES = ['Checking inputs', 'Preparing model', 'Solving', 'Saving results'] as const

const MARKERS: readonly { stage: number; re: RegExp }[] = [
  { stage: 3, re: /solve complete|storing results/ },
  { stage: 2, re: /optimising via|presolve:|rolling-horizon solve|myopic foresight/ },
  { stage: 1, re: /validation passed/ },
]

/** Zero-based index into SOLVE_STAGES for the lines of the current run. */
export function solveStageIndex(lines: readonly string[]): number {
  let reached = 0
  for (const line of lines) {
    if (!line.startsWith('[PHASE]')) continue
    const body = line.slice(7).toLowerCase()
    const hit = MARKERS.find(m => m.re.test(body))
    if (hit && hit.stage > reached) reached = hit.stage
  }
  return reached
}

export function solveStageLabel(lines: readonly string[]): string {
  const i = solveStageIndex(lines)
  return `${SOLVE_STAGES[i]}, step ${i + 1} of ${SOLVE_STAGES.length}`
}
