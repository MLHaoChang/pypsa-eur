<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Slice 3: run lessons via a dream job

Status: needs-triage
Blocked by: 02
Type: feature
Model: Opus. Reviewer: GK B.

A durable harness task, modelled on Anthropic's "Dreaming with a dynamic workflow" cookbook (MEMORY-DREAM-1):
- It reviews past chats and runs: an analyzer per transcript, then one organizer.
- It writes proposed lessons, which the user confirms.
- It is budget-capped and provider-agnostic.
