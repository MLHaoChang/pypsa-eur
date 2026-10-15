# E2E for non-data-center sites: generic site, H2 hub, island microgrid

Status: needs-triage (design, not yet approved)

## What exists (master 5711a6fa)

- The five-stage path is mostly generic already. It is driven by the `eh_*` network tags,
  `links_t["eh_energization_mw"]`, the sidecars (`eh_template.json`, stress,
  `solver_config.json` with `commercial` and `finance`, `island_config.json`) and their bind at
  create (`_bind_template_solver_config`).
- **Data-center-specific code lives only in the template module:**
  - `_staged_datacenter` hardcodes `it_load`/`cooling` and 36 MW;
  - the `DC_*` constants;
  - `_staged_island`;
  - the `projected_load` vocabulary (`it_mw`, `pue`).
- **Data center defaults in gridspine:** PF 0.98 (`static/capacity.py:93`) and load damping
  D = 0 (`static/frequency.py:39`).
- **Not wired in Guided for any template, the data center included:**
  - control: `gfm_settings_campus` has no caller in pypsa-gui;
  - report: the docx report has no finance or island sections; the island report exists
    as a route.
- **Today's limits on the other two templates:**
  - The H2 hub and the microgrid are single-year templates with no commercial or finance
    blocks.
  - The investment case refuses the H2 hub: an owner conversion Link sits beside a
    non-electric load (`owner_conversion_load`), or there are non-owner site assets.
  - `CommercialConfig.poc_link` is required, so the microgrid, whose tie is normally open, has
    no counterfactual.

## Shared stage logic (all four templates)

| Stage | What it reads | Shared code |
|---|---|---|
| Investment | multi-period EH solve; IC with `commercial` and `finance` bound at create; existing assets at COD period 1, cost 0; one candidate per period with `build_year` | solver, `finance_case`, P5c staging, D4 bind |
| Reliability | EH study: archetype, `eh_critical` → DtC per_load, outage columns, stress registry, energization | `eh_study`, `dtc`, `stress`, `energization` |
| Electrical | campus study draft: exactly one `eh_poc`, `eh_sk_mva`/`eh_ibr_mva`, island sidecar | `campus_electrical_service`, gridspine producers |
| Control | island sidecar: unit control modes, `ups.it_load` (a list of load names, so it already works for any protected load), pickup blocks, limits; GFM settings | island LP rows, frequency judge |
| Report | Results tiles (investment, grid plan, reliability, operation), island report, docx | `chapters.ts`, reports |

## 1. Generic site template `eh_site` (one parametrized template)

### Parameters

`SiteParams` is a pydantic model. The route is
`POST /from_template/eh_site` with an optional JSON body. With no body it uses the defaults of
`site_type`.

- `site_type`: `industrial | commercial | hospital | campus | other`. This is a preset that
  fills every field below. The user can override any of them.
- `stages`: 1–3 entries of `{year, peak_mw}`. A period runs to the next stage; the last one
  runs to `horizon_end`, default 2045.
- `load_profile`: a 168 h weekly shape per type, scaled so that its maximum equals the
  stage's peak:
  - industrial: 3 shifts, weekend at 60 %;
  - commercial: weekdays 08–18, nights at 35 %;
  - hospital: 24/7, base 70 % with a daytime peak;
  - campus: daytime with an evening shoulder;
  - other: flat at 80 %.
- `critical_share` sits on its own `critical` bus (`eh_critical`):
  - industrial 0.4 (process);
  - commercial 0.15;
  - hospital 0.5 (the life-safety and critical branch);
  - campus 0.2;
  - other 0.3.
- `connection_mw`: the PoC rating.
- `energization`: `[{year, mw}]`, default `connection_mw` in every stage. It is written to
  `eh_energization_mw` in absolute MW, as in #266.
- `fault_level_mva` at the PoC: default 20 × `connection_mw`, so the SCR gate passes as for
  the data center.
- `existing`:
  - `gensets {count, unit_mw}`;
  - `pv_mw`;
  - `battery {mw, mwh}`;
  - `ups {mw, minutes}` on the critical bus. It is on for hospital and industrial.
- `candidates`: `genset`, `bess`, `pv`, each `{enabled, max_mw}`. There is one row per stage
  with `build_year`.
- `tariff` and `finance`: the data center's synthetic numbers, renamed generic, and listed in
  the ledger.
- `island`: generated from the units. The pickup block is about 5 % of the last peak.
  - hospital: genset start ≤ 10 s (IEC 60364-7-710 / NFPA 110 Type 10), UPS on the critical
    branch, `sustained_h` 24;
  - the other types: the same structure with their own values.

**Preset defaults (peak by stage 2027/2030/2035, connection):**

| Type | Peak (MW) | Connection (MW) | On site |
|---|---|---|---|
| industrial | 8 / 12 / 15 | 20 | 2 × 2 MW gensets, 1 MW PV |
| commercial | 2 / 3 / 4 | 5 | 1 × 1 MW genset, 0.5 MW PV |
| hospital | 4 / 5 / 6 | 8 | 3 × 2 MW gensets (N+1), UPS, 0.3 MW PV |
| campus | 10 / 14 / 18 | 25 | 2 × 3 MW gensets, 2 MW PV, 2 MW / 4 MWh battery |

