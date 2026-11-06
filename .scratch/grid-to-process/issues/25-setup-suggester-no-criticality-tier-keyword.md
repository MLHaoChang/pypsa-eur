<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Setup suggester has no `criticality_tier` keyword match, so the concentrator's `site` is a medium-confidence pick

Status: needs-triage
Type: task
Found by: G2P-19 (#1189, merged), relayed by the coordinator; owner list DOCS-1.

## Problem

The setup suggester matches no `criticality_tier` keyword, so the concentrator's `site` is only a medium-confidence pick. Owner option D (draw only) stays the rule for now.

## Where to look

`pypsa-gui/backend/services/adequacy/eh_setup.py`. Details: ask the coordinator.
