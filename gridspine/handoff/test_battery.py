"""
The tool-neutral test battery: DMAT and compliance cases as data.

TRGC 04/05 (dynamic model acceptance tests) asks for the same set of cases
in every simulation tool: a single-machine-infinite-bus (SMIB) equivalent at
the minimum and maximum short-circuit level and X/R, balanced and unbalanced
faults, voltage / reactive / active power setpoint steps, frequency steps and
ramps, and a phase-angle jump. This module holds that set once. The PSS/E pack
(psspy scripts), the PSCAD case schedule and the read-back judge all walk the
same :class:`Case` objects and name their output channels from the one
contract in :data:`CHANNELS`, so a result file maps back to a case id with no
translation table.

Nothing here imports an engine or a tool API; it is data plus its checks.
A :class:`Case` is immutable (``params`` is a read-only mapping) and is keyed
by ``case_id``, which is unique in a battery and safe as a file name.

**Thevenin source.** Each case runs against an ideal source behind an
impedance set by ``sk_mva`` (three-phase short-circuit level at the PoC) and
``x_over_r``. The source voltage is 1.0 pu before the event.

**Event parameters** (per kind, :data:`KIND_PARAMS`):

- ``fault_3ph`` / ``fault_1ph``: ``v_retained_pu`` is the RETAINED voltage at
  the PoC during the fault (0.0 = bolted fault, 0.5 = half voltage left), in
  [0, 1); ``duration_s`` is the time from fault inception to clearing, > 0.
  For a single-phase fault the retained voltage is that of the faulted phase.
- ``v_step``: ``step_pu`` is the change of the voltage setpoint, pu of
  nominal voltage; nonzero.
- ``q_step``: ``step_pu`` is the change of the reactive power setpoint, pu of
  the plant's rated apparent power; nonzero; positive = more injection.
- ``p_step``: ``step_pu`` is the change of the active power setpoint, pu of
  the plant's rated active power; nonzero.
- ``f_step``: ``step_hz`` is the step of the source frequency; nonzero.
- ``f_ramp``: ``ramp_hz_per_s`` is the source frequency slope, nonzero, held
  for ``duration_s`` > 0, then constant.
- ``phase_jump``: ``angle_deg`` is the step of the source voltage angle,
  0 < |angle| < 180.
- ``flat``: no event; the flat run that checks initialisation.

Every number is finite (``math.isfinite``). A fault or ramp must end before
``t_end_s``; the event must start at or after 0 and before ``t_end_s``.

**Sources.** The battery's event depths, durations and step sizes are generic
engineering defaults (``source='assumed'``); a case edited through
``build_battery(overrides=...)`` is marked ``source='supplied'``. Sk and X/R
always come from the caller (a network study or the TSO's data).

**Serialisation.** :func:`battery_frame` / :func:`cases_from_frame` give a
flat table; :func:`battery_json` / :func:`cases_from_json` give the
manifest form. The JSON carries :data:`CONTRACT_VERSION` and the channel
contract, has sorted keys and Python's shortest round-trip float repr, so the
same battery always gives byte-equal JSON. A reader refuses a different
contract version.
"""

import json
import math
import re
from dataclasses import dataclass, field
from types import MappingProxyType

import pandas as pd

from gridspine.schema.contracts import ContractError

CONTRACT_VERSION = "1"


@dataclass(frozen=True)
class Channel:
    unit: str
    base: str
    sign: str
    point: str
    meaning: str


