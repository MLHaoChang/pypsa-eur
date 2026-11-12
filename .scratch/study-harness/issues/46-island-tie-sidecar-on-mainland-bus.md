<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 46 — Island tie reads no_tie when a sidecar unit sits on a mainland bus

Status: needs-triage
Type: bug
Gated: yes (GK A)
Parent: SH-12a (#1361), GK A (23) review finding N5, non-blocking per the coordinator

## What

When an island sidecar unit is placed on a bus that belongs to the mainland, the tie check reads `no_tie` instead of identifying the island/mainland link.

## Expected

The island is defined from its own buses (standing rule: never from a tag on one external bus, #1361 F3). A sidecar unit on a mainland bus is either refused with a typed reason or classified by its bus, never read as `no_tie`.

## Acceptance

- [ ] A fixture with a sidecar unit on a mainland bus asserts the chosen behaviour.

GK A reviews.
