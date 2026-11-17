"""
psspy run scripts for the test battery: one script per case (GC-04 item 6).

Each ``run_<case_id>.py`` loads the pack's RAW, ``.seq`` and ``.dyr``, turns
the grid source machine (a GENCLS with H = 0, an infinite bus) into the case's
Thevenin source, applies the case's event at ``t_event_s`` and writes
``<case_id>.out`` plus ``<case_id>.csv`` next to itself (TRGC 04/05).

**Precondition.** The RAW already holds the SMIB: the grid is one GENCLS
machine (``grid_machine``) at the PoC side, whose impedance the script sets
(Z1, and Z0 when given). The script adds no machine, bus or branch.

**Channels.** PSS/E records ``raw_<name>`` channels as it measures them; after
the run the script reads the ``.out`` back with ``dyntools.CHNF.get_data``,
applies :data:`CONVERSIONS` in a pure ``_convert`` function and writes the CSV
with the header ``t_s`` plus the case's channels by their contract names, in
contract units (``test_battery.CHANNELS``). So the read-back never sees a PSS/E
unit. id/iq come from the plant converter's current commands, pu on the plant
machine's MBASE: the pack sets MBASE = plant rated apparent power, which is
the contract base. P and Q are the PoC branch flow measured at its from bus
(the plant side) towards the grid: export, i.e. injection, is positive.

**Thevenin source.** ``thevenin_impedances_ohm`` gives Z1 (and Z0) in ohms at
the PoC base kV; the generator converts them to pu on the grid machine's MBASE
(``Z_pu = Z_ohm * MBASE / kV**2``) and writes the numbers as literals. The
script refuses to run when the RAW machine's MBASE differs from the one the
numbers were made on. Z2 = Z1 (test_battery module docstring).

**Pack-local files.** A script names files only by basename and resolves each
against its own directory; :func:`check_script` (also used by the pack) parses
a script with ``ast`` and refuses any other file, an absolute path, an import
outside psspy/dyntools/os, ``open``/``eval``/``exec``/``__import__``, and
any ``os`` call outside ``os.path`` (GK condition S1). The one ``open`` allowed
is ``open(os.path.join(HERE, '<file>.csv'), 'w')`` on a pack file, with
``HERE`` bound once to the script's own directory.

**Solved start.** The RAW is written flat (VM 1, VA 0). After setting the
source impedance each script solves the power flow (``fnsl`` with
:data:`FNSL_OPTIONS`) and refuses to continue (``SystemExit``) when
``psspy.solved()`` is not 0, so dynamic initialisation (``cong`` onwards)
never starts from an unsolved case. :func:`check_script` refuses a script
without ``fnsl``, then that convergence check, then ``cong``, in that order.

**Current magnitude at V = 0.** ``i_poc_pu`` is ``|S| / (V * S_rated)``,
undefined when the PoC voltage is 0 (a bolted fault at the PoC). The result
is a CSV of finite numbers with a fixed contract header and no slot for a
"not evaluated" sample, so ``_convert`` refuses: it raises ``ValueError``
naming the channel and the reason, and no CSV is written for that case (the
``.out`` stays). It never writes NaN or inf.

The psspy calls were written without a PSS/E licence: :data:`PENDING` is
repeated in every script, :data:`EVENT_CALLS` / :data:`CHANNEL_CALLS`
state per kind and channel which calls are used and what is unverified, and
:data:`UNVERIFIED` lists the remaining unverified calls and conventions.
"""

import ast
import math
import os
import re

from gridspine.handoff.test_battery import (
    fault_impedance_ohm,
    thevenin_impedances_ohm,
    validate_battery,
)
from gridspine.schema.contracts import ContractError

PENDING = (
    "psspy calls generated without a PSS/E licence; pending verification on "
    "first PSS/E load"
)

#: fnsl options: taps locked, area interchange off, phase shifters locked, dc
#: taps locked, switched shunts locked, no flat start (the RAW values are
#: already flat), var limits applied at once, non-divergent solution on.
FNSL_OPTIONS = (0, 0, 0, 0, 0, 0, 0, 1)
#: GENCLS angle state = STATE K + this offset, K from ``mdlind(..., 'STATE')``.
ANGLE_STATE_OFFSET = 1

