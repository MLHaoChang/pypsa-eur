<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 09 — "Expected shortfall is out of date" after a dispatch solve

Status: needs-triage
Severity: medium (it may be a real defect: a solve that changes no input marks the reliability figure stale)
Group: Investment and business case. Found in the issue 02 re-walk, 2026-11-23, master 443ec313.

## Repro

1. Create "Data Center Energy Hub" from the template form with every default.
2. Guided › 9 Readiness and run › Run study (the reliability study, about 6 min here). Section 10 shows "Expected shortfall 0.5 h/yr".
3. Section 10 › Investment tile › Run the investment case → "The investment case needs: A solved project — Run the solve" → Run the solve (about 5 s).
4. Run the investment case, reload, open section 10.

## Actual

The headline says "Not all figures describe the current design: Expected shortfall is out of date."

## Expected

The figure stays current if the dispatch solve changes none of the study's inputs. If the solve does change them, for example by writing optimised sizes of the extendable genset or BESS back into the network, the note names what changed. Find out which of the two happens before fixing anything: compare the study's input fingerprint before and after the solve.
