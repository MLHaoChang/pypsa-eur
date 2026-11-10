* Feat(pypsa-gui): The reliability loops (the coupling loop and the reserve-margin loop) and the energy-hub
  study's fixed-plan check now sample outages carried on Links. A grid-import Link with outage data is a
  two-state unit on the hub side, per investment period, and the grid behind it drops out of the fleet instead
  of being counted as must-take supply. An import Link without outage data is taken as never failing, and the
  loop record says so. A firm backup Link (a fuel cell or engine feeding an electrical bus) is credited only on
  an unlimited fuel. That holds for every bus the Link draws from (``bus0``, and any further port with a
  negative efficiency): the bus carries no Store, StorageUnit or Load, is fed by no other Link, is shared with
  no other Link, and has a fuel Generator without an energy cap. A GlobalConstraint that caps the fuel's
  carrier, or outage data set on the fuel Generator itself, also withholds the credit. The credit is bounded by
  the fuel Generator's capacity and availability and is never negative. A Link that is not credited is named
  with the reason in the loop record's ``fleet_notes``. The energy-hub study's fixed-plan check builds its
  fleet with the same code as the loops, so both sample the same units in the same order.

  What changes on the shipped energy-hub templates (coupling loop, goal 2 h/yr, 500 draws, before → after):

  | Template | Before | After |
  |---|---|---|
  | ``eh_h2_hub`` | refused (nothing to sample) | met, 1.46 h/yr |
  | ``eh_h2_hub_staged`` | refused (nothing to sample) | unreachable, 36.19 h over 18 years (goal 36 h) |
  | ``eh_datacenter`` | met, 0.0 h/yr | met, 1.15 h/yr |
  | ``eh_datacenter_staged`` | met, 0.0 h | met, 0.0 h |
  | ``eh_microgrid`` | met, 0.0 h/yr | met, 0.0 h/yr |
  | ``eh_microgrid_staged`` | met, 0.0 h | met, 0.0 h |
  | ``eh_ammonia_staged`` | refused (nothing to sample) | met, 6.67 h over 18 years (goal 36 h) |
  | ``eh_ipp_hybrid`` | refused (VoLL is 0) | refused (VoLL is 0) |
  | ``eh_concentrator_staged`` | refused (nothing to sample) | no longer refused; not run to a verdict (see below) |

  The ``eh_h2_hub`` run profile's reliability rule therefore moves from blocked to passed. That pass is two
  sampled shortfall events in 500 draws; a third would give 2.19 h/yr and miss the 2 h/yr goal, and the loop
  record marks the estimate as not confident. ``eh_h2_hub_staged`` stays blocked: the import's outages alone
  miss the goal and no lever moves them.

  ``eh_concentrator_staged`` now starts the loop, but its first solve did not finish within 26 minutes on the
  test machine, so it has no before/after verdict here.

  Known limits: the Monte Carlo study, ELCC and the portfolio still sample generators only, so on these
  templates the study still refuses or counts the grid as must-take. The run gate does not yet show
  ``fleet_notes``. An estimate within one sampled event of the goal still reads as a pass.
