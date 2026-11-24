<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05 Commercial library: no "Save to library", no shared scope, no "update available", no live tariff lookup (K2, L1–L5)

Status: ready-for-agent
Owner: SCU05-L15-1.
Blocked by: 03 for the inline pickers
Design: `.scratch/project-workflow/library-and-live-lookup.md` §1–§3 and §5; commercial-finance.md §7 row K2. Related in flight: `project-io/03` (#1689: a project keeps a copy of each pinned payload).
Source: gap review 2026-11-21, design map.
Model: sonnet (L1–L3), opus (L4–L5: network, allowlist). Review: UX; GK B (library schema; L4 security).

## Problem

`pages/economics/CommercialLibraryPage.tsx` lists library items. Missing: "Save to library" from a project's tariff or PPA (L1), common/workspace scope (L2), "update available" on a pinned item (L3; #1689 records the pinned copy this needs), and the live lookup of tariffs and PPAs from an allowlist of official hosts (L4, L5). None is filed.

## User-visible impact

A tariff a user built in one project cannot be reused in the next, and a pinned item never says it is out of date.

## Question for the owner

1. **(Recommended) L1–L3 now** (Sonnet, after #1689 merges); L4–L5 later, behind their own security plan.
2. L1–L5 together as one planned stack.
3. Not now.

## Acceptance criteria (L1–L3)

- [ ] "Save to library" on a project's tariff/PPA creates a workspace-scope item that another project in the workspace can pick.
- [ ] A pinned item whose library version is newer shows "Update available", with what changed, and updates only on the user's click.
- [ ] End to end in the real app: save a tariff from project A, pick it in project B, edit it in the library, and see "Update available" in B.

## Likely files

`pypsa-gui/frontend/src/pages/economics/CommercialLibraryPage.tsx`, the library routes and service in `pypsa-gui/backend`, the section-7 pickers.

## Comments
