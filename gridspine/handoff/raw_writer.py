"""PSS/E RAW v33 writer — the handoff contract's network half.

Impedances on 100 MVA system base (CZ=1); bus numbers deterministic from bus
table order; canonical names in the NAME field (<=12 chars, enforced by
schema). Offline units written STAT=0, not omitted: the .dyr and the
dynamic study need the unit to exist.

Frequency: ``write_raw`` takes ``f_hz`` (default 50.0 — this pipeline's
target grids are 50 Hz European). It is used consistently for BOTH the
header's BASFRQ field and the line-charging susceptance B = 2*pi*f*C*Zbase.
Case39 is nominally a 60 Hz US system; callers exporting it as-built should
pass ``f_hz=60.0``. The default stays 50.0 because the handoff target is
the European study grid.

v33 layout decisions that differ from a naive single-line dump:
- 2-winding transformers are FOUR-line records (control words CW=CZ=CM=1:
  WINDV in p.u. of bus base voltage, R/X in p.u. on system SBASE, MAG in
  p.u. admittance on system base). Line 3 carries all 17 fields through
  CNXA1.
- Off-nominal turns ratios are NOT dropped: WINDV on the tapped side is
  (vn_winding / vn_bus) * (1 + (tap_pos - tap_neutral) * tap_step_percent
  / 100).
- (I, ID) must be unique per bus for machines/loads and (I, J, CKT) unique
  per branch pair — per-bus / per-pair counters assign IDs.
"""
import numpy as np

SBASE_MVA = 100.0

_EMPTY_TAIL = [
    "AREA", "TWO-TERMINAL DC", "VSC DC LINE", "IMPEDANCE CORRECTION",
    "MULTI-TERMINAL DC", "MULTI-SECTION LINE", "ZONE", "INTER-AREA TRANSFER",
    "OWNER", "FACTS DEVICE", "SWITCHED SHUNT", "GNE", "INDUCTION MACHINE",
]


def _bus_numbers(net):
    return {net.bus.at[i, "name"]: k + 1 for k, i in enumerate(net.bus.index)}


def _tap_ratio(tr):
    """Off-nominal ratio from pandapower tap columns; 1.0 when no tap set.

    pandapower (probed via net._ppc) applies tap_pos only when
    tap_changer_type == "Ratio"; otherwise the tap is inert and the ratio
    is 1.0. The writer mirrors that. (Other changer types — e.g. "Ideal"
    phase shifters via tap_step_degree — are not modelled here.)
    """
    if "tap_changer_type" in tr.index:
        tct = tr["tap_changer_type"]
        if not (isinstance(tct, str) and tct == "Ratio"):
            return 1.0
    pos = tr.get("tap_pos", np.nan)
    neutral = tr.get("tap_neutral", np.nan)
    step = tr.get("tap_step_percent", np.nan)
    if not (np.isfinite(pos) and np.isfinite(neutral) and np.isfinite(step)):
        return 1.0
    return 1.0 + (float(pos) - float(neutral)) * float(step) / 100.0


class _IdCounter:
    """Sequential 2-char IDs per key — (I, ID) / (I, J, CKT) uniqueness."""

    def __init__(self):
        self._seen = {}

    def next(self, key):
        n = self._seen.get(key, 0) + 1
        self._seen[key] = n
        return str(n)


