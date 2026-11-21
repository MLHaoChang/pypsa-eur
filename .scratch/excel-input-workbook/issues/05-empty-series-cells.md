# Empty time-series cells in the older Excel layouts

Status: done (#1861)
Blocked by: 01 (#1732)

See `../spec.md`. Filed at GK B 68's request with its OK on #1732 @edb18b9eb8d20299221c8431059925190be5d42e. The gap predates #1732.

- **Gap.** In PyPSA's own export layout (`_read_native` / `_check_native_cells` in `gridspine/ingest/excel_import.py`), an empty cell inside an asset's time-series column is skipped by the E3 check. PyPSA's reader then stores it as NaN, with no report row. The NaN reaches the model as a missing value.
- **Ask.** Report each asset whose series has an empty cell before the sheet's last row. Then either drop that asset's series, as for a bad cell, or refuse it. Decide which with GK B in the plan. Test it on a series sheet in `_two_gens()`.
- **Check also** the raw-sheets layout and the input workbook's Time series sheet for the same gap, and say in the PR whether each has it.
