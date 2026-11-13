// What a running job says about where it is. Study runs (gridspine, campus)
// write "<kind> <stage> <done>/<total>" lines to the job log; an LP solve
// writes no stages, so it reads "Solving…" (UM04-PROGRESS-1). The latest stage
// line wins; the wording is plain, never the engine's stage id.
const STAGE_WORDS: Record<string, string> = {
  // gridspine year study
  ingest: 'Reading inputs', dispatch: 'Dispatching hours', ranking: 'Ranking hours',
  loadflow: 'Checking the grid', screening: 'Screening results', handoff: 'Preparing the handoff',
  // campus electrical study
  prepare: 'Preparing', rank: 'Ranking cases', island_checks: 'Checking island operation',
  cuts: 'Working out the frequency cuts', size: 'Sizing equipment', harmonic_scan: 'Scanning harmonics',
  voltage_stability: 'Checking voltage stability', dips_restart: 'Checking voltage dips and restarts',
  invest: 'Planning investment', gfm_settings: 'Tuning grid-forming settings', done: 'Finishing',
}

const STAGE_LINE = /(?:^|\s)(?:gridspine|campus) (\w+)(?: (\d+)\/(\d+))?\s*$/

export function solveProgressLabel(lines: readonly string[]): string {
  for (let i = lines.length - 1; i >= 0; i--) {
    const m = STAGE_LINE.exec(lines[i])
    if (!m) continue
    const words = STAGE_WORDS[m[1]] ?? 'Working'
    const total = Number(m[3])
    if (!(total >= 1)) return words
    return `${words} · step ${Math.min(Number(m[2]) + 1, total)} of ${total}`
  }
  return 'Solving…'
}
