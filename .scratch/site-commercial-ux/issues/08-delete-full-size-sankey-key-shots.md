<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 08 Delete the full-size `sankey-key` shots

Status: needs-triage
Source: UX nit on the Sankey key shots (UX follow-up, 2026-11-24)

## What

Delete `sankey-key-light.png` (216 KB) and `sankey-key-dark.png` (229 KB). The `.small.png` copies carry the evidence; the full-size files only add weight to the repo (SHOTS-HOME-1 asks for downscaled shots). Check nothing links the full-size names first, including `pypsa-gui/frontend/scripts/sankey-key-shots.mjs` and `doc/release-notes.d/claude-sankey-key-process-label.md`.

## Where

`.scratch/site-commercial-ux/shots/`
