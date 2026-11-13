* Feat(pypsa-gui): The Monte Carlo adequacy study (``POST /api/results/mc``), its ELCC table and the ELCC
  portfolio now sample the same fleet as the reliability loops. Where a trusted grid-import Link separates the
  hub from the grid, the fleet and the demand are the hub side and the Link is a sampled unit
  (``link:<name>``). The grid behind it drops out instead of being counted as must-take supply on a copper
  plate, which overstated adequacy (the double count the loops dropped earlier). A firm backup Link credited
  under the loops' unlimited-fuel rule is sampled too. The "nothing to sample" refusal is decided on this fleet.

  ELCC: a sampled ``link:<name>`` unit is offered and priced like a generator. An import Link taken as never
  failing is refused by name ("taken as never failing (no outage data); its credit would restate that
  assumption, so none is priced"). A firm backup the loops withhold (a store-fed fuel cell, a capped fuel) is
  refused with the withhold reason. A generator, must-take plant or storage on the grid side is refused naming
  the import Link. The candidate list is read off the same fleet, so every candidate resolves and no grid-side
  asset is offered. The portfolio's must-take members are the hub-side ones. Against a reserve-margin payload,
  margin rows on the grid side are left out of the "absent from the snapshot" check and named in the block's
  ``notes``, with the two standards stated: the margin's derate and peak window are taken on the copper-plate
  group, the ELCC against the hub-side residual.

  What changes on the shipped energy-hub templates (MC study on the unsolved template, 500 draws, seed 0,
  ELCC on up to three candidates with any ``link:`` unit first, the portfolio on; LOLE before → after):

  | Template | Before | After | ELCC after (MW) | Portfolio after |
  |---|---|---|---|---|
  | ``eh_h2_hub`` | refused (nothing to sample) | 3.08 h/yr | link:grid_import 16.7, wind_farm 30.2, solar_park 11.3 | 37.5 MW |
  | ``eh_h2_hub_staged`` | refused (nothing to sample) | 51.73 h over 18 years | link:grid_import 16.4, wind_farm 30.5, solar_park 12.5 | 37.5 / 37.8 / 38.1 MW |
  | ``eh_datacenter`` | 0.0 h/yr | 13.40 h/yr | link:grid_import 25.2, ups_battery 4.7, genset_1 10.0 | 1.0 MW |
  | ``eh_datacenter_staged`` | 0.0 h | 167.9 h over 18 years | link:grid_import 25.8, ups_battery 4.4, genset_1 10.0 | 2027 unidentifiable, 1.0 / 1.0 MW |
  | ``eh_microgrid`` | 0.0 h/yr | 4.59 h/yr | pv_plant 1.5, island_battery 3.4, wind_turbines 1.2 | 2.6 MW |
  | ``eh_microgrid_staged`` | 0.0 h | 500.9 h over 18 years | pv_plant 2.2, island_battery 4.5, wind_turbines 1.2 | 2.8 / 3.6 / 3.9 MW |
  | ``eh_ammonia_staged`` | refused (nothing to sample) | 6.60 h over 18 years | link:grid_import 9.4, wind_farm 35.3, solar_park 12.0 | 38.7 / 38.7 / 39.3 MW |
  | ``eh_ipp_hybrid`` | refused (nothing to sample) | refused (nothing to sample) | — | — |
  | ``eh_concentrator_staged`` | refused (nothing to sample) | 330.3 h over 18 years | link:grid_import 3.0 | no population |

  Before, every ELCC row was ``unidentifiable`` (the study either refused or found no loss of load to price
  against), and the candidates included the grid-side supply (``grid_supply``, ``mainland_supply``). Four
  templates move from a refusal to a verdict. The four that read 0.0 before did so only because the grid
  generator was netted as must-take with no import limit; ``eh_microgrid``'s subsea tie has no capacity in any
  hour, so its island now stands alone. These are unsolved templates (no capacity expansion), so the staged
  numbers include periods whose capacity is built only by a solve. No reserve margin exists before a solve, so
  every portfolio block with members reads ``margin_unavailable``.

  Known limits: process Links and withheld converters are still not sampled, so the study's ``process_loss``
  stays ``not_established``. The portfolio comparison keeps two standards rather than rebasing the margin.
