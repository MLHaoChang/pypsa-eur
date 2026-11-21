# A project's own workbook leaves out columns it wrote, and the reasons use engine ids

Status: needs-triage
Blocked by: 02 (#1798)

See `../spec.md`. Found while taking the S3 preview shots from the real app.

- **Gap.** Export a saved project with `GET /api/projects/{name}/workbook`, then preview the file with `POST /api/workbook/preview`. The preview's "Left out" list has 4 rows, for example: "Generators › Outage rate value: 'outage_rate_value' is not a parameter of Generators". The writer emits app columns, such as the outage rate and MTTR, that the reader's allow-list does not accept. Those values do not round-trip.
- **Wording.** The reader's reason texts quote engine ids in repr quotes ('outage_rate_value', "Generator 'G1'"), and `rederived` rows do the same. The S3 dialog shows the backend's words as given, so these break the UX rule that a refusal or report uses plain words and the workbook's own sheet words (UX 27's condition 3 on S3).
- **Ask.**
  - Make the writer and the reader agree on every column the writer emits: either accept these app columns on read, or stop writing them.
  - Add a round-trip test that a saved project's exported workbook previews with an empty "Left out" list.
  - Reword the reader's reason texts and `rederived` rows in plain words: the column's display name, and no repr quotes.
  - Plan with GK B.
