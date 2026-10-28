"""
The Site 3D native scene (SC-01a): the confirmed site anchor, scene-enabled
Sites with immutable generations behind one atomic pointer, model-free native
generation, and the additive `/flow` scene verdicts.

Contracts: `.scratch/site-scene-generation/contracts.md`; the decisions these
modules implement are closed in `.scratch/site-scene-generation/sc-00-n/
decisions.md` (D01-D04, D08, D11, D12).

  contract  D02  error kinds, HTTP statuses, route table and chat dispositions
  frames    V05/V06  metric frames, units, glTF axes, reference building
  anchor    D01  the confirmed site anchor: validation, persistence, CAS
  store     D03  generations, atomic pointer, adoption, receipts,
                 asset_footprints takeover, legacy-PUT guard, copy closure
  native    D04/D12  model-free native generation and its measured limits
  verdicts  D02/UI  per-action /flow scene verdicts and their UI labels
  proposals D04  the bounded in-memory proposal map (previews before Apply)
"""
