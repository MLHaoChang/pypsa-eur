// Cell-value coercion for the bulk-edit / inline-edit paths.
//
// Extracted from layout/BottomPanel.tsx so it can be unit-tested without
// mounting the whole panel. This is load-bearing: a string that slips into a
// numeric DataFrame column upcasts the whole column to `object` on the backend
// and crashes `n.export_to_netcdf()` at the next save with
// "unable to infer dtype on variable 'X'; object array contains mixed native
// types" — i.e. the user loses the save, not just the edit.

// When `sample` is null/undefined (every existing row has no value), we can't
// type-sniff. The backend coerces against the column's pandas dtype too, so
// here we just try numeric first — that matches the overwhelming majority of
// PyPSA fields. Boolean strings are still recognised. If neither parses, we
// return the raw string and let the backend reject it cleanly.
export function coerceForColumn(raw: string, sample: unknown): unknown {
  if (raw === '') return null
  if (typeof sample === 'number') {
    const n = Number(raw)
    return Number.isFinite(n) ? n : null
  }
  if (typeof sample === 'boolean') {
    if (raw === 'true' || raw === '1' || raw === 'yes') return true
    if (raw === 'false' || raw === '0' || raw === 'no') return false
    return raw
  }
  if (sample === undefined || sample === null) {
    if (/^(true|false)$/i.test(raw)) return raw.toLowerCase() === 'true'
    const n = Number(raw)
    if (Number.isFinite(n) && raw.trim() !== '') return n
  }
  return raw
}
