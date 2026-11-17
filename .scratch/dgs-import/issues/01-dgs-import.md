<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# PowerFactory DGS import into a new project

Status: claimed
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