#: call or convention -> what is assumed and unverified
UNVERIFIED = {
    "GENCLS angle state": (
        "mdlind(GRID_BUS, GRID_ID, 'GEN', 'STATE') gives K, the first GENCLS "
        "state; the rotor angle is assumed STATE K+1 with K the speed deviation "
        "(the speed-then-angle order of the larger machine models); no public "
        "source found"
    ),
    "fnsl": "option order and meaning of FNSL_OPTIONS",
    "solved": "psspy.solved() returns 0 when the last solution converged",
}

#: kind -> (psspy calls used, note)
EVENT_CALLS = {
    "flat": ((), "no event; checks initialisation"),
    "fault_3ph": (
        ("dist_bus_fault", "dist_clear_fault"),
        "units=2 (ohms, at POC_KV) for Z_f > 0; a bolted fault uses units=1 "
        "with the documented default [0, -2e11] MVA",
    ),
    "fault_1ph": (
        ("dist_scmu_fault_2", "dist_clear_fault", "sysmva"),
        "intgar layout [UNBAL=1 (L-G), FLTCON=1, BUS, 0, 0] and Z_f in pu on "
        "SBASE are unverified",
    ),
    "v_step": (("increment_vref",), "VREF of the plant machine's controller"),
    "q_step": (
        ("increment_vref",),
        "assumes the plant controller in reactive-power mode, where VREF holds "
        "Qref; unverified",
    ),
    "p_step": (("increment_gref",), "GREF of the plant machine; base assumed MBASE"),
    "f_step": (
        ("mdlind", "dsrval", "change_state", "run"),
        "source angle advanced by 2*pi*df*t every 5 ms (H = 0 machine holds "
        "its angle); dsrval('STATE', K) return form unverified",
    ),
    "f_ramp": (
        ("mdlind", "dsrval", "change_state", "run"),
        "as f_step with the angle integral of the ramp, then constant df",
    ),
    "phase_jump": (
        ("mdlind", "dsrval", "change_state"),
        "one step of the GENCLS angle state; dsrval return form unverified",
    ),
}

#: recorded PSS/E channel -> (psspy call, note)
CHANNEL_CALLS = {
    "raw_v_poc_pu": ("voltage_channel", "pu of bus base kV (= POC_KV)"),
    "raw_theta_poc_deg": ("voltage_and_angle_channel", "absolute bus angle, deg"),
    "raw_theta_src": ("machine_array_channel", "code 1 ANGLE of the grid GENCLS, deg"),
    "raw_f_hz": ("bus_frequency_channel", "pu deviation from base frequency"),
    "raw_p_poc_mw": ("branch_p_and_q_channel", "MW at the from (plant) bus"),
    "raw_q_poc_mvar": ("branch_p_and_q_channel", "Mvar at the from (plant) bus"),
    "raw_id_pu": (
        "machine_array_channel",
        "code 22, Ip command, pu on MBASE = plant rated",
    ),
    "raw_iq_pu": (
        "machine_array_channel",
        "code 23, Iq command, pu on MBASE = plant rated; positive = capacitive "
        "injection assumed (REEC_A convention), unverified",
    ),
}

#: contract channel -> (recorded inputs a, b, c..., per-sample expression, formula)
CONVERSIONS = {
    "v_poc_pu": (("raw_v_poc_pu",), "a", "v_poc_pu = V"),
    "theta_poc_deg": (
        ("raw_theta_poc_deg", "raw_theta_src"),
        "a - b",
        "theta = bus angle - source EMF angle",
    ),
    "f_hz": (("raw_f_hz",), "f0_hz * (1.0 + a)", "f = f0 * (1 + dev_pu)"),
    "p_poc_mw": (("raw_p_poc_mw",), "a", "P plant -> grid, export positive"),
    "q_poc_mvar": (("raw_q_poc_mvar",), "a", "Q plant -> grid, injection positive"),
    "id_pu": (("raw_id_pu",), "a", "Ip command on MBASE = plant rated"),
    "iq_pu": (("raw_iq_pu",), "a", "Iq command on MBASE = plant rated"),
    "i_poc_pu": (
        ("raw_p_poc_mw", "raw_q_poc_mvar", "raw_v_poc_pu"),
        "(a * a + b * b) ** 0.5 / (c * s_rated) if c > 1e-6 else "
        "_refuse('i_poc_pu: V = 0 at the PoC (bolted fault there), current from "
        "P, Q and V undefined')",
        "|I| = sqrt(P^2 + Q^2) / (V * S_rated), S_rated = plant MBASE; refused at V = 0",
    ),
}

