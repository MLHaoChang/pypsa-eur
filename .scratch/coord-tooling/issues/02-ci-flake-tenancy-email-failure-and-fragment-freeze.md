<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# CI flake in test_tenancy_api and an unverified fragment check

Status: needs-triage
Type: task
Review: GK B
Source: AA 09 and retro-frag hand-offs (filed 2026-11-28 from the coordinator's follow-up lists)

## What is left

- Flake: `test_tenancy_api::test_admin_create_user_survives_email_failure` (shared in-memory SQLite race).
- There is no macOS `.app` freeze, so `check_fragments` is unverified on that layout (retro-frag-1).

## Comments
