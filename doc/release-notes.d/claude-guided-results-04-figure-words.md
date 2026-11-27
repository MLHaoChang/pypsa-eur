* Feat(pypsa-gui): One name per result figure (guided-results 04, slice 1). A new
  `services/figure_words.json` holds the name, unit and basis of the expected shortfall, the
  project NPV and the LCOE. Reports now print "Expected shortfall (h/yr)" (was "MC LOLE (h/yr)"),
  "Project NPV at the WACC, after tax (unlevered)" and "LCOE (finance-consistent, nominal)". The
  investment table's units follow the report currency instead of a fixed €. The
  `/results/headline_kpis` LCOE card carries its `money_year` and basis line. Reports drafted
  before this change will ask to be redrafted once, because their printed words changed.