ALLOWED_IMPORTS = frozenset({"psspy", "dyntools", "os"})
_FORBIDDEN_CALLS = frozenset(
    {"open", "eval", "exec", "__import__", "compile", "getattr", "globals", "vars"}
)
_FILE_LIKE = re.compile(r"[^\s]+\.[A-Za-z][A-Za-z0-9]{0,4}")
_ABS = re.compile(r"(/|\\|~|[A-Za-z]:[\\/])")
_PLANT_KINDS = ("v_step", "q_step", "p_step")
_STEP_DT_S = 0.005
_TWO_PI = 2.0 * math.pi


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


_HERE_EXPR = "os.path.dirname(os.path.abspath(__file__))"


def _open_ok(node, allowed):
    """True for exactly ``open(os.path.join(HERE, '<allowed>.csv'), 'w')``."""
    if node.keywords or len(node.args) != 2:
        return False
    path, mode = node.args
    if not (isinstance(mode, ast.Constant) and mode.value == "w"):
        return False
    if not (isinstance(path, ast.Call) and _dotted(path.func) == "os.path.join"):
        return False
    if path.keywords or len(path.args) != 2:
        return False
    here, name = path.args
    return (
        isinstance(here, ast.Name)
        and here.id == "HERE"
        and isinstance(name, ast.Constant)
        and name.value in allowed
        and str(name.value).endswith(".csv")
    )


def _calls(node):
    return {_dotted(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)}


def _solve_order_ok(tree):
    """
    True when the module's top level holds ``psspy.fnsl(...)``, then an
    ``if`` on ``psspy.solved()`` that raises, then ``psspy.cong(...)``.
    """
    want = [
        lambda n: isinstance(n, ast.Expr) and "psspy.fnsl" in _calls(n),
        lambda n: (
            isinstance(n, ast.If)
            and "psspy.solved" in _calls(n.test)
            and any(isinstance(b, ast.Raise) for b in n.body)
        ),
        lambda n: isinstance(n, ast.Expr) and "psspy.cong" in _calls(n),
    ]
    for node in tree.body:
        if want and want[0](node):
            want.pop(0)
    return not want


def check_script(text, allowed_files, channels=()) -> set:
    """
    Parse ``text`` and return the file names it references; raise ContractError
    listing every breach of the pack contract (module docstring).

    A string literal names a file when it has no whitespace and ends in an
    extension, or holds a path separator; docstrings are skipped.
    """
    tree = ast.parse(text)
    allowed, problems, files, literals = set(allowed_files), [], set(), set()
    docs = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.FunctionDef))
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docs:
                continue
            s = node.value
            literals.add(s)
            if _ABS.match(s):
                problems.append(f"absolute path literal {s!r}")
            elif "/" in s or "\\" in s or _FILE_LIKE.fullmatch(s):
                files.add(s)
                if s not in allowed:
                    problems.append(f"file {s!r} is not in the pack {sorted(allowed)}")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in names:
                if name.split(".")[0] not in ALLOWED_IMPORTS:
                    problems.append(f"import of {name!r}")
            if isinstance(node, ast.ImportFrom) and node.module == "os":
                bad = [a.name for a in node.names if a.name != "path"]
                if bad:
                    problems.append(f"import of {bad} from {node.module}")
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name == "open" and _open_ok(node, allowed):
                continue
            if name.split(".")[-1] in _FORBIDDEN_CALLS or "subprocess" in name:
                problems.append(f"call to {name}")
        elif isinstance(node, ast.Attribute) and not isinstance(
            node.value, ast.Attribute
        ):
            root = _dotted(node).split(".")[0]
            if root == "os" and node.attr != "path":
                problems.append(f"os call outside os.path: os.{node.attr}")
    here = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Name)
        and n.id == "HERE"
        and not isinstance(n.ctx, ast.Load)
    ]
    binds = (
        [
            n
            for n in tree.body
            if isinstance(n, ast.Assign)
            and [ast.dump(t) for t in n.targets] == [ast.dump(here[0])]
        ]
        if here
        else []
    )
    if len(here) != 1 or len(binds) != 1 or ast.unparse(binds[0].value) != _HERE_EXPR:
        problems.append(f"HERE must be bound once, to {_HERE_EXPR}")
    if not _solve_order_ok(tree):
        problems.append(
            "the case must be solved before dynamics: psspy.fnsl, then an if on "
            "psspy.solved() that raises, then psspy.cong"
        )
    for ch in channels:
        if ch not in literals:
            problems.append(f"channel {ch!r} has no ident literal")
    if problems:
        raise ContractError("psspy script: " + "; ".join(problems))
    return files


