// FMEA worksheet pure logic (Phase 3 Task 2) — extracted from the tab so the
// merge and the CSV shape are unit-testable without mounting anything.
// The merge only needs a per_mode carrier — /results/copt and the Phase 4
// aggregator /results/fmea_modes both satisfy it.
export interface ModesPayload {
  per_mode: Array<Record<string, unknown>>
  // Criticality is ΔEUE × VoLL × occurrence. With no VoLL the entire ranking
  // is €0/yr, which reads as "these modes cost nothing" rather than "these
  // modes cannot be priced" — the worksheet says which.
  voll_eur_per_mwh?: number | null
  sweep_status?: string | null
  sweep_error?: string | null
  /** Phase 12e (shipped-code review, finding 14): whether the sweep's closing
   *  base re-solve RAN, and the solver's own word on it. `true` never meant
   *  "your plan is back", only "it did not raise" — an `infeasible` re-solve
   *  does neither. */
  sweep_base_restored?: boolean | null
  sweep_base_restore_status?: string | null
}

/**
 * The worksheet's ranking is meaningless without a VoLL: every criticality
 * is zero and modes with very different ΔEUE tie. Returns the notice to show,
 * or null when the ranking is priced.
 */
export function unpricedRankingWarning(
  modes: ModesPayload | null | undefined,
): string | null {
  if (!modes || !modes.per_mode?.length) return null
  const voll = modes.voll_eur_per_mwh
  if (voll === undefined || voll === null) return null
  if (voll > 0) return null
  return (
    'No Value of Lost Load is set, so every criticality below is €0/yr — ' +
    'the ranking is unpriced, not harmless. Set a VoLL in Solver settings ' +
    '(typical 3 000–10 000 €/MWh) to rank these modes.'
  )
}

// One worksheet row after the client-side merge. Computed rows come from
// /results/copt and regenerate on every view; manual rows and overlays come
// from the per-project sidecar (GET /api/projects/{name}/worksheet).
export interface WorksheetRow {
  mode_id: string
  component_class: string
  name: string
  failure_class: string
  occurrence_per_year: number
  occurrence_basis: string
  severity_eur: number
  criticality_eur_per_year: number
  delta_eue_mwh?: number
  in_metric_scope: boolean
  mitigability: string
  engine: string
  fidelity: string
  // Manual (class-D, expert) rows are fully editable and deletable;
  // computed rows expose ONLY the mitigability cell.
  editable: boolean
}

export interface WorksheetSidecar {
  version: number
  manual_rows: Array<Record<string, unknown>>
  overlays: Record<string, { mitigability?: string; notes?: string }>
}

// Merge: computed rows get their overlay's mitigability re-attached by
// mode_id (that's how annotations survive a re-solve — the overlay outlives
// the regenerated row); manual rows append as editable. One ranking, €/yr
// criticality descending, computed and manual interleaved — the worksheet is
// ONE table, not two (spec §4.2).
export function mergeWorksheet(
  copt: ModesPayload | null,
  sidecar: WorksheetSidecar | null,
): WorksheetRow[] {
  const overlays = sidecar?.overlays ?? {}
  const rows: WorksheetRow[] = []
  for (const m of copt?.per_mode ?? []) {
    const r = m as Record<string, unknown>
    const modeId = String(r.mode_id ?? '')
    rows.push({
      mode_id: modeId,
      component_class: String(r.component_class ?? ''),
      name: String(r.name ?? ''),
      failure_class: String(r.failure_class ?? ''),
      occurrence_per_year: Number(r.occurrence_per_year ?? 0),
      occurrence_basis: String(r.occurrence_basis ?? ''),
      severity_eur: Number(r.severity_eur ?? 0),
      criticality_eur_per_year: Number(r.criticality_eur_per_year ?? 0),
      delta_eue_mwh: typeof r.delta_eue_mwh === 'number' ? r.delta_eue_mwh : undefined,
      in_metric_scope: r.in_metric_scope !== false,
      mitigability: overlays[modeId]?.mitigability ?? '',
      engine: String(r.engine ?? ''),
      fidelity: String(r.fidelity ?? ''),
      editable: false,
    })
  }
  for (const m of sidecar?.manual_rows ?? []) {
    const r = m as Record<string, unknown>
    rows.push({
      mode_id: String(r.mode_id ?? ''),
      component_class: String(r.component_class ?? ''),
      name: String(r.name ?? ''),
      failure_class: String(r.failure_class ?? 'D'),
      occurrence_per_year: Number(r.occurrence_per_year ?? 0),
      occurrence_basis: String(r.occurrence_basis ?? 'expert'),
      severity_eur: Number(r.severity_eur ?? 0),
      criticality_eur_per_year: Number(r.criticality_eur_per_year ?? 0),
      in_metric_scope: r.in_metric_scope !== false,
      mitigability: String(r.mitigability ?? ''),
      engine: String(r.engine ?? 'expert'),
      fidelity: String(r.fidelity ?? 'expert_judgement'),
      editable: true,
    })
  }
  rows.sort((a, b) => b.criticality_eur_per_year - a.criticality_eur_per_year)
  return rows
}

// IEC 60812-shaped column order: identify the mode, then occurrence,
// severity, criticality (THE ranking — no RPN, no AP, decided v2),
// mitigability, provenance last.
export const WORKSHEET_CSV_HEADER = [
  'mode_id', 'failure_class', 'component', 'name',
  'occurrence_per_year', 'occurrence_basis',
  'severity_eur', 'criticality_eur_per_year', 'delta_eue_mwh',
  'in_metric_scope', 'mitigability', 'engine', 'fidelity',
]

export function worksheetCsvRows(rows: WorksheetRow[]): unknown[][] {
  return rows.map(r => [
    r.mode_id, r.failure_class, r.component_class, r.name,
    r.occurrence_per_year, r.occurrence_basis,
    r.severity_eur, r.criticality_eur_per_year, r.delta_eue_mwh ?? '',
    r.in_metric_scope, r.mitigability, r.engine, r.fidelity,
  ])
}

// Build a contract-valid manual (class-D, expert) row from the add-form's
// three inputs. Criticality is COMPUTED as occurrence × severity — the f×S
// identity holds by construction, the user never types a product.
export function buildManualRow(input: {
  name: string; occurrencePerYear: number; severityEur: number;
  mitigability?: string;
}): Record<string, unknown> {
  const occ = Math.max(input.occurrencePerYear, 0)
  const sev = Math.max(input.severityEur, 0)
  return {
    mode_id: `manual:${input.name.trim().replace(/\s+/g, '_').toLowerCase()}`,
    component_class: 'Network',
    name: input.name.trim(),
    failure_class: 'D',
    occurrence_per_year: occ,
    occurrence_basis: 'expert',
    severity_eur: sev,
    criticality_eur_per_year: occ * sev,
    in_metric_scope: false,
    mitigability: input.mitigability ?? null,
    engine: 'expert',
    fidelity: 'expert_judgement',
  }
}
