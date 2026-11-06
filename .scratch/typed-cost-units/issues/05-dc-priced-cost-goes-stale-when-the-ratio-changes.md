# A DC-priced cost does not go stale when the generator's DC/AC ratio changes

Status: needs-triage
Type: gap
Found in: TC-02 (issue 02) review condition 6 by GK A, 2026-11-06.

Issue 02 scales an `output_electric_dc` overnight cost by the generator's `dc_ac_ratio` and records the ratio it priced
with on the overnight cost's provenance (`dc_ac_ratio`). Nothing flags the cost when the ratio changes afterwards.
There is no existing staleness check to join: a Link's eta-scaled pack price is not flagged when its efficiency
changes either. Decide one mechanism for both (compare the recorded scale input with the current one, and show the
cost as stale on the asset card), rather than one per field.