def _pack_local(label, name):
    if not isinstance(name, str) or not name:
        raise ContractError(f"{label} {name!r}: not a file name")
    if os.path.isabs(name) or _ABS.match(name):
        raise ContractError(f"{label} {name!r}: absolute path; give a pack-local name")
    if os.path.basename(name) != name or name in (".", ".."):
        raise ContractError(f"{label} {name!r}: not pack-local (no directories)")


def _event_lines(case, poc_kv):
    p, kind = case.params, case.kind
    if kind == "flat":
        return ["# flat run: no event"]
    if kind == "fault_3ph":
        zf = fault_impedance_ohm(case, poc_kv)
        vals = (
            "1, POC_KV, [0.0, -2.0e11]"
            if zf == 0
            else (f"2, POC_KV, [{zf.real!r}, {zf.imag!r}]")
        )
        return [
            f"# three-phase fault, retained voltage {p['v_retained_pu']!r} pu",
            f"psspy.dist_bus_fault(POC_BUS, {vals})",
            f"psspy.run(0, T_EVENT + {p['duration_s']!r}, 1000, 1, 0)",
            "psspy.dist_clear_fault(1)",
        ]
    if kind == "fault_1ph":
        zf = fault_impedance_ohm(case, poc_kv)
        return [
            f"# single-phase-to-earth fault, retained voltage {p['v_retained_pu']!r} pu",
            f"ZF_OHM = ({zf.real!r}, {zf.imag!r})",
            "ZF_PU = [z * psspy.sysmva() / POC_KV**2 for z in ZF_OHM]",
            "psspy.dist_scmu_fault_2([1, 1, POC_BUS, 0, 0], ZF_PU + [0.0, 0.0])",
            f"psspy.run(0, T_EVENT + {p['duration_s']!r}, 1000, 1, 0)",
            "psspy.dist_clear_fault(1)",
        ]
    if kind in ("v_step", "q_step"):
        return [f"psspy.increment_vref(PLANT_BUS, PLANT_ID, {p['step_pu']!r})"]
    if kind == "p_step":
        return [f"psspy.increment_gref(PLANT_BUS, PLANT_ID, {p['step_pu']!r})"]
    head = [
        "ierr, K = psspy.mdlind(GRID_BUS, GRID_ID, 'GEN', 'STATE')",
        f"K_ANGLE = K + {ANGLE_STATE_OFFSET!r}  # GENCLS angle state: unverified",
        "ierr, ANGLE0 = psspy.dsrval('STATE', K_ANGLE)",
    ]
    if kind == "phase_jump":
        rad = math.radians(p["angle_deg"])
        return head + [f"psspy.change_state(K_ANGLE, ANGLE0 + {rad!r})"]
    if kind == "f_step":
        rate, ramp, hold = p["step_hz"], 0.0, 0.0
    else:
        rate, ramp, hold = 0.0, p["ramp_hz_per_s"], p["duration_s"]
    return head + [
        f"DF0, RAMP, HOLD, DT = {rate!r}, {ramp!r}, {hold!r}, {_STEP_DT_S!r}",
        "n = 1",
        "while T_EVENT + n * DT < T_END:",
        "    tau = n * DT",
        "    th = min(tau, HOLD)",
        "    cycles = DF0 * tau + RAMP * (0.5 * th * th + HOLD * (tau - th))",
        f"    psspy.change_state(K_ANGLE, ANGLE0 + {_TWO_PI!r} * cycles)",
        "    psspy.run(0, T_EVENT + tau, 1000, 1, 0)",
        "    n += 1",
    ]


