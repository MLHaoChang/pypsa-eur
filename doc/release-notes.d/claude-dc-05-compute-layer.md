* Feat(pypsa-gui): The data-centre load models all four workload kinds (AI training, inference, mixed, flat) with assumed hourly shapes. `inference`, `mixed` and `flat` were refused when creating a project and, in a stored spec, ran with the AI-training shape; they now follow their own. No shipped template or stored example uses them.

* Feat(pypsa-gui): An optional compute block (accelerators, kW per accelerator, utilisation) shows the IT MW it implies beside the typed IT MW. The typed value always wins and the block changes nothing in the model.

* Fix(pypsa-gui): The never-read `waste_heat_export` input is removed. An old spec that sets it still loads and its ledger says it was ignored; a spec's dump no longer carries that key.
