* Feat(gridspine): The campus run screens the planning criteria (TRGC 15) and grid strength.

  ``planning_campus`` runs ``screen_planning`` at each period's max-import and max-export hour, over the campus N-1 or an imported TSO list, and writes ``campus_planning.csv``. It fails closed: a non-converged or load-losing case fails, as does an hour or period whose intact load flow does not converge, an empty set reads "not evaluated", and a list with excluded contingencies never reads pass.

  ``strength_campus`` writes intact and worst-outage SCR/ESCR per PoC and stage (``campus_strength.csv``) and WSCR (``campus_strength_groups.csv``). The fault level is the IEC 60909 minimum case with every converter off, and P is the rated power. It is a static screen; the binding answer is the RMS/EMT study.

* Fix(gridspine): Engine change to ``static/planning_criteria.py``: a charging storage unit lost in an N-1 case no longer gives a negative "generation lost" that made the whole screen refuse. Generation lost now counts each unit's injection clipped at zero, and the removed charging draw is named in the case note.

* Fix(gridspine): The contingency CSV import refuses an ``order`` other than 1 or 2, and lists an N-2 row that names one branch twice as not representable.