_POC = "point of connection (PoC), grid side of the plant's main transformer"
#: The channel contract: every tool writes these names, every reader reads them.
CHANNELS = {
    "v_poc_pu": Channel(
        "pu",
        "nominal PoC voltage (line-line, kV)",
        "magnitude, >= 0",
        _POC,
        "positive-sequence voltage magnitude",
    ),
    "theta_poc_deg": Channel(
        "deg",
        "angle of the Thevenin source voltage",
        "positive = PoC voltage leads the source",
        _POC,
        "positive-sequence voltage angle",
    ),
    "f_hz": Channel(
        "Hz",
        "absolute frequency",
        "absolute value",
        _POC,
        "frequency of the PoC voltage",
    ),
    "p_poc_mw": Channel(
        "MW",
        "absolute",
        "positive = injection into the grid (generator convention)",
        _POC,
        "active power",
    ),
    "q_poc_mvar": Channel(
        "Mvar",
        "absolute",
        "positive = injection into the grid (generator convention, over-excited)",
        _POC,
        "reactive power",
    ),
    "id_pu": Channel(
        "pu",
        "plant rated current at the PoC",
        "positive = active power injection",
        _POC,
        "positive-sequence active current",
    ),
    "iq_pu": Channel(
        "pu",
        "plant rated current at the PoC",
        "positive = reactive injection (capacitive, voltage-raising)",
        _POC,
        "positive-sequence reactive current",
    ),
    "i_poc_pu": Channel(
        "pu",
        "plant rated current at the PoC",
        "magnitude, >= 0",
        _POC,
        "positive-sequence current magnitude",
    ),
}

#: Required event parameters per kind; no other parameter is accepted.
KIND_PARAMS = {
    "flat": (),
    "fault_3ph": ("v_retained_pu", "duration_s"),
    "fault_1ph": ("v_retained_pu", "duration_s"),
    "v_step": ("step_pu",),
    "q_step": ("step_pu",),
    "p_step": ("step_pu",),
    "f_step": ("step_hz",),
    "f_ramp": ("ramp_hz_per_s", "duration_s"),
    "phase_jump": ("angle_deg",),
}
PARAM_NAMES = tuple(sorted({p for ps in KIND_PARAMS.values() for p in ps}))
SOURCES = ("assumed", "supplied")
_NONZERO = ("step_pu", "step_hz", "ramp_hz_per_s")
_ID = re.compile(r"[a-z0-9_]{1,64}")
CHANNEL_SEP = "|"


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


@dataclass(frozen=True)
class Thevenin:
    label: str
    sk_mva: float
    x_over_r: float

    def __post_init__(self):
        if not isinstance(self.label, str) or not _ID.fullmatch(self.label):
            raise ContractError(f"thevenin label {self.label!r}: not a lower-case id")
        for name in ("sk_mva", "x_over_r"):
            v = getattr(self, name)
            if not _finite(v):
                raise ContractError(
                    f"thevenin {self.label}: {name}={v!r} is not a finite number"
                )
            object.__setattr__(self, name, float(v))
        if self.sk_mva <= 0:
            raise ContractError(
                f"thevenin {self.label}: sk_mva={self.sk_mva} must be > 0"
            )
        if self.x_over_r <= 0:
            raise ContractError(
                f"thevenin {self.label}: x_over_r={self.x_over_r} must be > 0"
            )


