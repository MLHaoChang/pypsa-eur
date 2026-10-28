* Refactor(pypsa-gui): Separate renewable profile generation from apply so the forthcoming Expert tuning dialog can wait for a pending profile without changing the model. Existing one-click generation still uses the project's weather choice and applies in one batch step.

* Feat(pypsa-gui): The profile comparison route accepts an omitted current side to preview a new profile on its own. The response explicitly omits the baseline and changes rather than inventing a zero profile; the assistant's comparison tool still requires both sides.
