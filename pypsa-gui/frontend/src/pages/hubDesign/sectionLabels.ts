// Report section ids in words (spec §5.8: the cards never show a stage id).
/** Report sections in words — the card never shows a stage id (§5.8). */
export const SECTION_LABEL: Record<string, string> = {
  target: 'Reliability target',
  cost: 'Cost',
  frontier: 'Cost versus reliability',
  sizing: 'Equipment sizing',
  redundancy: 'Spare-equipment options',
  levers: 'Design options',
  dtc: 'Running without the grid',
  fmea_top: 'Top risks',
  tea: 'Cost of energy',
  certification: 'Reliability check',
}
