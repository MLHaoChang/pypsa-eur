# Home card and artwork

Status: done (#504, 8e2337fb)
Type: task
Blocked by: 03 (the card is "In development" until then); W2 places it
Gated: UX
Model tier: sonnet
Size: S

Read [the spec](../spec.md) §8.4 first.

## What to build

- The card data for W2: id, title, one-liner (it may say "illustrative German site"; the project gets
  no pin), chips, the group pre-fill (spec §8.1) and the
  "In development" state until ticket 03 merges.
- `ipp.svg`, from a new block in `.scratch/workspace-redesign/create-industry-art.mjs` using the
  existing primitives, to spec §8.4's brief. The UX reviewer checks it against #436's style.
