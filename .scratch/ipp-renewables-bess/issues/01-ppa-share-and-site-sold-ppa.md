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
2. **The PPA the site sells (G2)**, as the gatekeeper set it (spec §10 G2):
   - `PpaContract.settlement: Literal["physical", "financial"] = "physical"`, in `FIELDS_AFTER_V1`;
   - `financial` settles `(price − ref) × share × gen` by calling the CfD settlement code, not a
     second implementation. It needs `reference_price` (validator);
   - `suspend_on_negative_price` on the financial PPA, default True for this form. With False, a
     warning says dispatch is not contract-optimal;
   - `preflight`: `commercial.ppa_export_double_count` stops only for `settlement == "financial"`.
     An info line is added when the PPA's ref isn't the export series (basis risk).
3. **`solve_ppa`** finds that contract as "the case's single owner-sold PPA" (test).
4. **The negative-price rule** (spec §3): the LP sees only the merchant price. Settlement uses the
   actual generation. The form's default text names the clause.

## Acceptance

- A cash-identity test on a toy year with a battery: Σ ref · export + Σ (P − ref) · s · gen equals
  "PPA price on the share + merchant on the rest", to the cent. It includes hours where the
  merchant rest (export − s · gen) is negative because the battery charges from the plant.
- Hash stability on an existing PPA and CfD fixture (`share`, `settlement` at their defaults).
- The double-count warning fires as before for a behind-the-meter seller without export pricing.