def _recorded(case):
    return {r for ch in case.channels for r in CONVERSIONS[ch][0]}


def _channel_lines(case):
    raw, out = _recorded(case), []
    if "raw_theta_poc_deg" in raw:
        out.append(
            "psspy.voltage_and_angle_channel([-1, -1, -1, POC_BUS], "
            "['raw_v_poc_pu', 'raw_theta_poc_deg'])"
        )
    elif "raw_v_poc_pu" in raw:
        out.append("psspy.voltage_channel([-1, -1, -1, POC_BUS], 'raw_v_poc_pu')")
    if "raw_theta_src" in raw:
        out.append(
            "psspy.machine_array_channel([-1, 1, GRID_BUS], GRID_ID, 'raw_theta_src')"
        )
    if "raw_f_hz" in raw:
        out.append("psspy.bus_frequency_channel([-1, POC_BUS], 'raw_f_hz')")
    if raw & {"raw_p_poc_mw", "raw_q_poc_mvar"}:
        out.append(
            "psspy.branch_p_and_q_channel([-1, -1, -1, BR_FROM, BR_TO], BR_CKT, "
            "['raw_p_poc_mw', 'raw_q_poc_mvar'])"
        )
    for name, code in (("raw_id_pu", 22), ("raw_iq_pu", 23)):
        if name in raw:
            out.append(
                f"psspy.machine_array_channel([-1, {code}, PLANT_BUS], PLANT_ID, {name!r})"
            )
    return out


def _convert_lines(case):
    """A pure ``_convert(rec, f0_hz, s_rated)``: recorded lists -> contract units."""
    out = [
        "def _convert(rec, f0_hz, s_rated):",
        "    def _refuse(why):",
        "        raise ValueError('not evaluated, no CSV written: ' + why)",
        "",
        "    out = {'t_s': list(rec['t_s'])}",
    ]
    for ch in case.channels:
        inputs, expr, formula = CONVERSIONS[ch]
        if len(inputs) > len("abc"):
            raise ContractError(
                f"conversion of {ch!r}: {len(inputs)} inputs, at most 3"
            )
        names = "abc"[: len(inputs)]
        cols = ", ".join(f"rec[{r!r}]" for r in inputs)
        out += [
            f"    # {formula}",
            f"    out[{ch!r}] = [{expr} for ({', '.join(names)},) in zip({cols})]",
        ]
    return out + ["    return out", ""]


