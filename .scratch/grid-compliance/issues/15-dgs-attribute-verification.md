<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-15 DGS attribute names and the harmonic-source block, checked against public sources

Status: ready-for-agent
Type: task
Parent: `05-powerfactory-dgs.md` (#1580 review follow-up)
Gated: **yes** (GK A2, gridspine)

## What

#1580 writes the DGS 5.x attribute names "pending verification on first import", and writes one
`$$TypHmccur` header before every harmonic-source row, because the vector length differs per row. Check
both against the format's reference.

## Evidence (public sources only, GC-TEXT-1)

The tool's own DGS manual and technical references are not publicly retrievable. The evidence below comes from
an open-source DGS parser and its test files, and from a third-party simulator's import documentation.

- Confirmed: General (ID, Descr, Val; Version 5.0); ElmNet loc_name, fold_id, frnom; ElmTerm uknom, outserv;
  StaCubic fold_id, obj_id, obj_bus; TypLne uline, sline, rline, xline, bline; ElmLne typ_id, dline, nlnum;
  ElmLod plini, qlini, scale0; ElmSym typ_id, pgini, usetp; ElmTr2 typ_id; ElmShnt shtype (1 = R-L, 2 = C).
- Vector syntax confirmed: `name:SIZEROW(i)`, then `name:k(...)`, with the count first in the row.
- Not found publicly: every TypTr2 attribute, ElmTr2 nntap and ntnum, ElmGenstat, TypSym, ElmXnet, ElmShnt
  ushnm/qtotn/ncapx/ncapa, phmc, and all of TypHmccur.
- Open doubt: a public test file has `inAir` on ElmLne. We write it on TypLne. That is not proof it is absent
  from TypLne, so it is unchanged.
- Repeated header blocks of one class: the open-source reader accepts them. No public file shows two blocks of
  one class, and the tool's own rule is unknown.

## Acceptance (GK A2 plan GO)

- No attribute-name change; the PENDING_VERIFICATION ledger stays.
- One `$$TypHmccur` block over the union of every source's orders. A source with no value at an order gets
  amplitude 0 and alpha 0 there, so every row has the same SIZEROW (no repeated header, no empty fields).
- Each source's non-zero (order, amplitude, alpha) set is unchanged by the merge (tested).
- One block per class in the whole file (tested by parsing the file back).

## Comments

- 2026-11-17, GK A2 plan verdict: GO, with the conditions above. Neither a repeated header nor padding (both
  rest on unverifiable reader behaviour). The DGS-import session is told of the layout.
