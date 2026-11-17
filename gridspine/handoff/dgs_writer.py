"""
PowerFactory DGS writer — the network, the plant and its harmonic sources
for the power-quality (TRGC 13/14) and small-signal (TRGC 18) studies that
run in PowerFactory.

Format: ASCII ``.dgs`` (DGS 5.x): one ``$$Class;ID(a:40);attr(type);...``
header per element class, then ``;``-separated rows. Every object carries a
numeric ``ID`` unique in the file; ``(p)`` attributes point at another ID.
Connections are cubicles (``StaCubic``: ``fold_id`` the terminal,
``obj_id`` the element, ``obj_bus`` its side, 0 or 1), as PowerFactory
stores them.

What is written, from the pandapower net:

- ``ElmTerm`` per bus (``uknom`` kV); ``ElmXnet`` per external grid with
  BOTH short-circuit cases (``snss``/``snssmin`` MVA, ``rntxn``/``rntxnmin``
  R/X), so the harmonic and strength studies can run at S_k min and max;
- ``TypLne`` + ``ElmLne`` per line or cable (Ohm/km, uS/km, km, parallel
  count; ``inAir`` 0 for a cable when the net marks it ``type == "cs"``);
- ``TypTr2`` + ``ElmTr2`` per 2-winding transformer (rating, voltages,
  ``uktr``/``pcutr``, no-load losses, tap step, neutral, limits, side and
  the position in service);
- ``ElmLod`` per load, ``ElmGenstat`` per static generator (converters,
  STATCOMs), ``TypSym`` + ``ElmSym`` per synchronous machine, ``ElmShnt``
  per shunt (capacitor bank, reactor or filter by the sign of Q);
- ``TypHmccur`` per harmonic spectrum, pointed at by its element's ``phmc``:
  orders, magnitude in % of the unit's rated current and the IEC 61000-3-6
  summation exponent per order (``harmonics.alpha``), as vectors.

Names: every DGS ``loc_name`` is the pandapower name, with ``;`` (the field
separator) and control characters replaced, cut to 40 characters and made
unique within its class by a ``~n`` suffix. The ``names`` frame the writer
returns maps each element both ways; written as ``dgs_names.csv`` next to
the ``.dgs``, it is what ``readback.results.read_back`` uses to key a
PowerFactory export of this network back onto the pandapower load flow.

LEDGER (pending verification on the first PowerFactory import): the
attribute names are the documented DGS ones as far as public material
gives them; ``ATTRIBUTES`` lists every one in one place so a correction is
one edit. Values are SI/engineering units as PowerFactory's input dialogs
show them, not per unit.
"""

from __future__ import annotations

import dataclasses
import math
import re

import pandas as pd

from gridspine.handoff.raw_writer import sgen_mbase
from gridspine.schema.contracts import ContractError
from gridspine.static.harmonics import alpha, parse_spectrum

DGS_VERSION = "5.0"
NAME_LEN = 40
NAME_COLUMNS = (
    "pp_table",
    "pp_index",
    "pp_name",
    "dgs_class",
    "dgs_name",
    "from_bus",
    "to_bus",
    "ckt",
)
HARMONIC_COLUMNS = (
    "pp_table",
    "pp_name",
    "dgs_name",
    "status",
    "spectrum_source",
    "orders",
)
NO_SPECTRUM = "no spectrum"
WITH_SPECTRUM = "spectrum"
PENDING_VERIFICATION = "DGS attribute names: confirm on the first PowerFactory import"

