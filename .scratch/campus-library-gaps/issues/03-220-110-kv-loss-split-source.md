<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# The 220/110 kV class-rule losses miss Ecodesign Tier 2 and need a sourced no-load/load-loss split

Status: needs-info
Type: task
Blocked by: None

Filed by campus-library-gaps 02 (2026-11-11), from issue 02's check.

## What was seen

All five units TR_220_110_125/160/200/250/315 reach PEI 99.654 %, below the Tier 2 minimum (99.780 to 99.797 %, Regulation (EU) 2019/1783 Annex I Table I.7) and below Tier 1 (99.737 %). PEI bounds only sqrt(P0 x Pk): at the rule's uncited Pk of 0.3 %, P0 is 2.5 to 2.9 times what Tier 2 allows, so the rule overstates losses against a compliant unit with that Pk (conservative for loss cost), but a compliant unit with a higher Pk can lose more at high loading, so the rule bounds losses neither way. See issue 02 ## Comments.

## What to do

1. Find a public datasheet, tender document or standard giving P0 and Pk for 220/110 kV transformers at 125-315 MVA.
2. Propose the values as a reviewed change to `gridspine/templates/data/campus_assets.yaml` (`pfe_kw`, `vkr_percent`, `i0_percent`). A new value changes the library's sha256, so it needs a new defaults pack version. Reviewer: GK A.
3. Until a source exists, the class rule stays, tagged `assumed`.

## Done

A sourced split is merged, or the owner accepts the `assumed` class rule with its Tier 2 miss.

## Comments

### 2026-11-13: source found (near class), built pending the owner's ruling (campus-library-gaps 03, session_01G7qCuo2Aycih94TRsBiQx5)

Owner decision CLG03-SOURCE-1: "Source first (Recommended)".

**Search.** No public P0/Pk split for 220/110 kV at 125-315 MVA was found. Tried and found unusable:
- CEA (India) "Standard Specifications and Technical Parameters for Transformers and Reactors (66 kV & above)", 2021, and its Amendment 01: every cea.nic.in URL returned 404.
- OPTCL E39 and TS-11/2018-19: losses are bidder-quoted only.
- CEB Sri Lanka: covers 132/33 kV only.
- PSE PSE-ST.ATR.KEP: no public loss table.
- VITO Ecodesign review study: no large-unit base case could be fetched.
- KSEB, GETCO and TGTransco: their files could not be fetched.

**Source used.** [OPTCL-TS]: Odisha Power Transmission Corporation Ltd, "Technical Specification for Power and Auto Transformers (From 132 kV up to 400 kV level)", Revision 1, May 2025, Annexure-A section C "200 & 160 MVA 220/132 kV 3-phase auto transformer (NEW)", item 32 "Maximum Permissible Losses". It also gives impedance 12.5 % at the principal tap. Clause 9.3 says the limits follow the latest CEA guideline. https://www.optcl.co.in/writereaddata/Tender/110325040316TenderSpecification%20(2).pdf (HTTP 200, 2026-11-13, sha256 3d69b0867ae94b66af53771bc36235429006db9896ac0e81d10d6ea71509bde2). Values as printed:

| Unit | Max no-load loss (kW) | Max load loss, 75 °C (kW) |
| --- | --- | --- |
| 160 MVA | 30 | 200 |
| 200 MVA | 35 | 260 |

**Values built.** OPTCL × 1.25 as autotransformers (GK A C1, see below). The 125 MVA unit is scaled down from the 160 MVA row, and 250/315 MVA up from the 200 MVA row, at constant kW/MVA. All stay tagged `assumed`.

| Unit | pfe_kw | vkr_percent | Pk kW | PEI % | Tier 2 % |
| --- | --- | --- | --- | --- | --- |
| TR_220_110_125 | 29.3 | 0.15625 | 195.3 | 99.879 | 99.780 |
| TR_220_110_160 | 37.5 | 0.15625 | 250.0 | 99.879 | 99.790 |
| TR_220_110_200 | 43.75 | 0.1625 | 325.0 | 99.881 | 99.797 |
| TR_220_110_250 | 54.69 | 0.1625 | 406.3 | 99.881 | 99.797 |
| TR_220_110_315 | 68.91 | 0.1625 | 511.9 | 99.881 | 99.797 |

i0 and vk keep the class rule. Defaults pack 2026-11-13 pins the new library sha256.

**GK A plan verdict (GO with conditions; build only after the owner rules that 220/132 counts as a source):**
- C1 Winding type. OPTCL's rows are autotransformers, whose losses follow S(1 − U_LV/U_HV). If the TR_220_110 units are autos, scale by 0.50/0.40. If they are two-winding, OPTCL does not apply and the question goes back to the owner. Each note names the winding type. Finding: Ei R-NR-T-8 says only "Transformator", so the winding type is unstated. Autotransformer (×1.25) was put to the owner as the recommended option.
- C2 Scaling disclosure. The notes name the base row and say "constant kW/MVA". The 250/315 MVA notes say "extrapolated beyond the source's range", and the 125 MVA note says "scaled down".
- C3 The notes say "maximum permissible" and "a tender cap"; the tag stays `assumed`.
- C4 The PR lists the unpinned `load_defaults_pack()` callers. A test shows that 2026-11-13 equals 2026-11-10 except for the manifest. A second test shows that the YAML differs from the previous pin only in the five units' `pfe_kw` and `vkr_percent` and in the citation.
- C5 List the templates and tests that build a TR_220_110 unit. Run the sidecar byte-identity check and state any deltas.
- C6 The class-rule exemption is keyed on the five ids. A mutant that puts the class rule back on one 220/110 unit must fail the new pins.

Owner questions sent to the coordinator: does 220/132 count as a source, and are the units autotransformers?