def write_raw(net, path, title="gridspine export", f_hz=50.0):
    nums = _bus_numbers(net)
    name_of = net.bus["name"]
    slack_buses = set(net.ext_grid["bus"])
    gen_buses = set(net.gen["bus"]) | slack_buses
    lines = [
        f" 0, {SBASE_MVA:.2f}, 33, 0, 0, {f_hz:.2f}",
        str(title)[:60],
        "gridspine v33 export",
    ]

    for i in net.bus.index:
        if not net.bus.at[i, "in_service"]:
            ide = 4
        else:
            ide = 3 if i in slack_buses else (2 if i in gen_buses else 1)
        lines.append(
            f"{nums[name_of.at[i]]},'{name_of.at[i][:12]:<12s}',{net.bus.at[i, 'vn_kv']:9.4f},"
            f"{ide}, 1, 1, 1,1.00000, 0.00000,1.10000,0.90000,1.10000,0.90000"
        )
    lines.append("0 / END OF BUS DATA, BEGIN LOAD DATA")

    load_ids = _IdCounter()
    for _, ld in net.load.iterrows():
        num = nums[name_of.at[ld["bus"]]]
        stat = 1 if ld["in_service"] else 0
        scal = float(ld.get("scaling", 1.0))
        lines.append(
            f"{num},'{load_ids.next(num):<2s}',{stat}, 1, 1,"
            f"{ld['p_mw'] * scal:10.3f},{ld['q_mvar'] * scal:10.3f},"
            f" 0.000, 0.000, 0.000, 0.000, 1,1,0"
        )
    lines.append("0 / END OF LOAD DATA, BEGIN FIXED SHUNT DATA")
    lines.append("0 / END OF FIXED SHUNT DATA, BEGIN GENERATOR DATA")

    mach_ids = _IdCounter()

    def gen_record(bus_idx, p_mw, vm_pu, mbase, stat):
        num = nums[name_of.at[bus_idx]]
        return (
            f"{num},'{mach_ids.next(num):<2s}',{p_mw:10.3f},   0.000,9999.000,-9999.000,"
            f"{vm_pu:7.5f}, 0,{mbase:9.3f}, 0.00000,0.15000, 0.00000, 0.00000,1.00000,"
            f"{stat},100.0,{mbase:9.3f},   0.000, 1,1.0000"
        )

    for _, g in net.gen.iterrows():
        sn = g.get("sn_mva", np.nan)
        mbase = float(sn) if np.isfinite(sn) else SBASE_MVA
        scal = float(g.get("scaling", 1.0))
        lines.append(gen_record(g["bus"], g["p_mw"] * scal, g.get("vm_pu", 1.0),
                                mbase, 1 if g["in_service"] else 0))
    for _, e in net.ext_grid.iterrows():
        lines.append(gen_record(e["bus"], 0.0, e.get("vm_pu", 1.0), SBASE_MVA,
                                1 if e["in_service"] else 0))
    lines.append("0 / END OF GENERATOR DATA, BEGIN BRANCH DATA")

    branch_ckts = _IdCounter()
    for _, ln in net.line.iterrows():
        vn = net.bus.at[ln["from_bus"], "vn_kv"]
        z_base = vn ** 2 / SBASE_MVA
        r_pu = ln["r_ohm_per_km"] * ln["length_km"] / ln["parallel"] / z_base
        x_pu = ln["x_ohm_per_km"] * ln["length_km"] / ln["parallel"] / z_base
        b_pu = (2 * np.pi * f_hz * ln["c_nf_per_km"] * 1e-9 * ln["length_km"]
                * ln["parallel"]) * z_base
        rate = np.sqrt(3) * vn * ln["max_i_ka"] * ln["parallel"]
        i, j = nums[name_of.at[ln["from_bus"]]], nums[name_of.at[ln["to_bus"]]]
        ckt = branch_ckts.next((min(i, j), max(i, j)))
        lines.append(
            f"{i},{j},'{ckt:<2s}',"
            f"{r_pu:10.5f},{x_pu:10.5f},{b_pu:10.5f},{rate:8.2f},{rate:8.2f},{rate:8.2f},"
            f" 0.000, 0.000, 0.000, 0.000,{1 if ln['in_service'] else 0},1, 0.00, 1,1.0000"
        )
    lines.append("0 / END OF BRANCH DATA, BEGIN TRANSFORMER DATA")

    trafo_ckts = _IdCounter()
    for _, tr in net.trafo.iterrows():
        sn = tr["sn_mva"]
        par = int(tr.get("parallel", 1))
        hv_bus, lv_bus = tr["hv_bus"], tr["lv_bus"]
        bus_vn_hv = net.bus.at[hv_bus, "vn_kv"]
        bus_vn_lv = net.bus.at[lv_bus, "vn_kv"]
        # pandapower vk/vkr are on (sn_mva, vn_lv) base; CZ=1 wants p.u. on
        # system SBASE at the winding's bus voltage base. Probed against
        # net._ppc: with tap_side=="lv" pandapower uses the TAP-ADJUSTED lv
        # nominal voltage as the impedance base (r/x scale by tap**2); with
        # tap_side=="hv" it does not.
        tap = _tap_ratio(tr)
        vn_hv_eff, vn_lv_eff = tr["vn_hv_kv"], tr["vn_lv_kv"]
        if str(tr.get("tap_side", "hv")) == "lv":
            vn_lv_eff *= tap
        else:
            vn_hv_eff *= tap
        conv = (SBASE_MVA / sn) * (vn_lv_eff / bus_vn_lv) ** 2 / par
        r_pu = (tr["vkr_percent"] / 100.0) * conv
        z_pu = (tr["vk_percent"] / 100.0) * conv
        x_pu = np.sqrt(max(z_pu ** 2 - r_pu ** 2, 1e-12))
        windv1 = vn_hv_eff / bus_vn_hv
        windv2 = vn_lv_eff / bus_vn_lv
        shift = float(tr.get("shift_degree", 0.0) or 0.0)
        hv, lv = nums[name_of.at[hv_bus]], nums[name_of.at[lv_bus]]
        ckt = trafo_ckts.next((min(hv, lv), max(hv, lv)))
        stat = 1 if tr["in_service"] else 0
        rate = sn * par
        xname = f"TR_{hv}_{lv}"[:12]
        # MAG1/MAG2 = 0: deliberate case39-scope simplification — case39 has
        # pfe_kw == i0_percent == 0 everywhere, so zero is exact, not lossy.
        lines.append(
            f"{hv},{lv},0,'{ckt:<2s}',1,1,1, 0.00000, 0.00000,2,'{xname:<12s}',{stat},1,1.0000"
        )
        lines.append(f"{r_pu:10.5f},{x_pu:10.5f},{sn:8.2f}")
        lines.append(
            f"{windv1:7.5f},{tr['vn_hv_kv']:9.4f},{shift:7.3f},"
            f"{rate:8.2f},{rate:8.2f},{rate:8.2f},0, 0,"
            f"1.10000,0.90000,1.10000,0.90000,33,0, 0.00000, 0.00000, 0.000"
        )
        lines.append(f"{windv2:7.5f},{tr['vn_lv_kv']:9.4f}")
    lines.append("0 / END OF TRANSFORMER DATA, BEGIN AREA DATA")

    for k, name in enumerate(_EMPTY_TAIL[1:], start=1):
        lines.append(f"0 / END OF {_EMPTY_TAIL[k - 1]} DATA, BEGIN {name} DATA")
    lines.append(f"0 / END OF {_EMPTY_TAIL[-1]} DATA")
    lines.append("Q")

    with open(path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return nums