@dataclass(frozen=True)
class Case:
    case_id: str
    kind: str
    trgc: str
    grid: Thevenin
    t_event_s: float
    t_end_s: float
    channels: tuple
    params: MappingProxyType = field(
        default_factory=dict
    )  # read-only after construction
    source: str = "assumed"

    def __post_init__(self):
        cid = self.case_id
        if not isinstance(cid, str) or not _ID.fullmatch(cid):
            raise ContractError(f"case id {cid!r}: not a lower-case id")
        if self.kind not in KIND_PARAMS:
            raise ContractError(f"case {cid}: unknown kind {self.kind!r}")
        if not isinstance(self.trgc, str) or not self.trgc:
            raise ContractError(f"case {cid}: trgc reference is empty")
        if not isinstance(self.grid, Thevenin):
            raise ContractError(f"case {cid}: grid is not a Thevenin source")
        if self.source not in SOURCES:
            raise ContractError(f"case {cid}: source {self.source!r} not in {SOURCES}")
        self._check_times()
        self._check_channels()
        self._check_params()

    def _check_times(self):
        cid = self.case_id
        for name in ("t_event_s", "t_end_s"):
            v = getattr(self, name)
            if not _finite(v):
                raise ContractError(f"case {cid}: {name}={v!r} is not a finite number")
            object.__setattr__(self, name, float(v))
        if self.t_event_s < 0:
            raise ContractError(f"case {cid}: t_event_s={self.t_event_s} must be >= 0")
        if self.t_event_s >= self.t_end_s:
            raise ContractError(
                f"case {cid}: event at {self.t_event_s} s is not before t_end_s={self.t_end_s}"
            )

    def _check_channels(self):
        cid = self.case_id
        chans = tuple(self.channels)
        if not chans:
            raise ContractError(f"case {cid}: names no output channels")
        unknown = [c for c in chans if c not in CHANNELS]
        if unknown:
            raise ContractError(
                f"case {cid}: unknown channel(s) {unknown}; the contract has {list(CHANNELS)}"
            )
        if len(set(chans)) != len(chans):
            raise ContractError(f"case {cid}: repeats a channel in {list(chans)}")
        object.__setattr__(self, "channels", chans)

    def _check_params(self):
        cid = self.case_id
        params = dict(self.params)
        need = KIND_PARAMS[self.kind]
        missing = [p for p in need if p not in params]
        if missing:
            raise ContractError(
                f"case {cid}: kind {self.kind} needs parameter(s) {missing}"
            )
        extra = sorted(set(params) - set(need))
        if extra:
            raise ContractError(
                f"case {cid}: kind {self.kind} takes no parameter(s) {extra}"
            )
        for p, v in params.items():
            if not _finite(v):
                raise ContractError(f"case {cid}: {p}={v!r} is not a finite number")
            params[p] = float(v)
        if "v_retained_pu" in params and not 0.0 <= params["v_retained_pu"] < 1.0:
            raise ContractError(
                f"case {cid}: v_retained_pu={params['v_retained_pu']} must be in [0, 1)"
            )
        if "duration_s" in params:
            if params["duration_s"] <= 0:
                raise ContractError(
                    f"case {cid}: duration_s={params['duration_s']} must be > 0"
                )
            if self.t_event_s + params["duration_s"] >= self.t_end_s:
                raise ContractError(
                    f"case {cid}: event ends at {self.t_event_s + params['duration_s']} s, "
                    f"not before t_end_s={self.t_end_s}"
                )
        for p in _NONZERO:
            if p in params and params[p] == 0:
                raise ContractError(f"case {cid}: {p} must be nonzero")
        if "angle_deg" in params and not 0 < abs(params["angle_deg"]) < 180:
            raise ContractError(
                f"case {cid}: angle_deg={params['angle_deg']} must satisfy 0 < |angle| < 180"
            )
        object.__setattr__(
            self, "params", MappingProxyType(dict(sorted(params.items())))
        )


def validate_battery(cases) -> tuple:
    """Return ``cases`` as a tuple after checking each is a Case and ids are unique."""
    cases = tuple(cases)
    if not cases:
        raise ContractError("test battery is empty")
    seen = set()
    for c in cases:
        if not isinstance(c, Case):
            raise ContractError(f"test battery holds a {type(c).__name__}, not a Case")
        if c.case_id in seen:
            raise ContractError(f"duplicate case id {c.case_id!r}")
        seen.add(c.case_id)
    return cases


# Generic engineering defaults (source='assumed'): one-second pre-event run,
# ten-second window, 150 ms fault clearing, ±5 % voltage, ±20 % Q and P steps,
# ±0.5 Hz frequency step, ±0.5 Hz/s ramp for 2 s, ±20° phase jump.
_T_EVENT_S = 1.0
_T_END_S = 10.0
_FAULT_S = 0.15
_V_RETAINED = (0.0, 0.2, 0.5)
_BASE_CH = ("v_poc_pu", "p_poc_mw", "q_poc_mvar", "f_hz")
_CURRENT_CH = ("id_pu", "iq_pu", "i_poc_pu")
_TRGC = "04/05"


def _ordered(names) -> tuple:
    return tuple(c for c in CHANNELS if c in set(names))


