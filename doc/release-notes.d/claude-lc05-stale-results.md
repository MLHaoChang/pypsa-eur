* Feat(pypsa-gui): After a chat edit, the assistant's result reads (`get_results`, `dispatch_status`) say whether the inputs changed since the last solve and whether the results are current by the same check Check answers uses, and call stale results stale instead of presenting them as current.

* Fix(pypsa-gui): A project solved in the app, saved and reopened no longer reads as not current in Check answers: user time series now keep their index name across save and reopen.
