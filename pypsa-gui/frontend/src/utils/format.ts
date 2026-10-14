// One number-formatting module for every surface (UX overhaul S1,
// docs/superpowers/plans/2026-10-14-ux-overhaul.md §3). Moved here unchanged
// from pages/results/shared.tsx; rounding changes wait for the owner's
// sign-off on the units table, so every output below is byte-identical to the
// pre-S1 formatters.

// fmtEnergy expects MWh (PyPSA's natural unit when summing MW × hourly
// snapshots). It scales up to GWh / TWh once the magnitude crosses 1000 /
// 1e6 MWh respectively; below 1 MWh it stays in MWh (three decimals, P31 C3). The previous code
// formatted MWh as if they were Wh, producing absurd labels (e.g. 1234 MWh
// as "1.23 kWh").
export function fmtEnergy(mwh: number | null | undefined, digits = 2): string {
  if (mwh == null || !Number.isFinite(mwh)) return '—'
  const a = Math.abs(mwh)
  if (a >= 1e6) return `${(mwh / 1e6).toFixed(digits)} TWh`
  if (a >= 1e3) return `${(mwh / 1e3).toFixed(digits)} GWh`
  if (a >= 1)   return `${mwh.toFixed(digits)} MWh`
  // P31 C3: below 1 MWh stay in MWh with three decimals (not "250.00 kWh"
  // beside MWh / GWh cells), and zero reads "0 MWh" (not "0.00 kWh").
  if (mwh === 0) return '0 MWh'
  return `${mwh.toFixed(3)} MWh`
}

/** A MWh value as a bare number, for a column whose header carries the unit
 *  ("ΔEUE (MWh)"): never scaled to kWh / GWh, so the header stays true for
 *  every cell. Two decimals with grouping from 1 MWh, three below, "0" for
 *  zero, "—" for a missing value (P31 C3). */
export function fmtMwhNumber(mwh: number | null | undefined): string {
  if (mwh == null || !Number.isFinite(mwh)) return '—'
  if (mwh === 0) return '0'
  if (Math.abs(mwh) < 1) return mwh.toFixed(3)
  return mwh.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function fmtCurrency(eur: number | null | undefined, digits = 2): string {
  if (eur == null || !Number.isFinite(eur)) return '—'
  const a = Math.abs(eur)
  if (a >= 1e9) return `€${(eur / 1e9).toFixed(digits)} B`
  if (a >= 1e6) return `€${(eur / 1e6).toFixed(digits)} M`
  if (a >= 1e3) return `€${(eur / 1e3).toFixed(digits)} k`
  return `€${eur.toFixed(digits)}`
}

export function fmtPower(mw: number | null | undefined, digits = 2): string {
  if (mw == null || !Number.isFinite(mw)) return '—'
  const a = Math.abs(mw)
  if (a >= 1e3) return `${(mw / 1e3).toFixed(digits)} GW`
  return `${mw.toFixed(digits)} MW`
}

// A formatted figure split into its number and its unit, so a KPI tile can
// keep the unit on the value's line at a smaller size ("136.64" + "€/MWh",
// "€2.21" + "M"). Only a leading number followed by a one-word unit is split;
// anything else — a dash, a name, "3 of 5" — comes back whole with no unit.
const VALUE_UNIT = /^([-+−]?€?[-+−]?\d[\d.,]*)\s+(\S+)$/

export function splitValueUnit(s: string): { value: string; unit: string | undefined } {
  const m = VALUE_UNIT.exec(s.trim())
  if (!m) return { value: s, unit: undefined }
  return { value: m[1], unit: m[2] }
}
