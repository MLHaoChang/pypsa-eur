# Carrier KPIs: hydrogen is not renewable

Status: ready-for-agent (outsourced: H)
Type: task
Blocked by: 11 (#676)
Gated: yes (GK triage 2026-10-23: G1 (a), G2 include, G3 follow-up, G4 accept)
Model tier: opus

This issue is the sibling of issue 11. It was noted in task F's brief as out of scope.

## Gap

`services/results/carrier_kpis.py::_is_renewable_carrier` tests `'hydro' in carrier`, so a hydrogen Generator counts as renewable. Its unused capacity then shows as curtailment in Load Flow's Carrier KPIs table, often above 50 % and in the warning colour. The CSV export and the chat assistant's numbers show the same figure.

The table is keyed by carrier `nice_name`, so this also hits any `H2` Generator, whose catalogue nice name is "Hydrogen".

`services/results/curtailment.py::curtailment_frame` (`RENEW_KEYWORDS`) has the same substring test. It feeds the Curtailment tab, the Dispatch and Overview curtailment series, Curtailment by cause and `load_shape`.

## What to build

In both files, blank hydrogen words before the renewable substring test, the same way as F's `_gen_category`. Use one copy of the mask, and keep each file's keyword set. The `curtailment_cost > 0` rule still applies.

Pin the mask to `_gen_category` with a test. Show that Load Flow and the Curtailment tab agree on a hydrogen Generator.

Brief: `.scratch/timeseries-generators/outsource/H-carrier-kpis-hydrogen.md`.

## Done when

Tests show that:

- a `hydrogen` Generator and an `H2` Generator with nice name "Hydrogen" report 0 curtailment in Carrier KPIs;
- a `hydrogen` Generator is absent from the Curtailment tab's frame, unless it has `curtailment_cost > 0`;
- `hydro` and `solar` keep their curtailment on both surfaces;
- the existing results and curtailment tests still pass.

## Follow-ups (not H)

- G3: PyPSA-Eur nice names `Run of River` and `Reservoir & Dam` get no curtailment figure in Carrier KPIs, because the statistics key is the nice name.
- Harmonise `curtailment.RENEW_KEYWORDS` (`offwind`, `onwind`, `rooftop`) with `carrier_kpis._RENEWABLE_KW` (`biogas`, `run-of-river`).
- A shared renewable helper in `services/profile_shapes.py`.
