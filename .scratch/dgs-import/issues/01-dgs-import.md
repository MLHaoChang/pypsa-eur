<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# PowerFactory DGS import into a new project

Status: done

Done in #1612 (9491878d); #1678 (c5368463); #1731 (c9c874cb). S1 parser, S2 mapping, S3 CSV/XLSX.
Type: task
Blocked by: none for S1; S2 waits for the import contract (`gridspine/ingest/import_result.py`, PR #1600)
Gated: **yes** (GK A2, all slices; coordinator 2026-11-17)
Owner decision: IMPORTS-1, IMPORT-FLOW-1, HOLISTIC-1, RAW-NAMES-1, PR-SIZE-1, GC-TEXT-1
Likely files: new `gridspine/ingest/dgs_parse.py`, new `gridspine/ingest/dgs_import.py`, new `tests/gridspine/test_dgs_parse.py`, `tests/gridspine/test_dgs_import.py`

## What to build

Read a PowerFactory DGS export (ASCII `.dgs` first, the CSV/XLSX variants if small) into the
shared `ImportResult`: terminals, lines and line types, 2- and 3-winding transformers and types,
loads, static generators, synchronous machines, shunts, external grids, switches (open/closed
topology), and harmonic sources into a sidecar. Anything unmappable goes to `report.unmapped`,
and every assumed default to `report.assumed`. Nothing is dropped silently.

Slices:

- **S1, parser** (`dgs_parse.py`): the ASCII grammar as typed tables; no engine import.
- **S2, mapping** (`dgs_import.py`): `read_dgs(paths, options) -> ImportResult` over pypsa.
- **S3**: the DGS CSV/XLSX variants, if small.

## Done when

- `gridspine/handoff/dgs_writer.py` output round-trips: counts per asset type are equal, and per-unit
  branch values match `net._ppc` within 1e-6.
- A hand-written fixture with hand-calculated per-unit values covers every mapped class.
- Each refusal names the file and the line.

## Comments

- 2026-11-17, GK A 28 (carried by GK A 29): S1 GO. The parser imports neither pypsa nor pandapower,
  and the cage test covers this. A version other than 5.x is refused, naming the version found.
  Each refusal reason gets one test that names the line. For S2: guard r > z in x = √(z² − r²)
  (refuse, never NaN); give 3-winding star values per unit on each winding's own base; a terminal
  merged through a switch keeps every original name in `dgs_names`.
- 2026-11-17, GK A2 plan verdict: GO for S1, S2 and S3, under these conditions.
  - S1 (D1): refusals name the file and the line, with one test per reason. A SIZEROW that does
    not match its element count is refused. An unknown type letter is refused. Numbers are read
    with no locale guessing.
  - D2: when `utrn_h`/`utrn_l` differs from the bus `uknom`, apply the off-nominal ratio (in
    `tap_ratio`, or by converting the impedance to the bus base) and record it in `assumed`.
    Tested with a transformer rated off the bus voltage.
  - D3: tap_ratio = 1 + dutap/100 · (nntap − nntap0) on the stated side, tested at an HV-side and
    an LV-side tap. Magnetising b = −√((curmg/100)² − g²) in pypsa's sign, pinned by a hand value;
    g > |y| is refused.
  - D4: for a 3-winding transformer, bring each pair's uk/pcu to a common base before the star
    conversion. Tested against a hand-calculated star with unequal ratings.
  - D5: ElmGenstat p_nom = sgn · cosn when cosn is given, else sgn with "MVA used as MW" in
    `assumed`. The slack's p_nom is inf if pypsa's power flow accepts it, else a stated value in
    `assumed`, tested not to change the power flow.
  - D6: if the installed pypsa does not honour `active` in pf/lpf, out-of-service rows go to a
    sidecar and stay out of the network, with a test.
  - D7: an independent oracle besides the writer round trip: a hand-written fixture with hand
    per-unit values for every mapped class, plus the `_ppc` comparison. Aliases are tested in both
    their current and corrected spellings, over a hand list.
  - D8: every mapped number is finite. A negative length or rating, or nlnum/ntnum < 1, is refused
    naming the item, and each guard is tested over a hand list of fields.
  - The S2 PR body says how `ImportResult` reaches the app (import flow, chat, how `assumed` and
    `unmapped` show). GC-TEXT-1 holds, PRs stay about 1,500 lines or under, and mutants run per module.
- 2026-11-17, writer follow-ups (GC-05 follow-ups, branch `claude/gc-15-16-dgs-follow-ups`): no
  attribute renames. One `$$TypHmccur` header covers the union of all sources' orders, and an order
  a source does not name is written as amplitude 0 and alpha 0, so S2 keeps only non-zero
  amplitudes. Line g is TypLne `gline(r)` in uS/km. Shunt P is not written, so S2 records it as an
  assumed 0.
- 2026-11-17, owner decision Q6 (via the coordinator): S3 accepts both DGS 7.0 ASCII and Excel.
  GK A2 (4) plan verdict: GO, on these conditions.
  - X1, 7.x references: pointer attributes (p:…; bus, typ_id, cubicle) hold FID values. Resolve
    them through the FID column and refuse a dangling FID by name. Test: a 7.0 file whose FIDs
    differ from its 5.0 twin's IDs gives the same network.
  - X2, 7.0 oracle: "same tables as the 5.0 twin" compares the parser with itself, so also assert
    literal values from the 7.0 file (a bus kV, a line length, a pointer target). The 7.0 fixture
    is hand-written, never produced by our writer (ruling 4).
  - X3, version gate: refuse 6.x and the rest by name. Pin with tests: 4.x, 6.x, 7.x without FID,
    and 5.x with FID, OP or MATRIX.
  - X4, Excel cell types, each handled explicitly: a formula cell is refused naming sheet, row and
    column (open with data_only=False, or refuse a missing cached value; never read it as empty);
    dates, booleans and error cells (#N/A) are refused by cell; exponent floats (1e-05) tested
    through parse_dgs_text; wholly empty trailing rows and columns ignored, a partly empty row
    keeps its empty cells; an extra non-DGS-class sheet is refused or unmapped (say which);
    hidden sheets are treated like any other (say so).
  - X5, safety: reuse the repo's safe xlsx opening (defusedxml, keep_links=False, read_only) and
    the other xlsx importers' size and row caps; no second, looser path. Run
    tests/test_packaging_requirements.py and test_openpyxl_parses_uploads_with_defusedxml* if
    imports change (the latter is a known container-only failure: report it, don't fix it).
  - X6, .xlsx dispatch: dgs, excel and plexos all take .xlsx, so the registry decides by content.
    A workbook with a DGS General sheet and typed headers is DGS. The app's own network workbook
    is never claimed by DGS, and a DGS workbook never by excel. Test both directions, plus an
    ambiguous workbook refused naming both candidates. The body says how an upload reaches the
    right importer (HOLISTIC).
  - X7, Excel oracle: the S2 hand fixture as .xlsx maps to the same network and report as the .dgs
    apart from locations; assert the "sheet <class>, row <n>" text in a refusal.
  - X8, disclosure: the PR body and release note say the xlsx layout is third-party-documented and
    untested against a real PowerFactory export.
  - X9, mutants: mutants.py on both modules; hand-mutate the OP≠"C" refusal, the 5.x FID refusal,
    the formula refusal, the integral-float formatting (5.0 → "5.0") and the dispatch sniff, each
    with its killing test. Release-note fragment. About 600 lines is within PR-SIZE-1.
- 2026-11-17, S3 build (session_01G7qCukUXA9jRyQCkZWGwRz), choices within X1-X9:
  - The one public PF 2025 7.0 export writes reals with a decimal comma ("158,7713"). A real
    with exactly one comma between digits and no point now reads as a point; "1.234,5", two
    commas, or a comma in an integer field stay refused. Put to GK A2 as a plan question.
  - 7.x: a header with an ID column, or without FID, is refused; General has no OP. A MATRIX
    column drops every `name:...` column of its name; each is listed unmapped once per class
    for mapped classes (other classes are already listed whole).
  - X4: a non-empty sheet that is not a class table is refused (row 1 is not typed headers); a
    wholly empty sheet is ignored; hidden sheets are read like any other.
  - X5: the Excel input workbook's zip, declared-size and row checks moved to
    `gridspine/ingest/xlsx_safe.py` (no pypsa), shared by both readers; `open_workbook` opens
    read-only, formulas kept, `keep_links=False`.
  - X6: `ImporterSpec.sniff` ("module:function") and `import_result.for_file(path)`: one
    importer for the extension wins outright; for `.xlsx` the one sniff that claims the file wins
    (dgs: a typed General sheet; excel: `xlsx_safe.is_excel_input_workbook`, a sheet only the
    app's workbooks use), else the one spec marked `fallback` (excel, so S1b's older layouts
    still route); two claims, or no claim and no single fallback, are refused.
- 2026-11-17, GK A2 (4): #1678 OK @9a9b4d7b. Added to S3:
  - X10: ElmLod, ElmGenstat and ElmSym P/Q set-points the file omits are listed as assumed with
    their default (0), never silently 0; one test per field.
  - X11: a text value in an int field (tap_side, nntap, nntap0) is an ImportFormatError naming
    the field, not a bare ValueError; tested. (Built for every int read in the mapper:
    obj_bus, outserv, on_off, ip_ctrl, shtype too; a fraction is refused the same way.)
  - D1a, amending D1: in r/d fields only, one comma between digits with no point reads as the
    decimal mark ("158,7713" → 158.7713). Still refused, naming line, class and attribute: a comma
    in an i field, mixed marks in one value ("1.234,5"), more than one comma, a leading or trailing
    comma (a text field keeps its comma as text). One file, one mark: a file with a comma real and
    a point real is refused naming the first of each; integers carry no mark. The report says
    "Decimal comma read as decimal mark" once per file. Fixtures are hand-written; the third-party
    file is neither committed nor named in the repo.
- 2026-11-19, GK A2 (5): NOT OK @f22e7b52, seven asks, all taken: (1) excel sniffs its known
  sheets and is the explicit `.xlsx` fallback, a workbook both claim is refused; (2) a damaged
  sheet is a format error in the sniff and the reader; (3) MATRIX columns listed for every class;
  (4) a dropped MATRIX column is never parsed or counted for the decimal mark; (5) a refusal test
  per X11 flag; (6) an understated `<dimension>` test; (7) the docstring says a saved 5.0 reads as 5.
  Also: a line break in a sheet name is refused like `;`.
