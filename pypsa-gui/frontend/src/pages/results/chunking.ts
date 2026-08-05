/**
 * How much of a result series to fetch at a time.
 *
 * The canvas overlay needs ONE row — the snapshot the scrubber sits on — but
 * playback walks the rows, so a request per frame would be worse than one big
 * download. Instead we fetch a window aligned to a fixed boundary: scrubbing
 * inside it costs nothing, crossing it costs exactly one request, and
 * scrubbing back is a cache hit.
 *
 * The window is sized by BYTES, not by a fixed number of snapshots. 168
 * snapshots of 20 assets is a wastefully small request; 168 of 2000 assets is
 * too big. Deriving it from the asset count self-selects day / week / month.
 */

/** Day, week, month, year — calendar units, so boundaries stay meaningful. */
export const CHUNK_STEPS = [24, 168, 720, 8760] as const

/** Target serialised size of one chunk. */
export const TARGET_CHUNK_BYTES = 512_000

/**
 * Measured, not guessed: a frame of the exact shape `ts_payload` emits,
 * 5,256,000 values, serialised to 52.6 MB — about 10 bytes per value.
 */
export const BYTES_PER_VALUE = 10

const DEFAULT_CHUNK = 168

/**
 * Largest calendar step whose payload fits the byte target.
 *
 * Stable by construction: it depends only on `assetCount`, which is fixed for
 * a solved network. Recomputing it as data changed would shift cache keys
 * underneath the user and destroy the hit rate that makes playback smooth.
 */
export function chooseChunk(assetCount: number, totalSnapshots: number): number {
  const horizon = Math.max(1, totalSnapshots)
  if (assetCount <= 0) return Math.min(DEFAULT_CHUNK, horizon)
  const ideal = TARGET_CHUNK_BYTES / (assetCount * BYTES_PER_VALUE)
  const fit = [...CHUNK_STEPS].reverse().find(step => step <= ideal) ?? CHUNK_STEPS[0]
  return Math.min(fit, horizon)
}

/**
 * The aligned window containing `idx`, inclusive at both ends.
 *
 * `clampTo` is the investment period the scrubber is confined to, when there
 * is one. Alignment is relative to `clampTo.start` rather than to zero, so the
 * first chunk of a period is a full chunk instead of a short offcut.
 */
export function chunkBounds(
  idx: number,
  chunk: number,
  total: number,
  clampTo?: { start: number; end: number },
): { from: number; to: number } {
  const lo = clampTo?.start ?? 0
  const hi = clampTo?.end ?? total - 1
  const safeChunk = Math.max(1, chunk)
  const offset = Math.floor((Math.max(lo, idx) - lo) / safeChunk) * safeChunk
  const from = Math.max(lo, lo + offset)
  return { from, to: Math.min(from + safeChunk - 1, hi) }
}
