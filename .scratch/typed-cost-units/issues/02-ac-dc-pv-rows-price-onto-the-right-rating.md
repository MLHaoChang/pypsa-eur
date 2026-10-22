# `output_electric_ac` vs `output_electric_dc` rows: price onto the right rating

Status: needs-triage
Type: bug (latent)
Found in: GS-2b (typed cost units) review by the engine gatekeeper, 2026-10-21.

`pricing.SCALING_RULES` treats `output_electric_ac` and `output_electric_dc` like `output_electric`. A
DC-rated PV row (per MW of DC) priced onto a Generator whose `p_nom` is the AC rating misprices by the
DC/AC ratio, and an AC row onto a DC-rated asset the other way. Today every shipped PV row is
`output_electric_ac`, so nothing is wrong in practice.

To decide: the asset has no field that says which rating its `p_nom` is. Options are a refusal for
`output_electric_dc` rows until the asset schema carries the rating, or a DC/AC ratio on the asset.
Needs the engine gatekeeper's call on where the rating lives.