#: Every class written and its attributes in column order, with the DGS
#: type letter: a (text), i (integer), r (real), p (pointer to an ID).
ATTRIBUTES = {
    "General": [("Descr", "a:40"), ("Val", "a:40")],
    "ElmNet": [("loc_name", "a:40"), ("frnom", "r")],
    "ElmTerm": [
        ("loc_name", "a:40"),
        ("fold_id", "p"),
        ("uknom", "r"),
        ("outserv", "i"),
    ],
    "TypLne": [
        ("loc_name", "a:40"),
        ("uline", "r"),
        ("sline", "r"),
        ("rline", "r"),
        ("xline", "r"),
        ("bline", "r"),
        ("inAir", "i"),
        ("frnom", "r"),
    ],
    "ElmLne": [
        ("loc_name", "a:40"),
        ("fold_id", "p"),
        ("typ_id", "p"),
        ("dline", "r"),
        ("nlnum", "i"),
        ("outserv", "i"),
    ],
    "TypTr2": [
        ("loc_name", "a:40"),
        ("strn", "r"),
        ("utrn_h", "r"),
        ("utrn_l", "r"),
        ("uktr", "r"),
        ("pcutr", "r"),
        ("pfe", "r"),
        ("curmg", "r"),
        ("nt2ag", "r"),
        ("dutap", "r"),
        ("nntap0", "i"),
        ("ntpmn", "i"),
        ("ntpmx", "i"),
        ("tap_side", "i"),
        ("frnom", "r"),
    ],
    "ElmTr2": [
        ("loc_name", "a:40"),
        ("fold_id", "p"),
        ("typ_id", "p"),
        ("nntap", "i"),
        ("ntnum", "i"),
        ("outserv", "i"),
    ],
    "ElmLod": [
        ("loc_name", "a:40"),
        ("fold_id", "p"),
        ("plini", "r"),
        ("qlini", "r"),
        ("scale0", "r"),
        ("phmc", "p"),
        ("outserv", "i"),
    ],
    "ElmGenstat": [
        ("loc_name", "a:40"),
        ("fold_id", "p"),
        ("sgn", "r"),
        ("pgini", "r"),
        ("qgini", "r"),
        ("scale0", "r"),
        ("phmc", "p"),
        ("outserv", "i"),
    ],
    "TypSym": [("loc_name", "a:40"), ("sgn", "r"), ("ugn", "r")],
    "ElmSym": [
        ("loc_name", "a:40"),
        ("fold_id", "p"),
        ("typ_id", "p"),
        ("pgini", "r"),
        ("usetp", "r"),
        ("scale0", "r"),
        ("outserv", "i"),
    ],
    "ElmShnt": [
        ("loc_name", "a:40"),
        ("fold_id", "p"),
        ("shtype", "i"),
        ("ushnm", "r"),
        ("qtotn", "r"),
        ("ncapx", "i"),
        ("ncapa", "i"),
        ("outserv", "i"),
    ],
    "ElmXnet": [
        ("loc_name", "a:40"),
        ("fold_id", "p"),
        ("usetp", "r"),
        ("snss", "r"),
        ("snssmin", "r"),
        ("rntxn", "r"),
        ("rntxnmin", "r"),
        ("bustp", "a:2"),
        ("outserv", "i"),
    ],
    "StaCubic": [
        ("loc_name", "a:40"),
        ("fold_id", "p"),
        ("obj_id", "p"),
        ("obj_bus", "i"),
    ],
}
#: ``TypHmccur`` is written separately: its vectors' length varies per row.
HARMONIC_CLASS = "TypHmccur"
#: ``ElmShnt.shtype``: 2 = C (capacitive), 1 = R-L (inductive).
SHTYPE_C, SHTYPE_L = 2, 1


@dataclasses.dataclass(frozen=True)
class DgsExport:
    """
    ``names``: one row per written element (``NAME_COLUMNS``; branches
    carry the load flow's ``(from_bus, to_bus, ckt)`` key). ``harmonic_sources``:
    one row per element that should carry a spectrum (``HARMONIC_COLUMNS``),
    ``status`` ``spectrum`` or ``no spectrum`` — a converter without one is
    written as a plain element and listed here, never skipped silently.
    """

    names: pd.DataFrame
    harmonic_sources: pd.DataFrame


_UNSAFE = re.compile(r"[;\x00-\x1f\x7f]")


class _Names:
    """Unique, DGS-safe ``loc_name`` per class."""

    def __init__(self):
        self._used: dict = {}

    def __call__(self, cls: str, name) -> str:
        base = _UNSAFE.sub("_", str(name)).strip() or cls
        used = self._used.setdefault(cls, set())
        out, n = base[:NAME_LEN], 1
        while out in used:
            n += 1
            suffix = f"~{n}"
            out = base[: NAME_LEN - len(suffix)] + suffix
        used.add(out)
        return out


def _fmt(v, kind: str) -> str:
    if kind == "p":
        return "" if v is None else str(int(v))
    if kind == "i":
        return str(int(v))
    if kind == "r":
        v = float(v)
        if not math.isfinite(v):
            raise ContractError(f"DGS: a real attribute is not finite ({v})")
        return repr(v)
    return str(v)


