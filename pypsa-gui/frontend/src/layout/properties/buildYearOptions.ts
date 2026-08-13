/**
 * Options for the Build Year dropdown.
 *
 * Prefer the network's configured investment periods so a multi-period run only
 * lets users pick years the LP actually models — `build_year <= period` gates
 * whether an asset is available at all, so an off-grid year silently removes it
 * from the model. With no periods configured (single-period overnight runs),
 * fall back to a 35-year span in 5-year steps.
 *
 * The asset's current value is always merged in so an asset created with a
 * non-standard year doesn't silently lose it when the form opens.
 */
export function buildYearOptions(
  periods: number[],
  currentValue: number | null | undefined,
  currentYear: number,
): number[] {
  let opts: number[]
  if (periods.length > 0) {
    opts = periods.slice().sort((a, b) => a - b)
  } else {
    const start = Math.floor(currentYear / 5) * 5
    opts = Array.from({ length: 8 }, (_, i) => start + i * 5)
  }
  if (
    currentValue != null &&
    Number.isFinite(currentValue) &&
    currentValue > 0 &&
    !opts.includes(currentValue)
  ) {
    opts = [...opts, currentValue].sort((a, b) => a - b)
  }
  return opts
}
