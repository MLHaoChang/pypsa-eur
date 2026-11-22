<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 08 — Edit form: the Harmonic source dropdown cuts off its longest option

Status: ready-for-agent
Type: task
Gated: yes (UX)
Depends on: DGS-EXTRAS-1 (#1765); lands with slice 03 (Expert panels and mode parity, which switches options to their plain names).

## What

In the Generator and Load edit forms, Harmonic source sits in a half-width column. The closed dropdown shows the selected "active-front-end" as "active-front-er". The value is the gridspine harmonic-source key that the DGS import and `parse_spectrum` defaults read, so DGS-EXTRAS-1 can't shorten it. The shot is `.scratch/expert-catalogue/shots/dgs-extras/edit-gen-slack-light.png`. UX 26 ticketed it on #1765.

## Acceptance

- Give each Harmonic source option a plain display name in `entries.py` (`options_plain`), with the stored key unchanged. Slice 03 shows plain option names.
- The longest display name reads in full in the closed dropdown at the half-width edit column. Otherwise the field takes the full width.
- An edit-form shot with "active-front-end" selected, in light and dark.

## Comments
