<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 63 — eh_site fuel price: an electrical word next to a thermal-less price still verifies

Status: needs-triage
Type: task
Gated: yes (GK B)
Parent: [34-eh-site-genset-fuel-price-research.md](34-eh-site-genset-fuel-price-research.md) (GK B (70) N3b, #1950)

`harness/research.py` `_states_electrical` accepts a genset fuel-price quote when an electrical
unit (`MWhe`, `MWh_el`, `electric… MWh`, `MWh of … electric…`) follows the stated value within
60 characters and before any other number. The words in between are not parsed. So a quote whose
electrical word belongs to something else still verifies, for example:

> diesel EUR 60 per MWh, unlike electricity MWh tariffs

There, 60 EUR/MWh is a fuel-energy price, and the run would write it as the gensets' cost per MWh
of electrical output (about 2.5 times too low). The user's research card still shows the quote
before anything is written.

Acceptance:
- [ ] The quote above is refused (`research_unit_mismatch`), with a test.
- [ ] The cases pinned in `tests/test_harness_run_eh_site_v5.py` keep their verdicts.

## Comments
