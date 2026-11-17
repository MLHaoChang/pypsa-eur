<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 11 — ui-modern 10 follow-ups: lock-held restore test, 409 doc

Status: needs-triage
Gated: GK B
Type: test
Parent: ui-modern 10 (#1479) follow-ups
Blocked by: #1479 merged

## Problem

1. No test covers a restore attempted while the network lock is held.
2. The doc comment at `pypsa-gui/frontend/src/api/campusElectrical.ts:1097` should name the `409 dc_backup_busy` response.

## Ask

Add the lock-held restore test and fix the doc comment.
