"""PSS/E .dyr writer — the handoff contract's dynamics half.

One record per synchronous machine, model class from the template::

    I 'GENROU' ID  T'do T''do T'qo T''qo H D Xd Xq X'd X'q X''d Xl S(1.0) S(1.2) /
    I 'GENSAL' ID  T'do T''do T''qo H D Xd Xq X'd X''d Xl S(1.0) S(1.2) /

Identity is the whole risk. A record attaches to a machine by (bus number,
machine ID), and both are authored by the RAW writer: bus numbers are
bus-table order, 1-based; machine IDs are a per-bus counter run over gen,
then ext_grid, then sgen. This module therefore does not reproduce that
convention — it CALLS ``raw_writer._bus_numbers`` and ``_IdCounter`` and walks
the machine tables in the same order, and
``tests/gridspine/test_dyr_writer.py`` parses both emitted files to prove the
(I, ID) sets agree. A .dyr whose numbering disagrees with its .raw imports
cleanly and attaches machines to the wrong buses, which is worse than no
.dyr at all.

Base: H and every reactance in a .dyr are on MBASE, the machine base the RAW
record declares (``sn_mva`` when finite, else the 100 MVA system base). The
template states its own base in ``mbase_mva``; a mismatch RAISES. Rescaling
H silently would produce a plausible, wrong swing curve, and the template is
the ledger — if the base is wrong, the ledger is wrong, and that is the thing
to fix.

Inverters (``model: inverter``) get NO record: no IBR dynamic model
(REGC/REEC) is in scope yet. They are omitted rather than handed a
synchronous record, and they are absent from the returned mapping so the
caller can ledger the omission. Legacy H-only units cannot be written either
— a GENROU built from H alone would be invented.

Nothing named reaches the file: records carry numbers and counter IDs only,
so the canonical-charset guard has no work to do here.

Record order follows the RAW's machine records (gen table, then ext_grid),
not bus-number order, so a reader can walk the two files side by side.
"""
import math

import pandas as pd

from gridspine.handoff.raw_writer import SBASE_MVA, _bus_numbers, _IdCounter
from gridspine.schema.contracts import ContractError

#: PSS/E v33 CON order per model, in template field names.
DYR_CONS = {
    "GENROU": (
        "t_do_p", "t_do_pp", "t_qo_p", "t_qo_pp", "h_s", "d",
        "xd", "xq", "xd_p", "xq_p", "xd_pp", "xl", "s1", "s12",
    ),
    "GENSAL": (
        "t_do_p", "t_do_pp", "t_qo_pp", "h_s", "d",
        "xd", "xq", "xd_p", "xd_pp", "xl", "s1", "s12",
    ),
}


def _machines(net):
    """Every machine as (unit_id, bus_number, machine_id, mbase, synchronous),
    in RAW record order, IDs from the RAW's own per-bus counter."""
    nums = _bus_numbers(net)
    name_of = net.bus["name"]
    ids = _IdCounter()
    out = []
    for _, g in net.gen.iterrows():
        sn = g.get("sn_mva", float("nan"))
        mbase = float(sn) if (sn is not None and math.isfinite(float(sn))) else SBASE_MVA
        num = nums[name_of.at[g["bus"]]]
        out.append((g["name"], num, ids.next(num), mbase, True))
    for _, e in net.ext_grid.iterrows():
        num = nums[name_of.at[e["bus"]]]
        out.append((e["name"], num, ids.next(num), SBASE_MVA, True))
    sgen = getattr(net, "sgen", None)
    if sgen is not None:
        for _, s in sgen.iterrows():
            # Counted so the IDs stay the RAW's; never written (see docstring).
            num = nums[name_of.at[s["bus"]]]
            out.append((s["name"], num, ids.next(num), float("nan"), False))
    return out


def write_dyr(net, unit_params: pd.DataFrame, path) -> dict:
    """Write the .dyr; return unit_id -> bus_number for every machine written."""
    lines, written = [], {}
    for unit_id, num, mid, mbase, synchronous in _machines(net):
        if not synchronous:
            continue
        if unit_id not in unit_params.index:
            raise ContractError(f"machine {unit_id} has no unit-parameter template row")
        row = unit_params.loc[unit_id]
        model = row.get("model")
        if model == "legacy":
            raise ContractError(
                f"unit {unit_id}: model 'legacy' carries H only and has no dynamic "
                "record; supply GENROU or GENSAL parameters"
            )
        if model not in DYR_CONS:
            raise ContractError(
                f"unit {unit_id}: no .dyr layout for model {model!r}; known {sorted(DYR_CONS)}"
            )
        template_base = float(row["mbase_mva"])
        if not math.isclose(template_base, mbase):
            raise ContractError(
                f"unit {unit_id}: template mbase_mva {template_base} does not match the "
                f"RAW MBASE {mbase}; H and reactances are on machine base and are "
                "not rescaled"
            )
        cons = []
        for name in DYR_CONS[model]:
            v = row.get(name)
            if v is None or pd.isna(v):
                raise ContractError(
                    f"unit {unit_id} ({model}): CON {name} is missing; a zero here is "
                    "a plausible wrong machine"
                )
            cons.append(float(v))
        lines.append(
            f"{num:>6d} '{model}' '{mid:<2s}' "
            + "".join(f"{c:10.5f}" for c in cons)
            + " /"
        )
        written[unit_id] = num

    with open(path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return written
