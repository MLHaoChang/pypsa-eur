* Fix(pypsa-gui): Compare Economics now books a storage's holding cost (`marginal_cost_storage`, state of charge × cost × the objective and period weighting, as the LP prices it) in its carrier's opex, for a StorageUnit and for a split storage's Store alike. Other Stores stay unbooked, as their marginal cost is.

* Fix(pypsa-gui): Compare Dispatch applies the results readers' name rule to split storages, so a Store named like a StorageUnit, or a split storage discharging to another bus, stays out of the energy mix and the cycles; a split storage's dispatch and cycles read its signed AC-side power, as a StorageUnit's do, so a snapshot where its charger and discharger both flow now counts the net, not the gross.

* Fix(pypsa-gui): The load shape's battery state of charge no longer double-counts a StorageUnit and a Store that share a name (intended change: such a network now shows the StorageUnit's state of charge once), and the EV and flexible-load series read that Store's own carrier.
