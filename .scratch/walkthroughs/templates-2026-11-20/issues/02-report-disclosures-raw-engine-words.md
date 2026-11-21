<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 — Report disclosures use engine and tool words and a hydrogen note in non-hydrogen projects

Status: needs-triage
Impact: medium
Templates: DC, microgrid (H2 shows the jargon without the tea line)
Found by: template walk, 2026-11-20 (master 1f6905e3)
Shot: ../shots/edc-rep.png

## What

Expert > Reports > Create evidence report opens a page of 'Disclosure' boxes. They include: 'tea — LCOH skipped: the network has no electrolyser Links' (DC and microgrid: a hydrogen note, with a raw 'tea' prefix, in a project that has no hydrogen), 'Name the engine and its fidelity beside every number — this document puts a screening convolution, an LP proxy and a sampler on one page', 'The COPT figure is a screening estimate — thermal-only, storage-excluded, network-free', 'is on a single_period basis ... do not add a VoLL term' and 'certification — the engine's own standing note ... DSR slacks ... LP proxy gap'.

## Why it matters

The report is the document a reader outside the tool gets. These sentences name engine internals and terms the user never met (RAW-NAMES-1), and a hydrogen disclosure in a data-centre report reads as a copy-paste error.

## Suggested fix

Rewrite the disclosures in the user's words (what the number is, what it leaves out). Drop 'not applicable' notes such as LCOH-skipped when the project has no such asset.
