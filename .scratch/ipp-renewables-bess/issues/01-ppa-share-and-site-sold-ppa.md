# PPA share and the PPA the site sells

Status: needs-triage
Type: task
Blocked by: — (owner scope A: built first in this package)
Gated: **yes** (contract model and settlement semantics)
Model tier: opus
Size: M

Read [the spec](../spec.md) §3 and §10 (G1, G2) first.

## What to build

1. **Share (G1).** Add `share: float = 1.0`, in (0, 1], to `PpaContract` and `CfdContract`
   (`models/commercial.py`).
   - `contracts._ppa` and `contracts._cfd` multiply the contracted generation by it before any
     volume cap.
   - A baseload PPA refuses a share other than 1 (its volume is `baseload_mw`).
   - The field goes in `services/commercial/hashing.FIELDS_AFTER_V1` with the default 1.0, so every
     existing contract hash stays the same (test).
2. **The PPA the site sells (G2).** When the seller is the site party and the site earns an export
   price, a pay-as-produced PPA settles as financial: `(price − ref) × share × gen`, where `ref` is
   the export price series.
   - Implement it the way the gatekeeper decides (a settlement mode on the contract, or an automatic
     rule from the config).
   - `preflight` then stops warning `commercial.ppa_export_double_count` for that case. It keeps
     warning for every other case.
3. **`solve_ppa`** finds that contract as "the case's single owner-sold PPA" (test).
4. **The negative-price rule** (spec §3): the LP sees only the merchant price. Settlement uses the
   actual generation. The form's default text names the clause.

## Acceptance

- A cash-identity test on a toy year: (merchant on all MWh + financial PPA) equals
  (PPA price on the share + merchant on the rest), to the cent.
- Hash stability for contracts saved before the change.
- The double-count warning fires as before for a behind-the-meter seller without export pricing.
