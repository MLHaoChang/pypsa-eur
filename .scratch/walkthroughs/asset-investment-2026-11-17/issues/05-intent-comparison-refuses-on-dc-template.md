<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The intent comparison refuses on the data-centre template, with no way to supply what it needs

Status: done

Done in #1698 (62d4bf7c).
Type: bug (asset-investment walkthrough, WALK-E2E-1)
Journey steps: J1 Design intent → sizing loop → sized design (Expert)
Severity: high
Send to: asset sizing loop (`services/adequacy/sizing_loop_runner.py`, `pages/IntentComparisonSection.tsx`)
Duplicate check: none in #1618 or `.scratch/asset-sizing-loop/issues/` (all done).

## What happens

1. Create "Data Center Energy Hub" from the template with its defaults (IT 36 MW, AC LV UPS, gensets).
2. Expert › Run LOPF (optimal, €34.7 M).
3. Expert › 8 Scenarios & optimisation › Grid study › Intent comparison:
   - Tick A. Replace diesel and C. Keep diesel, no new BESS.
   - Campus link: grid_import.
   - Grid ramp limit 4, Pst limit 1, Transformer sizes 40,63,80.
   - Load swing 23.4, Oscillation amplitude 7.2, Oscillation period 2.
   - Compare intents.
4. POST `/intent-comparison` returns 422. The section reads "The comparison cannot run: link grid_import carries no AS-05 PQ tags; the PQ and Pst checks need them" (`shots/dc-intent-refused.small.png`).

`_check_inputs` (`sizing_loop_runner.py:208-214`) requires the Link columns `pq_x_pu`, `pq_pf`, `pq_s_n_mva`, `pq_p_cap_mw`, `pq_pf_req` and `statcom_capital_cost` (`services/solver/pcs_statcom.py:59`). No shipped template sets them (`grep pq_x_pu project_templates` is empty), and no front-end file reads or writes them. A user can't reach a sized design by design intent. The data-centre template is the one this feature was built for (AS-PROTO-1, AS-BUILD-1).

The refusal also shows an internal spec id ("AS-05") and engine words ("PQ tags"), with no link to where to fix it.

## Expected

The intent comparison runs on the Data Center Energy Hub template as shipped. Either of these would do:

- The template carries assumed PQ tags on `grid_import`, marked assumed.
- The section offers the missing values as plain fields: the PoC reactance (p.u.), the power factor and its requirement, the converter MVA and cap, and the STATCOM cost. It saves them to the link.

The refusal names the missing fields in plain words, then links to them (review-ux.md: refusals open with the field label).

## Breaks

AS-BUILD-1 / AS-PROTO-1: the sizing loop is the design-intent journey. RAW-NAMES-1. review-ux.md: no dead ends.

## Done when

Steps 1–3 run intents A and C to a converged or reported loop status on the shipped template. A test runs the endpoint on the template network.
