<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05c The in-hour peak uplift applies the training share to every workload kind

Status: needs-triage
Blocked by: none
Source: GK review of the DC 05 tail (load wording by workload kind).
Severity: minor

## Problem

`services/adequacy/grid_readiness.py` `_in_hour_uplift_mw` computes the in-hour
step above the hourly mean as IT MW × (full share − training hourly mean) × PUE
for any stored `dc_load.spec`, whatever its `utilisation` kind. An inference,
mixed or flat project gets a training-shaped uplift, and the energization-peak
note still says "the in-hour AI-training step". Since the DC 05 tail, the rest
of the scorecard names the load by its kind, so this note is the one place
where the old wording remains.

## Wanted

The uplift follows the stored kind: zero or a kind-specific step for flat and
inference, and the mix's training share for mixed. The note names the kind.
That changes the math as well as the words, so the warning can move.
