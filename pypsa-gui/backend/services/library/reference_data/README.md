# Optional European technology references

`eu-dea-2026-08.json` is an attributed, modified subset of the Danish Energy Agency / Energinet August 2026 electricity/heat and renewable-fuel workbooks. Both inspected workbook `Intro` sheets state **Attribution 4.0 International (CC BY 4.0)**. See the JSON for original URLs, SHA-256, cells, labels, number formats, licence, attribution and modifications. No endorsement is implied.

The existing native `PackValue` and `CostPart` models are returned by `services.library.europe_reference.load_europe_technology_reference(technology, projection_year)`, inside a source/applicability wrapper. `available_europe_technology_references()` lists selectors. Twenty-four explicit technology/year snapshots cover utility onshore wind, fixed-tilt utility PV, commercial/industrial rooftop PV, and 100 MW AEC/PEM electrolysis. No projection interpolation occurs.

These are optional benchmarks. They are not registered as a new automatic defaults-pack version and do not create another database. The reader makes no network calls and writes no project or Library records. UI/chat discovery and approved apply wiring are subsequent work; the references are not yet selectable in the application's UI.

Important boundaries:

- Wind is **2020 EUR**; updated PV sheets are **2025 EUR**; the fuel workbook is **2020 EUR**. Projection year is a separate coordinate. No inflation, FX or national calibration is performed.
- Wind/PV are per electrical output capacity; these PV rows use **AC** capacity. Electrolyser costs are per **total electrical input** capacity, efficiencies are **LHV** based.
- Wind/PV FOM share is derived from the exact absolute FOM and investment cells, with the same money/capacity/projection basis. Absolute costs can decline even while their percentage of CapEx rises.
- Total source investment may already include grid connection, land or other site components. Read `included_costs` before combining costs. Wind VOM, electrolyser stack replacement, water/electricity, auxiliary flows and degradation remain separate terms.
- `illustrative=False` denotes a cited source value, not a measured customer fact, country calibration, OEM guarantee or engineering approval. Applicability remains a benchmark.
- SOEC lifetime ambiguity and H2 tank/cavern O&M unit ambiguities are research findings, not guessed numerical imports.

Extraction: `.scratch/gridspine-library-eu/build_dea_reference.py` accepts only the two exact inspected workbook hashes, reads cached values without evaluating formulas/macros/external links, checks selected row/year labels, and produces deterministic bytes. Raw workbooks are not bundled. A different source release requires a new reference version, fresh assessment and hash pin; do not overwrite this shipped version.