def build_battery(sk_min_mva, sk_max_mva, xr_min, xr_max, *, overrides=None) -> tuple:
    """
    The default DMAT battery for a PoC with the given Sk and X/R range.

    The SMIB flat run is done at all four (Sk, X/R) corners; events at the
    weak corner (Sk min, X/R min) and the strong corner (Sk max, X/R max);
    frequency events, which barely depend on strength, at the strong corner;
    the phase-angle jump, which is hardest on a weak grid, at the weak corner.

    ``overrides`` maps a case id to ``{name: value}`` for any of its event
    parameters, ``t_event_s`` or ``t_end_s``; an edited case is marked
    ``source='supplied'``. An unknown id or a name the case does not take is
    refused.
    """
    for name, v in (
        ("sk_min_mva", sk_min_mva),
        ("sk_max_mva", sk_max_mva),
        ("xr_min", xr_min),
        ("xr_max", xr_max),
    ):
        if not _finite(v):
            raise ContractError(f"{name}={v!r} is not a finite number")
    if sk_min_mva > sk_max_mva:
        raise ContractError(f"sk_min_mva={sk_min_mva} exceeds sk_max_mva={sk_max_mva}")
    if xr_min > xr_max:
        raise ContractError(f"xr_min={xr_min} exceeds xr_max={xr_max}")
    grids = {
        "skmin_xrmin": Thevenin("skmin_xrmin", sk_min_mva, xr_min),
        "skmin_xrmax": Thevenin("skmin_xrmax", sk_min_mva, xr_max),
        "skmax_xrmin": Thevenin("skmax_xrmin", sk_max_mva, xr_min),
        "skmax_xrmax": Thevenin("skmax_xrmax", sk_max_mva, xr_max),
    }
    weak, strong = grids["skmin_xrmin"], grids["skmax_xrmax"]
    specs = []  # (case_id, kind, grid, params, channels)
    for g in grids.values():
        specs.append((f"flat_{g.label}", "flat", g, {}, _BASE_CH))
    for kind in ("fault_3ph", "fault_1ph"):
        for g in (weak, strong):
            for v in _V_RETAINED:
                specs.append(
                    (
                        f"{kind}_{g.label}_v{round(v * 100):03d}",
                        kind,
                        g,
                        {"v_retained_pu": v, "duration_s": _FAULT_S},
                        _BASE_CH + _CURRENT_CH,
                    )
                )
    for kind, size, extra in (
        ("v_step", 0.05, ("iq_pu",)),
        ("q_step", 0.2, ("iq_pu",)),
        ("p_step", 0.2, ("id_pu",)),
    ):
        for g in (weak, strong):
            for tag, sign in (("up", 1), ("down", -1)):
                specs.append(
                    (
                        f"{kind}_{g.label}_{tag}",
                        kind,
                        g,
                        {"step_pu": sign * size},
                        _BASE_CH + extra,
                    )
                )
    for tag, sign in (("up", 1), ("down", -1)):
        specs.append(
            (
                f"f_step_{strong.label}_{tag}",
                "f_step",
                strong,
                {"step_hz": sign * 0.5},
                _BASE_CH,
            )
        )
        specs.append(
            (
                f"f_ramp_{strong.label}_{tag}",
                "f_ramp",
                strong,
                {"ramp_hz_per_s": sign * 0.5, "duration_s": 2.0},
                _BASE_CH,
            )
        )
        specs.append(
            (
                f"phase_jump_{weak.label}_{tag}",
                "phase_jump",
                weak,
                {"angle_deg": sign * 20.0},
                _BASE_CH + ("theta_poc_deg",) + _CURRENT_CH,
            )
        )

    overrides = dict(overrides or {})
    known = {s[0] for s in specs}
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise ContractError(f"overrides name unknown case id(s) {unknown}")
    cases = []
    for cid, kind, g, params, chans in specs:
        times = {"t_event_s": _T_EVENT_S, "t_end_s": _T_END_S}
        source = "assumed"
        if cid in overrides:
            edit = dict(overrides[cid])
            bad = sorted(set(edit) - set(params) - set(times))
            if bad:
                raise ContractError(f"override of {cid}: case takes no {bad}")
            params = {**params, **{k: v for k, v in edit.items() if k in params}}
            times = {**times, **{k: v for k, v in edit.items() if k in times}}
            source = "supplied"
        cases.append(
            Case(
                cid,
                kind,
                _TRGC,
                g,
                times["t_event_s"],
                times["t_end_s"],
                _ordered(chans),
                params,
                source,
            )
        )
    return validate_battery(cases)


