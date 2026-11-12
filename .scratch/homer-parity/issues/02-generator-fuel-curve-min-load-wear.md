<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 — Generator realism: fuel curve, minimum load and run-hour wear

Status: needs-triage
Type: task
Gated: yes (GK B; GK A if it touches the IC/adequacy engine)
Spec: [spec.md](../spec.md) (HOMER-GAPS-1)

## What

HOMER Pro models a genset's fuel use as a no-load intercept plus a slope per kWh, a minimum load ratio, and an O&M cost and lifetime per running hour. In GridSpine a minimum output setting (`p_min_pu`) exists, but a fuel curve with no-load fuel use was found only in the data-centre backup code (`services/adequacy/dc_backup.py` heat rate). Without it, off-grid diesel results read optimistic: a genset idling at low load burns too little fuel.

## Likely shape

Optional fuel-curve inputs on a generator (intercept and slope, or a two-point curve), a minimum load ratio, and per-run-hour O&M, used in the dispatch (linear or with a commitment option) and in the cost breakdown. Reuse the DC backup heat-rate inputs where they fit.

## Triage

Confirm what exists on master, decide linear versus unit commitment, name the files and the reviewer, and set the model tier.