def _script(
    case,
    files,
    csv,
    poc_bus,
    poc_kv,
    grid_machine,
    mbase,
    plant_branch,
    plant_machine,
    f0_hz,
    plant_mbase,
):
    g, cid = case.grid, case.case_id
    z1, z0 = thevenin_impedances_ohm(g, poc_kv)
    to_pu = mbase / poc_kv**2
    z1p, z0p = z1 * to_pu, (None if z0 is None else z0 * to_pu)
    params = ", ".join(f"{k}={v!r}" for k, v in case.params.items()) or "none"
    lines = [
        '"""',
        f"PSS/E run for battery case {cid}.",
        "",
        f"Case {cid}; kind {case.kind}; TRGC {case.trgc}; grid corner {g.label} "
        f"(Sk {g.sk_mva!r} MVA, X/R {g.x_over_r!r}); parameters: {params}; "
        f"source {case.source}.",
        "",
        PENDING + ".",
        '"""',
        "import os",
        "",
        "import dyntools",
        "import psspy",
        "",
        "HERE = os.path.dirname(os.path.abspath(__file__))",
    ]
    for var, name in files.items():
        lines.append(f"{var} = os.path.join(HERE, {name!r})")
    lines += [
        f"POC_BUS, POC_KV = {poc_bus!r}, {float(poc_kv)!r}",
        f"GRID_BUS, GRID_ID, GRID_MBASE = {grid_machine[0]!r}, {grid_machine[1]!r}, "
        f"{float(mbase)!r}",
        f"Z1_PU = ({z1p.real!r}, {z1p.imag!r})  # Thevenin Z1 on GRID_MBASE",
        f"T_EVENT, T_END = {case.t_event_s!r}, {case.t_end_s!r}",
        f"F0_HZ, PLANT_MBASE = {float(f0_hz)!r}, "
        f"{None if plant_mbase is None else float(plant_mbase)!r}",
        f"HEADER = {['t_s', *case.channels]!r}",
    ]
    if plant_branch is not None:
        lines.append("BR_FROM, BR_TO, BR_CKT = {!r}, {!r}, {!r}".format(*plant_branch))
    if plant_machine is not None:
        lines.append("PLANT_BUS, PLANT_ID = {!r}, {!r}".format(*plant_machine))
    lines += [
        "",
        "",
        *_convert_lines(case),
        "",
        "psspy.psseinit()",
        "psspy.read(0, RAW)",
        "psspy.resq(SEQ)",
        "ierr, mbase = psspy.macdat(GRID_BUS, GRID_ID, 'MBASE')",
        "if ierr or abs(mbase - GRID_MBASE) > 1e-6:",
        "    raise SystemExit('grid machine MBASE differs from the generated base')",
        "_i, _f = psspy.getdefaultint(), psspy.getdefaultreal()",
        "psspy.machine_chng_2(GRID_BUS, GRID_ID, [_i] * 6, "
        "[_f] * 7 + list(Z1_PU) + [_f] * 8)",
    ]
    if z0p is not None:
        lines += [
            f"Z0_PU = ({z0p.real!r}, {z0p.imag!r})  # Thevenin Z0 on GRID_MBASE",
            "psspy.seq_machine_data_3(GRID_BUS, GRID_ID, 0, "
            "list(Z1_PU) + list(Z1_PU) + list(Z0_PU) + [_f, _f])",
        ]
    lines += [
        f"psspy.fnsl({list(FNSL_OPTIONS)!r})",
        "if psspy.solved() != 0:",
        "    raise SystemExit('power flow did not converge; dynamics not started')",
        "psspy.cong(0)",
        "for k in (1, 2, 3):",
        "    psspy.conl(0, 1, k, [0, 0], [100.0, 0.0, 0.0, 100.0])",
        "psspy.ordr(0)",
        "psspy.fact()",
        "psspy.tysl(0)",
        "psspy.dyre_new([1, 1, 1, 1], DYR, '', '', '')",
        *_channel_lines(case),
        "psspy.strt(0, OUT)",
        "psspy.run(0, T_EVENT, 1000, 1, 0)",
        *_event_lines(case, poc_kv),
        "psspy.run(0, T_END, 1000, 1, 0)",
        "_title, idents, values = dyntools.CHNF(OUT).get_data()",
        "rec = {idents[k]: values[k] for k in idents if k != 'time'}",
        "rec['t_s'] = values['time']",
        "data = _convert(rec, F0_HZ, PLANT_MBASE)",
        f"with open(os.path.join(HERE, {csv!r}), 'w') as fh:",
        "    fh.write(','.join(HEADER) + '\\n')",
        "    for row in zip(*[data[h] for h in HEADER]):",
        "        fh.write(','.join(repr(float(x)) for x in row) + '\\n')",
        "",
    ]
    return "\n".join(lines)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_positive_int(v):
    return _is_int(v) and v > 0


def _check_machine(label, m):
    ok = isinstance(m, tuple) and len(m) == 2
    if not ok or not _is_positive_int(m[0]) or not isinstance(m[1], str):
        raise ContractError(f"{label}={m!r} must be a (positive int bus, str id) tuple")
    if not 1 <= len(m[1]) <= 2:
        raise ContractError(f"{label} id {m[1]!r} must be 1 or 2 characters")


