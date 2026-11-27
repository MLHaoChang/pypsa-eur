<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Checked-answer card text: rounded numbers, percent IRRs, no raw ids

Status: needs-triage
Type: task
From: UX (32)'s OK on #2077 @1a45519a, not blocking.

Problem: the checked answer's server text (harness/assurance.py ~l.718 and the profiles' notes) prints values with repr and joins notes raw, in /1 and /2: float noise ("1.6800000000000002 h", "4.132231404958667 USD", over-precise "10.0000%"); IRRs print as fractions, not percent; raw "analytic_convolution" in COPT notes; "; "-chained clauses; the paste-tip banner sits in the chat shots.

Scope: round for display and show IRRs as a percent without changing the checked value or its hash contract (a display layer, or a new profile version under AA-V1-CHANGE-1 if the text is part of it); words for raw ids; split chained clauses; shots without the paste-tip banner.
