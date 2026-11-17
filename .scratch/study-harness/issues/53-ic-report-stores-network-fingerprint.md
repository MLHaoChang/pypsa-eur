<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 53 — The IC report stores the network fingerprint it was built on

Status: needs-triage
Gated: GK A
Type: task
Parent: SH 14 S-C (#1590), GK B (61) C9/S4

## Problem

The stored investment-case report (`services/finance/report.py`, written by
`investment_case_runner.start_investment_case`) carries its `assumptions_hash`
and per-part digests (finance, commercial, value flows, solver config, the
solve's dispatch digest, packs, owner and extra assets), but not the network
input fingerprint (`workflow_tools._network_input_fingerprint`) of the network
it was built on.

## Why S-C needs it

S-C's `ppa_price` step binds `run.ppa_price` to the network fingerprint (GK B
(56) C9, GK B (61) S4: "compare the fingerprint against the IC report's own
value and the current network"). With no fingerprint on the report, the step
cannot compare against the report's own value. Until this lands, S-C binds the
fingerprint as: the case's results are current at the step
(`solve_freshness`), the fingerprint is stable during the read (read before
and after), and it equals the recorded one at read-back. The assumptions hash
covers the dispatch digest, so a re-solve on other inputs is already caught.

## What to build

The case runner records the fingerprint of the network it built the case on
(read under the same lock as the case build) on the stored report, and
`harness/run_ppa.py` compares it with the current network at the step and at
read-back, each with its own red case. Engine-side, so GK A.
