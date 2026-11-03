* Fix(pypsa-gui): The adequacy site-peak check no longer counts a StorageUnit whose own ``active`` flag is off.
  Energy-hub lever studies skip a lever that has no asset to act on by error type (``LeverNotApplicable``)
  rather than by message text, and the storage-duration lever applies the same duration and C-rate checks as
  adding a split storage. Pumped hydro with inflow keeps its full count in the adequacy Monte Carlo, as before.
