// Deciding what to do with a previewed impedance rescale.
//
// r/x/b are stored absolute and shown per-km. When a length changes, holding
// per-km constant means scaling all three by the length ratio — so the
// relative change is IDENTICAL for r, x and b, and equals the change in
// length. One number describes the whole line; there is nothing to compare
// field by field.
//
// Accepting a material rescale changes x, and DC OPF splits flows inversely
// proportional to x — so results move. That is why anything material is a
// question rather than an action.

export interface RescalePreview {
  name: string
  old_length: number
  new_length: number
  old: { r: number; x: number; b: number }
  new: { r: number; x: number; b: number }
  rel_change: number
  skipped_reason: string | null
}

/** Relative change at or below which the rescale is applied without asking. */
export const RESCALE_PROMPT_THRESHOLD = 0.05

export function partitionRescale(previews: RescalePreview[]): {
  auto: RescalePreview[]
  ask: RescalePreview[]
  blocked: RescalePreview[]
} {
  const auto: RescalePreview[] = []
  const ask: RescalePreview[] = []
  const blocked: RescalePreview[] = []
  for (const p of previews) {
    if (p.skipped_reason !== null) blocked.push(p)
    else if (p.rel_change <= RESCALE_PROMPT_THRESHOLD) auto.push(p)
    else ask.push(p)
  }
  return { auto, ask, blocked }
}
