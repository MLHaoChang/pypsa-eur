<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 11 — ui-modern 10 follow-ups: lock-held restore test, 409 doc

Status: ready-for-agent
Model tier: sonnet
Size: XS
Likely files: `pypsa-gui/frontend/src/api/campusElectrical.ts` (doc comment on `setDcBackup`), `pypsa-gui/backend/tests/test_template_marker.py`
Gated: GK B
Type: test
Parent: ui-modern 10 (#1479) follow-ups
Blocked by: none (#1479 merged)

## Problem

1. No test covers a restore attempted while the network lock is held.
2. The doc comment at `pypsa-gui/frontend/src/api/campusElectrical.ts:1097` should name the `409 dc_backup_busy` response.

## Ask

Add the lock-held restore test and fix the doc comment.

## Comments

**2026-11-17 triage (SCALE-1).** The blocker #1479 merged. Item 2 still open: the `setDcBackup` doc comment names 422 only, not 409 `dc_backup_busy`. Item 1 is partly met: `test_the_dc_backup_route_answers_409_busy_while_the_marker_is_locked` covers the DC-backup route; the builder checks whether a restore (undo) attempt while the lock is held still needs its own test.
