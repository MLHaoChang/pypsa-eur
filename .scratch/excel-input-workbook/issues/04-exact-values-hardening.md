# Exact values sheet and read caps: hardening after #1646

Status: ready-for-agent
Blocked by: 01 (#1646)

See `../spec.md` and the ask-1 ruling (X1–X6) in `01-reader-writer.md` ## Comments.

Filed at GK B 64's request with its OK on #1646 @590cac78849cdf10881efe2bf7b05cee9b943ed0.

- **Bound.** `_Reader.exact_value` (gridspine/ingest/excel_import.py) now accepts a hidden stored float when `rescale(stored, scale) == shown`. Also require `|stored − rescale(shown, 1/scale)| ≤ 2 ulp` of that value.
- **Zero and subnormal.** Refuse a hidden entry whose shown value is 0 or subnormal. Report it and ignore it, like any other malformed entry.
- **Wording.** The tests and comments say a tampered entry moves a value by "at most 1 ulp". Correct this to the bound the code enforces.
- **Workbook-wide cap.** The caps on rows, columns and cells count per sheet and across the read, but 25 sparse sheets still took 12 s and 459 MB. Consider a cap on the cell count for the whole workbook, checked before the passes, or a memory-bound check.
- **Tests and mutants.** Each guard gets a test and a targeted mutant listed in the PR body (E8).

## Comments
