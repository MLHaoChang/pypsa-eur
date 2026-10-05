# Report-template fixtures (WP8, `services/reports/docx_reader.py`)

No `.docx` binary is committed here. `_build.py` builds every fixture with
python-docx; the tests (`tests/test_report_docx_reader.py`) call
`build_all(tmp_path)` and `build_all(tmp_path, language="de")`, so each
assertion refers to a document whose construction is visible in the script.

Rebuild them by hand (for Word, a reviewer, or the WP9–WP11 packages):

```sh
cd pypsa-gui/backend
python tests/fixtures/report_templates/_build.py /some/output/dir
```

which writes all four files and prints `name<TAB>path` per file.

## `tagged_minimal.docx` — the tagged (Jinja) shape

Body positions (each top-level paragraph or table is one index step):

| index | content |
|---|---|
| 0 | Title `{{ meta.title }}` |
| 1 | Heading 1 "Summary" |
| 2 | a Normal paragraph holding `{{ fields.executive_summary.text }}` written as **three runs** (`{{ fields.` / `executive_summary` / `.text }}`) — the way Word splits a tag the user edited piecemeal |
| 3 | a 2×4 "Table Grid" table: header row `Loop / Rank / Component / End`, second row `{% for row in tables.fmea_top.rows %}` / `{{ row[0] }}` / `{{ row[1] }}` / `{% endfor %}` |
| 4 | "Evidence hash in the footer." |

Footer: `Evidence {{ meta.evidence_hash }}`. No TOC, no header text.

Expected outline: `mode == "tagged"`, seven tags (`var`, `var`, `for`, `var`,
`var`, `endfor`, `var`; the footer tag has `paragraph_index == -1`),
`tables[0].index == 3`, `n_paragraphs == 4`, no placeholders.

## `corporate_untagged.docx` — a house template without tags

| index | content |
|---|---|
| 0 | Title `[Client name] — Energy Hub Reference Design` |
| 1 | "Prepared for [Client name]" |
| 2 | `<Date>` |
| 3 | a page break |
| 4 | a `w:fldSimple` TOC field (`TOC \o "1-3" \h \z \u`) |
| 5, 7, 9, 11 | Heading 1 "1 Executive Summary", "2 Introduction", "3 Availability Target", "4 Residual Failure Modes", each followed by a Normal paragraph ending in `XXX` |
| 13 | Heading 2 "4.1 Critical components" |
| 14 | a 2×3 "Table Grid" table, header row `Component / Failure mode / Criticality` |
| 15 | Heading 1 "5 Lorem ipsum" + a Normal paragraph |

Header "ACME Energy Consulting", footer "Confidential".

Expected outline: `mode == "untagged"`, `has_toc`, `body_start_index == 5`
(the first Heading 1 after the TOC), placeholders `[Client name]` (×2),
`<Date>`, `XXX` (×5), `language == "en"`, `n_paragraphs == 16`.

## `corporate_untagged_de.docx` — the same with German words

`build_all(dir, language="de")`. Cover "[Kundenname] — Referenzdesign Energy
Hub" / "Erstellt für [Kundenname]" / `<Datum>`, headings "1 Zusammenfassung",
"2 Einleitung", "3 Verfügbarkeitsziel", "4 Verbleibende Ausfallarten",
"5 Lorem ipsum", Heading 2 "4.1 Kritische Komponenten", table header
`Komponente / Ausfallart / Kritikalität`, footer "Vertraulich". Same positions
as the English file. Expected `language == "de"`.

## `with_textbox.docx` — the corporate template plus a text box

The English corporate document with one more paragraph (index 17) whose run
holds a VML text box (`w:pict/v:shape/v:textbox/w:txbxContent`). Expected:
`"text box"` in `unsupported`, everything else as the corporate file.
