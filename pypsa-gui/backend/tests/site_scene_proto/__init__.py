"""
SC-00 N prototype of the Site 3D native scene contracts.

SC-01a promoted contract, frames, anchor, scene_store, native_scene and
flow_verdicts to `services/site_scene/` (contract, frames, anchor, store,
native, verdicts); the modules of those names here are aliases, so the SC-00 N
tests run against the production code. media_policy (SC-06) and
assistant_context (SC-07) stay prototypes. No production module imports this
package (`test_site_scene_proto_isolation.py` checks that).

Each module freezes one decision of `.scratch/site-scene-generation/
decision-register.md`; the closures, with their evidence, are in
`.scratch/site-scene-generation/sc-00-n/decisions.md`.

  contract     D02  error kinds, HTTP statuses, route table and chat dispositions
  frames       V05/V06  metric frames, units, glTF axes, reference building
  anchor       D01  the confirmed site anchor: validation, persistence, CAS
  scene_store  D03  immutable generations, atomic pointer, adoption, receipts,
                    asset_footprints takeover, legacy-PUT guard, closure
  native_scene D04/D12  model-free native generation and its measured limits
  flow_verdicts D02/UI  per-action /flow scene verdicts and their UI labels
  media_policy D05  private original-media ownership, retention and quotas
  assistant_context D11  the bounded per-turn site context block
"""
