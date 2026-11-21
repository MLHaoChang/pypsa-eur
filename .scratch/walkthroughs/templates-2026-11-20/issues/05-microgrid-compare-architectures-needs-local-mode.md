<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05 — Microgrid Results: 'Compare supply architectures' ends in 'Needs local mode'

Status: needs-triage
Impact: low
Templates: microgrid
Found by: template walk, 2026-11-20 (master 1f6905e3)
Shot: ../shots/gm-afterrun.png

## What

Guided Results shows a 'Grid extension vs island' card ('How far from the grid does extending it cost less than supplying the site off-grid?') with a 'Compare supply architectures' button and the note 'Needs local mode'. The walk ran on the local dev server, so the app is in local mode as the owner runs it; the note does not say what local mode is, how to get it or who can.

## Why it matters

A card offered on the template's own results page leads nowhere, and 'local mode' is not a word the user knows.

## Suggested fix

Say in plain words what is missing and how to get it, or hide the card where it cannot run.
