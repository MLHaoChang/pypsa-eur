* Feat(pypsa-gui): Guided and Expert name each result figure alike (guided-results 04, slice 2).
  The frontend reads `services/figure_words.json`. The Investment tab and Guided's NPV tile say
  "Project NPV at the WACC, after tax (unlevered)", and Guided keeps "What the design is worth" as
  a lead line above it. With no tax pack, the tile shows the pre-tax NPV under its own name,
  "Project NPV at the WACC, before tax (unlevered)". Adequacy and Guided say "Expected shortfall"
  (h/yr), with LOLE in the tooltip. The LCOE card's basis line gives its money year. Compare's
  per-carrier "LCOE" row is now "Carrier LCOE (before financing and tax)", since it is not the investment case's LCOE.
