<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Queue location refusal: deferred journey step and two GK notes

Status: needs-triage
Type: task
Review: GK B + UX
Source: te 50 hand-off (#2116) (filed 2026-11-28 from the coordinator's follow-up lists)

## What is left

- The journey step for the queue location refusal was deferred; add it to the template journeys.
- GK: a refused job passes the claim. It shows as running for an instant, and `cleared_result_state` blanks in-memory results until reload.
- GK: the study-running `RuntimeError` precedes the location check, so a project with both reads `queue_error`; the order should put the location refusal first.

## Comments