**Physically plausible by construction:** each stage's peak is at or below that stage's
energized connection, so the grid-only comparison runs under its own connection. A test pins
this for every preset (Q1).

### How the stages read it

- **Investment:** as the data center does. The archetype is `strong_grid` when the energized
  capacity covers the peak in every stage, else `weak_flexible`. The IC runs with no
  `counterfactual_exceeds_connection` flag.
- **Reliability:**
  - stress scenarios by type: heatwave for commercial, hospital and campus; grid-outage week
    for all;
  - `dtc_attribution` per_load.
- **Electrical:** unchanged. The gridspine PF and D defaults are the data center's; the ledger
  says so (Q5).
- **Control and report:** whatever E2E wires for the data center, read from the same sidecar
  and meta. I'm asking E2E which seams.

### What's reused, and what's generalized

- `eh_site.py` is a new file beside `eh_templates.py`. It imports `_base`, `_outage`,
  `_tag_columns`, `_price`, `_day_shape` and `_genset_island`.
- It holds its own generic `stage_network(n, periods, load_scale, energization, existing,
  candidates)`. It does not edit `_staged_datacenter` while E2E's PRs are open. E2E can switch
  the data center onto it later, if they want.
- `projected_load` becomes generic in `eh_template.json`: `{year, peak_mw, note}`, plus
  `site_type` and `params` (the frontend shows the stages and the energization).

**Engine changes:** none for the generic site. One route change: `from_template` accepts a body
and generates the sidecars at create, because there is no prebuilt directory.

## 2. Industrial Hydrogen Hub `eh_h2_hub`

**What's specific:**
- the electrolyser, H2 storage, the fuel cell and a 25 MW H2 offtake;
- a 200 MW connection against a 40 MW process load: grid-only runs easily.

**The stages:**
- Investment needs:
  - a staged variant (wind, PV and BESS candidates by period; the electrolyser ramp is a
    question);
  - commercial and finance blocks;
  - an IC engine change, because H2 offtake makes the counterfactual impossible today (Q3).
- Reliability works today: `strong_grid`, the fuel cell as firm backup, conversion roles in
  the FMEA.
- Electrical works today.
- Control: a new island sidecar (PV and wind GFL, the battery GFM, the fuel cell as a
  converter unit; no genset).

## 3. Island Microgrid `eh_microgrid`

**What's specific:** no grid in operation. The 20 MW tie is normally open (`off_grid`).

**The stages:**
- Investment: no grid-only comparison exists. The baseline is the existing diesel fleet
  (Q4). That is an IC engine change: a counterfactual from a baseline solve instead of the
  PoC bill.
- Reliability works today (`off_grid`, the hospital feeder critical).
- Electrical: gridspine needs exactly one `eh_poc`. The open tie's mainland bus is tagged, so
  it works, but the SCR gate means nothing on an island. The island fault level comes from
  the diesels; check in the slice.
- Control: this is the island case: diesel droop, battery GFM, `sustained_h`. There is no
  UPS bridge, so it uses the `seamless` scenario.

## PR slices (recommended order: generic site first)

1. **G1, template data and route (no engine):**
   - `eh_site.py`: `SiteParams`, presets, builder, sidecars;
   - `from_template/eh_site` with a body;
   - tests: every preset builds, solves, the IC computes, and grid-only fits under the
     connection;
   - a release note.
2. **G2, frontend:**
   - the Guided Start card gets a "Generic site" form (type chips → preset; peaks per stage,
     profile, connection and energization, on-site options);
   - `EhTemplateMeta` gains `projected_load`, `energization` and `ledger`, shown on the Site
     card.
3. **G3, assistant:** the `create_project_from_template` params, then the regenerated
   inventory.
4. **M1 / H1, template data:** staged variants as new keys (`eh_microgrid_staged`,
   `eh_h2_hub_staged`, keeping the single-year examples as the data center does), with
   commercial and finance blocks and island sidecars.
5. **H2e / M2e, IC engine (gatekeeper):** the H2 reference-price counterfactual and the
   diesel-baseline counterfactual.
6. **C1, control and report:** follow E2E's wiring for the data center; each template adds
   only sidecar or meta data.

## G2 notes from the UX review (2026-10-15)

Required by the UX standards:
- Every field carries its own "default" tag. The defaults line above the form is only a
  preview. Its figures go through `kpiFigure` / `format.ts`.
- Stage years ascend, and the check shows next to the field ("2030 must come after 2027").
- Site card, "Must stay on": show the critical load as entered ("3.6 MW, 30 % of peak").
- An action that makes a new project says so ("Start a new project from these values").
  Better: edit in place and mark the study `stale`.

Suggestions to take:
- Kind of site: a radio group of 5 small cards (2–3 per row), each with a one-line example.
- "Grid strength — fault level (MVA)".
- "What the study may add".
- Site card: a table (Year | Peak load | Available from grid) plus one Kpi for the
  connection limit.
- Beside the disabled Create button: "N fields need attention", which scrolls to the first.
- New Project wizard: a "Generic site" card inside the From template tab that opens Guided
  Start. No new tab.
- The field name stays provisional until the owner decides on "Site type" against "Grid
  connection".
