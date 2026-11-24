// One set of certification verdict words for Guided and Expert (asset-
// investment walkthrough 2026-11-17; EXPERT-GUIDED-1).
/** The certification's verdict in words, one set for Guided's headline and
 *  Expert's Reliability badge (EXPERT-GUIDED-1). */
export function verdictWord(verdict: string): string {
  if (verdict === 'pass') return 'Certified'
  if (verdict === 'inconclusive') return 'Not decided'
  return 'Not certified'
}