_FRAME_HEAD = [
    "case_id",
    "kind",
    "trgc",
    "grid_label",
    "sk_mva",
    "x_over_r",
    "t_event_s",
    "t_end_s",
]
FRAME_COLUMNS = _FRAME_HEAD + list(PARAM_NAMES) + ["channels", "source"]


def battery_frame(cases) -> pd.DataFrame:
    """One row per case; parameters a kind does not take are NaN; channels joined on ``|``."""
    rows = []
    for c in validate_battery(cases):
        row = {
            "case_id": c.case_id,
            "kind": c.kind,
            "trgc": c.trgc,
            "grid_label": c.grid.label,
            "sk_mva": c.grid.sk_mva,
            "x_over_r": c.grid.x_over_r,
            "t_event_s": c.t_event_s,
            "t_end_s": c.t_end_s,
            "channels": CHANNEL_SEP.join(c.channels),
            "source": c.source,
        }
        row.update({p: c.params.get(p, math.nan) for p in PARAM_NAMES})
        rows.append(row)
    return pd.DataFrame(rows, columns=FRAME_COLUMNS)


def cases_from_frame(frame: pd.DataFrame) -> tuple:
    """Inverse of :func:`battery_frame`. NaN means "not taken": a taken parameter is never NaN."""
    missing = [c for c in FRAME_COLUMNS if c not in frame.columns]
    if missing:
        raise ContractError(f"battery frame lacks column(s) {missing}")
    cases = []
    for r in frame.to_dict("records"):
        params = {p: float(r[p]) for p in PARAM_NAMES if not pd.isna(r[p])}
        grid = Thevenin(r["grid_label"], float(r["sk_mva"]), float(r["x_over_r"]))
        cases.append(
            Case(
                r["case_id"],
                r["kind"],
                r["trgc"],
                grid,
                float(r["t_event_s"]),
                float(r["t_end_s"]),
                tuple(str(r["channels"]).split(CHANNEL_SEP)),
                params,
                r["source"],
            )
        )
    return validate_battery(cases)


def _contract_dict() -> dict:
    return {name: vars(ch) for name, ch in CHANNELS.items()}


def battery_json(cases) -> str:
    """Deterministic manifest form: same cases give byte-equal text."""
    doc = {
        "contract_version": CONTRACT_VERSION,
        "channels": _contract_dict(),
        "cases": [
            {
                "case_id": c.case_id,
                "kind": c.kind,
                "trgc": c.trgc,
                "grid": {
                    "label": c.grid.label,
                    "sk_mva": c.grid.sk_mva,
                    "x_over_r": c.grid.x_over_r,
                },
                "t_event_s": c.t_event_s,
                "t_end_s": c.t_end_s,
                "channels": list(c.channels),
                "params": dict(c.params),
                "source": c.source,
            }
            for c in validate_battery(cases)
        ],
    }
    return json.dumps(doc, sort_keys=True, indent=1, allow_nan=False) + "\n"


def cases_from_json(text: str) -> tuple:
    """Inverse of :func:`battery_json`; refuses another contract version or channel contract."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError(f"battery JSON does not parse: {exc}") from exc
    if not isinstance(doc, dict):
        raise ContractError("battery JSON is not an object")
    version = doc.get("contract_version")
    if version != CONTRACT_VERSION:
        raise ContractError(
            f"battery contract_version {version!r} is not {CONTRACT_VERSION!r}"
        )
    if doc.get("channels") != _contract_dict():
        raise ContractError("battery channel contract differs from this reader's")
    try:
        return validate_battery(
            Case(
                c["case_id"],
                c["kind"],
                c["trgc"],
                Thevenin(
                    c["grid"]["label"], c["grid"]["sk_mva"], c["grid"]["x_over_r"]
                ),
                c["t_event_s"],
                c["t_end_s"],
                tuple(c["channels"]),
                c["params"],
                c["source"],
            )
            for c in doc.get("cases", [])
        )
    except (KeyError, TypeError) as exc:
        raise ContractError(f"battery JSON case is malformed: {exc!r}") from exc
