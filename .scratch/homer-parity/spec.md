<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# HOMER Pro parity

Owner decision HOMER-GAPS-1 (2026-11-12): ticket three gaps against HOMER Pro now; battery fade and augmentation in the dispatch later; skip the kinetic battery model, 1-minute steps, a custom-dispatch link and the run-of-river / biomass-gasifier modules.

Source: a keyword pass over master on 2026-11-12 and `docs/superpowers/research/2026-09-28-competitors-der-and-cni-tools.md` §1.1. Each issue is needs-triage: triage checks the code first, since a feature may exist under another name.

GridSpine already co-optimises sizing and dispatch (HOMER enumerates a search space), models the network and power flow (HOMER has an active-power balance only), and has sensitivity sweeps, tornado, outage Monte Carlo, multi-period investment, hydrogen, CHP, a tariff pack with demand charges, and a finance engine with replacements, salvage, escalation and per-asset degradation.

## Owner addition

Load growth (issue 04): the owner, 2026-11-12, "Load growth is also very important to be considered for multi year optimization. The older module already considered a load growth factor per year."

## Later (not ticketed)

- Battery capacity fade and augmentation inside the dispatch over the project life (HOMER Advanced Storage and Multi-Year; HOMER Front). Today degradation scales the finance cash flow only (`services/finance/cashflow.py` `degradation_factor`).
