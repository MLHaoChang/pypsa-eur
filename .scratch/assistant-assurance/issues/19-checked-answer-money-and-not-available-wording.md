<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# € and "Not available: <reason>" wording for all checked answers, and no raw reason code in the text

Status: needs-triage
Type: task
Found by: UX 25 on S3 (`10-metric-calculation-and-validity.md`, objective-to-cost bridge), 2026-11-17.

## Problem

Every checked chat answer is rendered by one server renderer (`harness/assurance.py`, the slot loop after the profile notes). It prints money as `1234.5 EUR` and an unanswered slot as `Label: unavailable (reason_code).`. The rest of the app writes money in its own format (`frontend/src/utils/money.ts`, `€`). An internal reason code such as `losses_incomplete` or `unverified_solve` reaches the user as-is.

UX 25 waived this for S3 only. S3 uses plain-word codes, each explained by a note line, until this ticket lands.

## Scope

- Money in the app's own format (€) in every checked answer's text, for every profile.
- An unanswered slot reads `Not available: <plain reason>`; no raw reason code and no raw JSON in the text.
- The publication gate's number matching (assurance.py, the numeric-token check) must keep matching facts to text after the format change. Facts stay byte-identical.
- One review by UX (wording, every profile's shots) and by GK B (gate and receipt unchanged).

## Acceptance

- Exact-text tests for one answered and one unavailable slot per profile family.
- A test that no `[a-z]+_[a-z_]+` token reaches an answer's public text.
