<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-36 — Behaviour coverage for the six remaining LC-D13 variants

Status: ready-for-agent
Type: task

Owner decision LC33-ROWS-1: one Opus package covers the six `_pending` LC-D13 rows that
LC-33 (#945) added and owned `UNASSIGNED` in `OWNERS` (`smoke/release_evidence.py`). GK A
reviews.

| Variant | Annex clause (domain-coverage.md:74) |
|---|---|
| `LC-D13.multi_energy_heat` | Heat attribution |
| `LC-D13.finalist_certification` | Finalist certification |
| `LC-D13.unsupported_scope` | Unsupported scope |
| `LC-D13.study_swap` | Study swap |
| `LC-D13.stale_source` | Stale source |
| `LC-D13.incomplete_campaign` | Aborted / incomplete campaigns |

`LC-D13.multi_energy_dedicated_rollup` stays `_unavailable` (unreachable from the seam).

Same bar as LC-33 (`33-unassigned-variants-d06-d13.md`) and its `_covered` rows: a test
drives the real engine through the chat tools (`seam` from `test_lc06_d05_foresight.py`,
`chat_service._dispatch_real_tool_call`), pins a hand-computed number or outcome, and a
mutation moves it by a hand delta. No engine stubs. A variant the seam cannot reach is
recorded as `_unavailable` with the reason, as GK A ruled on #945.

Plan first: post each variant's oracle and mutation in this file for GK A's GO before
writing tests.

## Acceptance

- [ ] Each of the six rows is `_covered` (or `_unavailable` with GK A's OK) in
      `smoke/family_manifest.py`, and `OWNERS` names LC-36.
- [ ] test_family_manifest, test_release_evidence and the new tests pass.
- [ ] GK A OK on the final head.

## PRs

## Comments
