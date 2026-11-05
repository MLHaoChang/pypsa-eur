// AC power-flow convergence as the user reads it (AA-12). Each snapshot is
// converged, not converged, or unknown (PyPSA did not report it). Unknown is
// its own state: never counted as converged, never shown as failed.

export interface AcPfConvergenceCounts {
  // null while any snapshot is unknown (the run carries no converged count).
  converged_count?: number | null
  // Display only: snapshots known to have converged.
  known_converged_count?: number
  unknown_count?: number
  total_snapshots?: number
}

export interface AcPfConvergenceText {
  /** "Snapshots converged" KPI value: "k / n", "k / n · u unknown" or "Unknown / n". */
  kpi: string
  /** Short form for tooltips: "k/n converged" or "convergence unknown for u/n". */
  short: string
  /** Muted explanation under the KPIs, or null when every snapshot is known. */
  note: string | null
}

export function acPfConvergenceText(s: AcPfConvergenceCounts | null | undefined): AcPfConvergenceText {
  const n = s?.total_snapshots ?? 0
  const u = s?.unknown_count ?? 0
  if (u <= 0) {
    const k = s?.converged_count ?? s?.known_converged_count ?? 0
    return { kpi: `${k} / ${n}`, short: `${k}/${n} converged`, note: null }
  }
  const k = s?.known_converged_count ?? 0
  return {
    kpi: u >= n ? `Unknown / ${n}` : `${k} / ${n} · ${u} unknown`,
    short: `convergence unknown for ${u}/${n}`,
    note: `PyPSA did not report convergence for ${u} of ${n} snapshots: `
      + 'their AC values are not checked. Re-run Stage 2, or use the LOPF results.',
  }
}