def _val(row, col, default):
    v = row.get(col, default) if hasattr(row, "get") else default
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return default
    except TypeError:
        return default
    return v


def _out(row) -> int:
    return 0 if bool(_val(row, "in_service", True)) else 1


def write_dgs(
    net,
    path,
    *,
    title: str = "gridspine export",
    f_hz: float | None = None,
    harmonics: dict | None = None,
    converters=(),
) -> DgsExport:
    """
    Write ``net`` to ``path`` as an ASCII DGS file.

    ``harmonics``: ``{pp element name: {"spectrum": {order: % of rated current}
    or "5: 20, 7: 14", "source": text}}`` for loads and static generators;
    ``converters``: the further element names that should carry a spectrum —
    one that has none is written without and listed with status ``no
    spectrum``. A name in ``harmonics`` that matches no load or static
    generator is refused, as is a spectrum with an invalid order.
    """
    f_hz = float(net.f_hz if f_hz is None else f_hz)
    harmonics = {str(k): v for k, v in (harmonics or {}).items()}
    converters = {str(c) for c in converters}
    holders = {str(n) for n in net.load["name"]} | (
        {str(n) for n in net.sgen["name"]} if "sgen" in net else set()
    )
    unknown = sorted(set(harmonics) - holders)
    if unknown:
        raise ContractError(
            f"DGS: harmonic spectrum for {unknown}, which is no load or static generator"
        )

    rows: dict = {cls: [] for cls in [*ATTRIBUTES, HARMONIC_CLASS]}
    names, hsources = [], []
    ids = iter(range(1, 10**9))
    nm = _Names()

    rows["General"].append((next(ids), "Version", DGS_VERSION))
    rows["General"].append((next(ids), "Title", nm("General", title)))
    grid = next(ids)
    rows["ElmNet"].append((grid, nm("ElmNet", net.name or "Grid"), f_hz))

    term = {}
    for i, b in net.bus.iterrows():
        tid = next(ids)
        term[i] = tid
        dname = nm("ElmTerm", b["name"])
        rows["ElmTerm"].append((tid, dname, grid, float(b["vn_kv"]), _out(b)))
        names.append(("bus", i, str(b["name"]), "ElmTerm", dname, None, None, None))

    def cubic(elm_id, bus_idx, side, label):
        rows["StaCubic"].append(
            (next(ids), nm("StaCubic", f"{label}_{side}"), term[bus_idx], elm_id, side)
        )

    keys = _branch_keys(net)

    for i, ln in net.line.iterrows():
        typ, eid = next(ids), next(ids)
        dname = nm("ElmLne", ln["name"] if _val(ln, "name", None) else f"line_{i}")
        vn = float(net.bus.at[ln["from_bus"], "vn_kv"])
        b_us = 2 * math.pi * f_hz * float(ln["c_nf_per_km"]) * 1e-3  # nF/km -> uS/km
        cable = str(_val(ln, "type", "ol")) == "cs"
        rows["TypLne"].append(
            (
                typ,
                nm("TypLne", f"{dname}_type"),
                vn,
                float(ln["max_i_ka"]),
                float(ln["r_ohm_per_km"]),
                float(ln["x_ohm_per_km"]),
                b_us,
                0 if cable else 1,
                f_hz,
            )
        )
        rows["ElmLne"].append(
            (
                eid,
                dname,
                grid,
                typ,
                float(ln["length_km"]),
                int(_val(ln, "parallel", 1)),
                _out(ln),
            )
        )
        cubic(eid, ln["from_bus"], 0, dname)
        cubic(eid, ln["to_bus"], 1, dname)
        k = keys.get(("line", i), (None, None, None))
        names.append(("line", i, str(_val(ln, "name", "")), "ElmLne", dname, *k))

    for i, tr in net.trafo.iterrows():
        typ, eid = next(ids), next(ids)
        dname = nm("ElmTr2", tr["name"] if _val(tr, "name", None) else f"trafo_{i}")
        sn = float(tr["sn_mva"])
        ratio = str(_val(tr, "tap_changer_type", "Ratio")) == "Ratio"
        step = float(_val(tr, "tap_step_percent", 0.0)) if ratio else 0.0
        neutral = int(_val(tr, "tap_neutral", 0))
        pos = int(_val(tr, "tap_pos", neutral)) if ratio else neutral
        rows["TypTr2"].append(
            (
                typ,
                nm("TypTr2", f"{dname}_type"),
                sn,
                float(tr["vn_hv_kv"]),
                float(tr["vn_lv_kv"]),
                float(tr["vk_percent"]),
                float(tr["vkr_percent"]) / 100.0 * sn * 1000.0,  # kW
                float(_val(tr, "pfe_kw", 0.0)),
                float(_val(tr, "i0_percent", 0.0)),
                float(_val(tr, "shift_degree", 0.0)) / 30.0,
                step,
                neutral,
                int(_val(tr, "tap_min", neutral)),
                int(_val(tr, "tap_max", neutral)),
                1 if str(_val(tr, "tap_side", "hv")) == "lv" else 0,
                f_hz,
            )
        )
        rows["ElmTr2"].append(
            (eid, dname, grid, typ, pos, int(_val(tr, "parallel", 1)), _out(tr))
        )
        cubic(eid, tr["hv_bus"], 0, dname)
        cubic(eid, tr["lv_bus"], 1, dname)
        k = keys.get(("trafo", i), (None, None, None))
        names.append(("trafo", i, str(_val(tr, "name", "")), "ElmTr2", dname, *k))

    def harmonic(table, pp_name, dname):
        """The TypHmccur ID for an element, or None; records the row."""
        spec = harmonics.get(pp_name)
        if spec is None:
            if pp_name in converters:
                hsources.append((table, pp_name, dname, NO_SPECTRUM, "", ""))
            return None
        spectrum = parse_spectrum(spec.get("spectrum"))
        if not spectrum:
            hsources.append(
                (table, pp_name, dname, NO_SPECTRUM, str(spec.get("source", "")), "")
            )
            return None
        hid = next(ids)
        orders = sorted(spectrum)
        rows[HARMONIC_CLASS].append(
            (
                hid,
                nm(HARMONIC_CLASS, f"{dname}_spectrum"),
                orders,
                [spectrum[h] for h in orders],
                [alpha(h) for h in orders],
            )
        )
        hsources.append(
            (
                table,
                pp_name,
                dname,
                WITH_SPECTRUM,
                str(spec.get("source", "")),
                ", ".join(f"{h}: {spectrum[h]:g}" for h in orders),
            )
        )
        return hid

    for i, ld in net.load.iterrows():
        eid = next(ids)
        dname = nm("ElmLod", ld["name"] if _val(ld, "name", None) else f"load_{i}")
        hid = harmonic("load", str(ld["name"]), dname)
        rows["ElmLod"].append(
            (
                eid,
                dname,
                grid,
                float(ld["p_mw"]),
                float(ld["q_mvar"]),
                float(_val(ld, "scaling", 1.0)),
                hid,
                _out(ld),
            )
        )
        cubic(eid, ld["bus"], 0, dname)
        names.append(("load", i, str(ld["name"]), "ElmLod", dname, None, None, None))

    if "sgen" in net:
        for i, s in net.sgen.iterrows():
            eid = next(ids)
            dname = nm(
                "ElmGenstat", s["name"] if _val(s, "name", None) else f"sgen_{i}"
            )
            hid = harmonic("sgen", str(s["name"]), dname)
            sn = sgen_mbase(s)  # the same rating the RAW's MBASE states
            rows["ElmGenstat"].append(
                (
                    eid,
                    dname,
                    grid,
                    sn,
                    float(s["p_mw"]),
                    float(_val(s, "q_mvar", 0.0)),
                    float(_val(s, "scaling", 1.0)),
                    hid,
                    _out(s),
                )
            )
            cubic(eid, s["bus"], 0, dname)
            names.append(
                ("sgen", i, str(s["name"]), "ElmGenstat", dname, None, None, None)
            )

    for i, g in net.gen.iterrows():
        typ, eid = next(ids), next(ids)
        dname = nm("ElmSym", g["name"] if _val(g, "name", None) else f"gen_{i}")
        sn = float(_val(g, "sn_mva", 0.0)) or abs(float(g["p_mw"]))
        rows["TypSym"].append(
            (
                typ,
                nm("TypSym", f"{dname}_type"),
                sn,
                float(net.bus.at[g["bus"], "vn_kv"]),
            )
        )
        rows["ElmSym"].append(
            (
                eid,
                dname,
                grid,
                typ,
                float(g["p_mw"]),
                float(_val(g, "vm_pu", 1.0)),
                float(_val(g, "scaling", 1.0)),
                _out(g),
            )
        )
        cubic(eid, g["bus"], 0, dname)
        names.append(
            ("gen", i, str(_val(g, "name", "")), "ElmSym", dname, None, None, None)
        )

    for i, sh in net.shunt.iterrows():
        eid = next(ids)
        dname = nm("ElmShnt", sh["name"] if _val(sh, "name", None) else f"shunt_{i}")
        q = float(sh["q_mvar"])  # pandapower load convention: negative is capacitive
        max_step = int(_val(sh, "max_step", 1))
        rows["ElmShnt"].append(
            (
                eid,
                dname,
                grid,
                SHTYPE_C if q < 0 else SHTYPE_L,
                float(_val(sh, "vn_kv", net.bus.at[sh["bus"], "vn_kv"])),
                abs(q) * max_step,
                max_step,
                int(_val(sh, "step", 1)),
                _out(sh),
            )
        )
        cubic(eid, sh["bus"], 0, dname)
        names.append(
            ("shunt", i, str(_val(sh, "name", "")), "ElmShnt", dname, None, None, None)
        )

    for i, e in net.ext_grid.iterrows():
        eid = next(ids)
        dname = nm("ElmXnet", e["name"] if _val(e, "name", None) else f"ext_grid_{i}")
        sk_max = _val(e, "s_sc_max_mva", None)
        sk_min = _val(e, "s_sc_min_mva", None)
        if sk_max is None or sk_min is None:
            raise ContractError(
                f"DGS: external grid {dname!r} needs s_sc_max_mva and s_sc_min_mva "
                "(the harmonic and strength studies run at both)"
            )
        rows["ElmXnet"].append(
            (
                eid,
                dname,
                grid,
                float(_val(e, "vm_pu", 1.0)),
                float(sk_max),
                float(sk_min),
                float(_val(e, "rx_max", 0.1)),
                float(_val(e, "rx_min", _val(e, "rx_max", 0.1))),
                "SL",
                _out(e),
            )
        )
        cubic(eid, e["bus"], 0, dname)
        names.append(
            (
                "ext_grid",
                i,
                str(_val(e, "name", "")),
                "ElmXnet",
                dname,
                None,
                None,
                None,
            )
        )

    lines = []
    for cls, attrs in ATTRIBUTES.items():
        if not rows[cls]:
            continue
        lines.append(
            ";".join([f"$${cls}", "ID(a:40)", *(f"{a}({t})" for a, t in attrs)])
        )
        for r in rows[cls]:
            lines.append(
                ";".join(
                    [str(r[0]), *(_fmt(v, t[0]) for v, (_, t) in zip(r[1:], attrs))]
                )
            )
    for r in rows[HARMONIC_CLASS]:
        hid, hname, orders, mags, alphas = r
        n = len(orders)
        head = [
            "$$" + HARMONIC_CLASS,
            "ID(a:40)",
            "loc_name(a:40)",
            "norder:SIZEROW(i)",
            *(f"norder:{k}(i)" for k in range(n)),
            "ampl:SIZEROW(i)",
            *(f"ampl:{k}(r)" for k in range(n)),
            "alpha:SIZEROW(i)",
            *(f"alpha:{k}(r)" for k in range(n)),
        ]
        lines.append(";".join(head))
        lines.append(
            ";".join(
                [
                    str(hid),
                    hname,
                    str(n),
                    *map(str, orders),
                    str(n),
                    *(repr(float(m)) for m in mags),
                    str(n),
                    *(repr(float(a)) for a in alphas),
                ]
            )
        )
    with open(path, "w", newline="\n", encoding="utf-8") as f:
        f.write("".join(f"{ln}\n" for ln in lines))

    return DgsExport(
        names=pd.DataFrame(names, columns=list(NAME_COLUMNS)),
        harmonic_sources=pd.DataFrame(hsources, columns=list(HARMONIC_COLUMNS)),
    )


def _branch_keys(net) -> dict:
    """
    ``{(table, index): (from_bus, to_bus, ckt)}`` — the load flow's branch
    identity (``static.loadflow.branch_keys``), so a PowerFactory branch
    export keyed by DGS name joins onto ``lf_branch_flow.csv``.
    """
    from gridspine.static.loadflow import branch_keys

    keys = branch_keys(net)
    return {
        (r.element_type, r.element_index): (r.from_bus, r.to_bus, r.ckt)
        for r in keys.itertuples()
    }
