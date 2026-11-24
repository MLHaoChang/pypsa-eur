<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 11 Validate the PSS/E, PowerFactory and PSCAD exports in the licensed tools

Status: needs-triage
Blocked by: gc/23 (download), 10
Source: DC template review 2026-11-24 (`../README.md`). Stage 7.
Severity: major (until done, "validated in those models" cannot be claimed)
Model: none for the run (a person with the licences); sonnet for the follow-up fixes. Review: GK A.

## Problem

Every export test runs against the app's own parsers, pandapower or stubs: no test or recorded run imports `psspy`, `powerfactory` or `mhi.pscad` outside generated script text. `dgs_writer.py` is marked `PENDING_VERIFICATION` (`:95`); the PSCAD stub says it is untested against a PSCAD install (`emt_pack.py:524-525`); `tests/gridspine/test_raw_writer.py:76` skips when pandapower lacks `from_psse`. Read-back (`gridspine/readback/`) is built and judged against benchmarks (gc/07), but never fed with a real tool's output.

## Acceptance criteria

- [ ] For the staged DC example: the RAW/DYR opens in PSS/E (version named), solves a load flow, and the bus voltages and branch flows match the app within a stated tolerance; the psspy short-circuit matches IEC 60909 within tolerance.
- [ ] The DGS imports in PowerFactory (version named) and its load flow matches; every `PENDING_VERIFICATION` attribute is confirmed or corrected.
- [ ] The PSCAD pack builds and initialises in PSCAD (version named); a flat run settles.
- [ ] The tool outputs go through the app's read-back and the judge, and the result files are stored as fixtures for a regression test.
- [ ] A short record per tool (version, date, deviations) under `.scratch/grid-compliance/evidence/`, citing only public data.

## Comments
