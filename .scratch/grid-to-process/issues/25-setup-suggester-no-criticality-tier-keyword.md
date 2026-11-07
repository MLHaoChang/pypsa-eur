<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Setup suggester has no `criticality_tier` keyword match, so the concentrator's `site` is a medium-confidence pick

Status: done
Type: task
Found by: G2P-19 (#1189, merged), relayed by the coordinator; owner list DOCS-1.

## Problem

The setup suggester matches no `criticality_tier` keyword, so the concentrator's `site` is only a medium-confidence pick. Owner option D (draw only) stays the rule for now.

## Where to look

`pypsa-gui/backend/services/adequacy/eh_setup.py`. Details: ask the coordinator.

## Comments

### 2026-11-07: triage sweep (FILL-1107)

- Not buildable until answered.
- Question (GK A): Owner: should the setup suggester learn a `criticality_tier` keyword so the concentrator `site` becomes a high-confidence pick, or stay draw-only (option D) with a medium-confidence pick? Yes = we build the keyword match in `services/adequacy/eh_setup.py` (S, sonnet, GK A); No = this closes as wontfix.
- Blocked by: the answer.

### 2026-11-07: needs-info answered (coordinator)

Owner (card, G2P25-1): "Learn the keyword". Build the `criticality_tier` keyword match in services/adequacy/eh_setup.py so the concentrator `site` is a high-confidence pick. S, Sonnet, GK A.