def write_scripts(
    cases,
    pack_dir,
    *,
    raw,
    seq,
    dyr,
    poc_bus,
    poc_kv,
    grid_machine,
    grid_mbase_mva,
    f0_hz,
    plant_branch=None,
    plant_machine=None,
    plant_mbase_mva=None,
) -> dict:
    """
    Write ``run_<case_id>.py`` into ``pack_dir`` for each case; return
    ``{case_id: script file name}``.

    ``raw``/``seq``/``dyr`` are pack-local file names; ``poc_bus`` the RAW bus
    number of the PoC, ``poc_kv`` its base kV; ``grid_machine`` the grid source
    ``(bus, id)`` with MBASE ``grid_mbase_mva``; ``plant_branch`` the PoC branch
    ``(from_bus = plant side, to_bus, ckt)`` for P/Q/current channels;
    ``plant_machine`` the ``(bus, id)`` whose references the V/Q/P steps move
    and whose current commands id/iq record; ``f0_hz`` the base frequency that
    turns PSS/E's pu frequency deviation into Hz; ``plant_mbase_mva`` the plant
    machine's MBASE (= plant rated), needed when a case records ``i_poc_pu``.
    """
    cases = tuple(cases)
    if not cases:
        raise ContractError("no battery cases to write scripts for")
    cases = validate_battery(cases)
    for label, name in (("raw", raw), ("seq", seq), ("dyr", dyr)):
        _pack_local(label, name)
    if not _is_positive_int(poc_bus):
        raise ContractError(f"poc_bus={poc_bus!r} must be a positive int bus number")
    _check_machine("grid_machine", grid_machine)
    if plant_machine is not None:
        _check_machine("plant_machine", plant_machine)
    if plant_branch is not None:
        ok = isinstance(plant_branch, tuple) and len(plant_branch) == 3
        if (
            not ok
            or not all(_is_int(b) for b in plant_branch[:2])
            or not isinstance(plant_branch[2], str)
        ):
            raise ContractError(
                f"plant_branch={plant_branch!r} must be a (from bus, to bus, ckt) "
                "tuple of (int, int, str)"
            )
        if plant_branch[0] != poc_bus:
            raise ContractError(
                f"plant_branch from bus {plant_branch[0]!r} is not poc_bus "
                f"{poc_bus!r}: the PoC branch is measured at the PoC"
            )
    positive = [
        ("poc_kv", poc_kv),
        ("grid_mbase_mva", grid_mbase_mva),
        ("f0_hz", f0_hz),
    ]
    if plant_mbase_mva is not None or any("i_poc_pu" in c.channels for c in cases):
        positive.append(("plant_mbase_mva", plant_mbase_mva))
    for label, v in positive:
        ok = isinstance(v, (int, float)) and not isinstance(v, bool)
        if not ok or not math.isfinite(v) or v <= 0:
            raise ContractError(f"{label}={v!r} must be a finite number > 0")
    for c in cases:
        if plant_branch is None and set(c.channels) & {
            "p_poc_mw",
            "q_poc_mvar",
            "i_poc_pu",
        }:
            raise ContractError(
                f"case {c.case_id}: P/Q/current channels need plant_branch"
            )
        needs_machine = c.kind in _PLANT_KINDS or set(c.channels) & {"id_pu", "iq_pu"}
        if plant_machine is None and needs_machine:
            raise ContractError(
                f"case {c.case_id}: setpoint steps and id/iq channels need plant_machine"
            )
    os.makedirs(pack_dir, exist_ok=True)
    written = {}
    for c in cases:
        files = {"RAW": raw, "SEQ": seq, "DYR": dyr, "OUT": f"{c.case_id}.out"}
        csv = f"{c.case_id}.csv"
        text = _script(
            c,
            files,
            csv,
            poc_bus,
            poc_kv,
            grid_machine,
            grid_mbase_mva,
            plant_branch,
            plant_machine,
            f0_hz,
            plant_mbase_mva,
        )
        check_script(text, {*files.values(), csv}, c.channels)
        name = f"run_{c.case_id}.py"
        with open(
            os.path.join(pack_dir, name), "w", encoding="utf-8", newline="\n"
        ) as fh:
            fh.write(text)
        written[c.case_id] = name
    return written
