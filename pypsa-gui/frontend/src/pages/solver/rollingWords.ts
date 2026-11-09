/** The Solve strategy fields' unit words (Solver settings › General › Rolling
 *  horizon). Results › Summary's Plan vs operation card names the run's window
 *  settings with these same words; a test pins both to this map. */
export const ROLLING_WORDS = {
  horizon: 'snapshots per window',
  overlap: 'snapshots of SoC carry between windows',
} as const
