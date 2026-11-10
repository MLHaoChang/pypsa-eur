* Feat(pypsa-gui): Study runs on the H2 hub templates (`eh_h2_hub@1`, `eh_h2_hub_staged@1`): the H2 offtake must be served in every hour, electrolyser utilisation is reported against the profile's sizing-screen band, LOLE runs through the coupling loop, and the investment case is checked for an established counterfactual without blocking the run.

* Fix(pypsa-gui): A queued solve on a network with existing-capacity costs now settles as completed instead of unverified.
