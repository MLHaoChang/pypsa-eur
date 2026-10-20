"""The island data: control modes, unit dynamics and study requirements (plan I1).

Plan: ``docs/superpowers/plans/2026-10-07-campus-island-operation.md``.

One schema serves both homes of the data:

* the hub sidecar ``island_config.json``, keyed by PyPSA component name,
  validated by ``validate_island_config``. pypsa-gui wraps this function
  rather than defining its own model, because gridspine is imported by
  pypsa-gui and never the reverse;
* the ``island`` block of a campus unit and the campus-level ``island``
  requirements, validated by ``validate_unit_island`` and
  ``validate_requirements`` from ``ingest/campus.py``.

**Bases.** ``droop_pct`` and ``q_droop_pct`` are on the unit's rated active
power P_n, so K = P_n / (R * f0). ``H_s`` and ``H_v_s`` are on the rated
apparent power S_n. A droop GFM's inertia term is tau_f * P_n / (2 R).

**Every number is ``{value, source}``**, with ``source`` one of
``measured``, ``datasheet`` or ``assumed``, as in the campus file. A curve
or a list is one tagged value. Enumerations (``control``, ``governor``,
``excitation``, ``neutral_earthing``, ``scenarios``, ``gfl_min_case``),
flags (``critical``, ``spinning``, ``unit_transformer``,
``transfers_on_islanding``, ``linearised_uc``) and names (``it_load``,
``ride_through_storage``) are definitions, so they are untagged.

A validated block is ``{"kind", "values", "sources", ...untagged fields}``.
``values`` holds floats, or lists of floats for curves and lists.

pandas-free and engine-free: plain Python, like ``schema/contracts.py``.
"""
import copy
import math

from gridspine.schema.contracts import ContractError

SOURCES = frozenset({"measured", "datasheet", "assumed"})

CONTROLS = ("gfl_pq", "gfl_qu", "gfl_pf", "gfl_fw", "gfm_droop", "gfm_vsm")
GFM_CONTROLS = frozenset({"gfm_droop", "gfm_vsm"})
CONVERTER_KINDS = ("bess", "pv", "wind", "fuel_cell")
UNIT_KINDS = (*CONVERTER_KINDS, "genset", "ups", "load")
GOVERNORS = ("droop", "isochronous")
EXCITATIONS = ("avr_pmg", "avr_shunt", "none")
EARTHINGS = ("solid", "resistance", "isolated")
SCENARIOS = ("ups_bridged", "seamless", "planned")
GFL_MIN_CASES = ("keep", "drop")
LIMIT_SETS = ("transition", "steady_island")

#: The parameters each control mode needs, beyond ``pf_rated``. ``gfl_pf``
#: needs exactly one of its two (a fixed cos phi or a cos phi(P) curve).
_CONTROL_FIELDS = {
    "gfl_pq": (),
    "gfl_qu": ("qu_points",),
    "gfl_pf": (),
    "gfl_fw": ("fw_k_mw_per_hz", "fw_deadband_hz", "fw_delay_s", "fw_ramp_mw_per_s", "fw_p_limit_mw"),
    "gfm_droop": ("droop_pct", "q_droop_pct", "tau_f_s", "i_max_pu", "k_gfm_min"),
    "gfm_vsm": ("droop_pct", "q_droop_pct", "H_v_s", "i_max_pu", "k_gfm_min"),
}
_PF_CHOICE = ("cos_phi", "cosphi_p_points")
_CONVERTER_OPTIONAL = ("neg_seq",)

_GENSET_REQUIRED = ("q_droop_pct", "H_s", "T_g_s", "ramp_pu_per_s", "start_s", "sync_s",
                    "load_step_max_pct", "cos_phi_r", "xd_sat")
_GENSET_OPTIONAL = ("droop_pct", "p_min_pu", "r_n_ohm", "ik_sustained_pu", "x2_pu", "x0_pu", "unit_mw")
_GENSET_ENUMS = {"governor": GOVERNORS, "excitation": EXCITATIONS, "neutral_earthing": EARTHINGS}
_GENSET_FLAGS = ("spinning", "unit_transformer", "unit_transformer_oltc")
#: Fields only the hub sidecar carries: a campus unit is already built, so it
#: has its own x''d at unit level. ``unit_mw`` is carried into the campus
#: (owner, 2026-10-14): a campus genset may be several units, and I6's E4
#: removes N+k of them as I2b does.
_SIDECAR_ONLY = {"genset": ("xd_pp",)}

_UPS_REQUIRED = ("walk_in_s", "f_window_hz")
_LOAD_OPTIONAL = ("f_trip_hz",)

_REQ_REQUIRED = ("bridge_s", "sustained_h", "rocof_window_ms", "gfm_margin")
_REQ_OPTIONAL = ("fuel_autonomy_h", "genset_redundancy_n", "pickup_blocks", "i_max_sensitivity_pu",
                 "relay_sensitivity", "gfm_tuning_margin")
_REQ_STRUCTURAL = ("scenarios", "frequency_limits", "ride_through_storage", "linearised_uc", "gfl_min_case")
_LIMIT_FIELDS = ("rocof_hz_per_s", "f_min_hz", "f_max_hz", "qss_band_hz")
REQ_DEFAULTS = {"genset_redundancy_n": 1, "i_max_sensitivity_pu": [1.2, 1.5, 2.0], "relay_sensitivity": 1.5}


def _pos(v):
    return v > 0


def _nonneg(v):
    return v >= 0


def _unit_interval(v):
    return 0 < v <= 1


def _percent(v):
    return 0 < v <= 100


#: ``field: (check, wording)`` for every scalar field. A list or curve field
#: has its own check below.
_SCALAR = {
    "pf_rated": (_unit_interval, "in (0, 1]"),
    "cos_phi": (_unit_interval, "in (0, 1]"),
    "cos_phi_r": (_unit_interval, "in (0, 1]"),
    "droop_pct": (_pos, "positive"),
    "q_droop_pct": (_pos, "positive"),
    "tau_f_s": (_pos, "positive"),
    "H_v_s": (_pos, "positive"),
    "H_s": (_pos, "positive"),
    "T_g_s": (_pos, "positive"),
    "i_max_pu": (lambda v: v >= 1, "at least 1"),
    "k_gfm_min": (_nonneg, "non-negative"),
    "neg_seq": (_nonneg, "non-negative"),
    "fw_k_mw_per_hz": (_pos, "positive"),
    "fw_deadband_hz": (_nonneg, "non-negative"),
    "fw_delay_s": (_nonneg, "non-negative"),
    "fw_ramp_mw_per_s": (_pos, "positive"),
    "fw_p_limit_mw": (_pos, "positive"),
    "ramp_pu_per_s": (_pos, "positive"),
    "start_s": (_nonneg, "non-negative"),
    "sync_s": (_nonneg, "non-negative"),
    "load_step_max_pct": (_percent, "in (0, 100]"),
    "xd_sat": (_pos, "positive"),
    "p_min_pu": (lambda v: 0 <= v < 1, "in [0, 1)"),
    "r_n_ohm": (_pos, "positive"),
    "ik_sustained_pu": (_pos, "positive"),
    "x2_pu": (_pos, "positive"),
    "x0_pu": (_pos, "positive"),
    "relay_sensitivity": (lambda v: v >= 1, "at least 1"),
    "unit_mw": (_pos, "positive"),
    "xd_pp": (_pos, "positive"),
    "walk_in_s": (_nonneg, "non-negative"),
    "f_window_hz": (_pos, "positive"),
    "f_trip_hz": (lambda v: 40 <= v <= 70, "a frequency in [40, 70] Hz"),
    "bridge_s": (_pos, "positive"),
    "sustained_h": (_pos, "positive"),
    "fuel_autonomy_h": (_pos, "positive"),
    "rocof_window_ms": (_pos, "positive"),
    "gfm_margin": (_nonneg, "non-negative"),
    "gfm_tuning_margin": (lambda v: 0 <= v < 1, "in [0, 1)"),
    "genset_redundancy_n": (lambda v: v >= 0 and float(v).is_integer(), "a whole number of at least 0"),
    "rocof_hz_per_s": (_pos, "positive"),
    "f_min_hz": (lambda v: 40 <= v <= 70, "a frequency in [40, 70] Hz"),
    "f_max_hz": (lambda v: 40 <= v <= 70, "a frequency in [40, 70] Hz"),
    "qss_band_hz": (_pos, "positive"),
}
_LISTS = ("qu_points", "cosphi_p_points", "pickup_blocks", "i_max_sensitivity_pu")

#: Every field of the schema, and the plan increments that read it (plan I1,
#: "no orphan fields"). Adding a field means adding its reader here; the test
#: refuses a field that nobody reads.
FIELD_USERS = {
    # converters
    "control": ("I4", "I5", "I6", "I8"),
    "pf_rated": ("I2b", "I3a"),
    "droop_pct": ("I2b", "I3a", "I4", "I6"),
    "q_droop_pct": ("I4",),
    "tau_f_s": ("I2b", "I3a", "I6"),
    "H_v_s": ("I2b", "I3a", "I6"),
    "i_max_pu": ("I4", "I5", "I6"),
    "k_gfm_min": ("I5",),
    "qu_points": ("I4",),
    "cos_phi": ("I4",),
    "cosphi_p_points": ("I4",),
    "fw_k_mw_per_hz": ("I4", "I6"),
    "fw_deadband_hz": ("I4", "I6"),
    "fw_delay_s": ("I2b", "I6"),
    "fw_ramp_mw_per_s": ("I2b", "I6"),
    "fw_p_limit_mw": ("I2b", "I4", "I6"),
    "neg_seq": ("I5b",),
    # gensets
    "governor": ("I4", "I6", "I8"),
    "H_s": ("I2b", "I3a", "I6"),
    "T_g_s": ("I6", "I8"),
    "ramp_pu_per_s": ("I6",),
    "start_s": ("I2a", "I2b", "I6"),
    "sync_s": ("I2a", "I2b", "I6"),
    "spinning": ("I2b", "I3a"),
    "p_min_pu": ("I2b", "I2c"),
    "load_step_max_pct": ("I2b", "I6"),
    "cos_phi_r": ("I5",),
    "xd_sat": ("I5",),
    "excitation": ("I5",),
    "neutral_earthing": ("I5b",),
    "r_n_ohm": ("I5b",),
    "x2_pu": ("I5b",),
    "x0_pu": ("I5b",),
    "ik_sustained_pu": ("I5",),
    "relay_sensitivity": ("I5",),
    "unit_transformer": ("I5",),
    "unit_transformer_oltc": ("I5",),
    "unit_mw": ("I2a", "I6"),
    "xd_pp": ("I5",),
    # UPS
    "it_load": ("I2a", "I2b", "I3a", "I6"),
    "walk_in_s": ("I2b", "I6"),
    "f_window_hz": ("I6",),
    "transfers_on_islanding": ("I2b", "I6"),
    # loads
    "critical": ("I2a", "I2b", "I3a"),
    "f_trip_hz": ("I6",),
    # requirements
    "bridge_s": ("I2a", "I6"),
    "sustained_h": ("I2a",),
    "fuel_autonomy_h": ("I2a",),
    "genset_redundancy_n": ("I2a", "I2b"),
    "ride_through_storage": ("I2a",),
    "scenarios": ("I2b", "I2c", "I6"),
    "pickup_blocks": ("I2b", "I6"),
    "linearised_uc": ("I2c", "I3a"),
    "frequency_limits": ("I2b", "I6"),
    "rocof_hz_per_s": ("I2b", "I6"),
    "f_min_hz": ("I6",),
    "f_max_hz": ("I6",),
    "qss_band_hz": ("I2b", "I6"),
    "rocof_window_ms": ("I6",),
    "gfm_margin": ("I2b",),
    "gfm_tuning_margin": ("GFM-a",),            # recommended GFM settings (gfm_tuning), not I2b's margin
    "gfl_min_case": ("I5",),
    "i_max_sensitivity_pu": ("I5", "I6"),
    # control presets (data-centre grid readiness, ticket 04)
    "preset": ("CP04",),            # offered_services, the campus draft; ticket 02 reads offered_services
}

ALL_FIELDS = frozenset(
    {"control", "neutral_earthing", "governor", "excitation", "spinning", "unit_transformer", "unit_transformer_oltc",
     "it_load", "transfers_on_islanding", "critical", "scenarios", "frequency_limits",
     "ride_through_storage", "linearised_uc", "gfl_min_case", "preset"}
    | set(_SCALAR) | set(_LISTS)
)


# ── control presets (data-centre grid readiness, ticket 04) ───────────────
#
# A preset names a converter control and a UPS behaviour, so a project can
# say "grid forming support" without typing a droop. It is template-agnostic:
# it knows unit kinds, not templates. The sidecar's top-level ``preset`` is
# the project's choice; a unit's own ``preset`` overrides it, and a unit's
# ``"preset": null`` keeps the unit out of the project's preset.
#
# **Expansion** happens here, in ``validate_unit_island``: the preset fills
# every field the unit does not give, tagged ``assumed``; a field the unit
# gives wins with its own source. A unit ``control`` other than the preset's
# is refused, so a preset never names a mode the unit does not run. Every
# reader (the hub island LP, the campus draft, the frequency and GFM
# studies) then sees plain fields.
#
# **Kinds.** The hub island LP credits a grid former, and FFR, only on a
# storage-backed unit (a bess). ``grid_forming_support`` and
# ``fast_frequency_response`` therefore apply to bess only; ``grid_following``
# and ``voltage_support`` to every converter kind. A project preset leaves a
# kind it does not apply to as it is; a unit preset on such a kind is
# refused. The UPS block applies to ``ups`` units. Gensets and loads are
# never touched.

#: The grid services a unit can offer, as ``offered_services`` names them.
SERVICES = ("grid_forming", "synthetic_inertia", "frequency_containment", "fast_frequency_response",
            "voltage_control")

_QU_PRESET = [[0.9, 0.33], [0.97, 0.0], [1.03, 0.0], [1.1, -0.33]]

#: ``name: {"kinds", "converter", "ups"}``. Values are plain; expansion tags
#: them ``assumed``. The UPS blocks: under grid following the UPS goes to its
#: battery at a grid disturbance (it transfers on islanding, narrow window);
#: where the site's converters hold the island, it rides through.
PRESETS = {
    "grid_following": {
        "kinds": CONVERTER_KINDS,
        "converter": {"control": "gfl_pq", "pf_rated": 0.95},
        "ups": {"walk_in_s": 15.0, "f_window_hz": 0.5, "transfers_on_islanding": True},
    },
    "grid_forming_support": {
        "kinds": ("bess",),
        "converter": {"control": "gfm_vsm", "pf_rated": 0.95, "droop_pct": 4.0, "q_droop_pct": 4.0,
                      "H_v_s": 4.0, "i_max_pu": 1.2, "k_gfm_min": 1.0},
        "ups": {"walk_in_s": 10.0, "f_window_hz": 2.0, "transfers_on_islanding": False},
    },
    "fast_frequency_response": {
        "kinds": ("bess",),
        "converter": {"control": "gfl_fw", "pf_rated": 0.95, "fw_deadband_hz": 0.2, "fw_delay_s": 0.1},
        "ups": {"walk_in_s": 10.0, "f_window_hz": 2.0, "transfers_on_islanding": False},
    },
    "voltage_support": {
        "kinds": CONVERTER_KINDS,
        "converter": {"control": "gfl_qu", "pf_rated": 0.9, "qu_points": _QU_PRESET},
        "ups": {"walk_in_s": 10.0, "f_window_hz": 2.0, "transfers_on_islanding": False},
    },
}
#: The FFR fields that scale with the unit's rating: the preset cannot know
#: them, so the unit gives them, or ``ffr_sizing`` makes them from a rating.
FFR_SIZED = ("fw_k_mw_per_hz", "fw_ramp_mw_per_s", "fw_p_limit_mw")
#: ``ffr_sizing``: full output this far past the deadband, reached this fast.
FFR_FULL_HZ, FFR_RAMP_S = 0.3, 0.7
_UNTAGGED_PRESET = ("control", "transfers_on_islanding")


def ffr_sizing(rating_mw) -> dict:
    """The rating-scaled FFR fields of a ``rating_mw`` unit, tagged
    ``assumed``: full output (the rating) ``FFR_FULL_HZ`` past the deadband,
    ramped to in ``FFR_RAMP_S``. A rating of 0 or less (an extendable unit
    before sizing) has none: refused."""
    r = float(rating_mw)
    if not math.isfinite(r) or r <= 0:
        raise ContractError(f"ffr_sizing needs a positive rating, got {rating_mw!r}: an unsized unit offers no FFR")
    return {"fw_k_mw_per_hz": {"value": r / FFR_FULL_HZ, "source": "assumed"},
            "fw_ramp_mw_per_s": {"value": r / FFR_RAMP_S, "source": "assumed"},
            "fw_p_limit_mw": {"value": r, "source": "assumed"}}


def _check_preset_name(where, name):
    if name is not None and name not in PRESETS:
        raise ContractError(f"{where}.preset is {name!r}; allowed {list(PRESETS)} or null")
    return name


def preset_block(name, kind) -> dict | None:
    """The tagged fields preset ``name`` gives a unit of ``kind``, or None
    when it does not apply to that kind."""
    p = PRESETS[name]
    if kind == "ups":
        plain = p["ups"]
    elif kind in p["kinds"]:
        plain = p["converter"]
    else:
        return None
    return {f: (v if f in _UNTAGGED_PRESET else
                {"value": [list(x) for x in v] if isinstance(v, list) else v, "source": "assumed"})
            for f, v in plain.items()}


def _expand_preset(where, kind, spec, project_preset):
    """``(spec with the preset's fields filled, preset name or None)``."""
    if not isinstance(spec, dict) or "preset" not in spec:
        name, own = project_preset, False
    else:
        name, own = _check_preset_name(where, spec["preset"]), True
        spec = {k: v for k, v in spec.items() if k != "preset"}
    if name is None:
        return spec, None
    block = preset_block(name, kind) if kind in (*CONVERTER_KINDS, "ups") else None
    if block is None:
        if own:
            why = ("the hub island LP credits a grid former and FFR on storage-backed units (bess) only"
                   if kind in CONVERTER_KINDS else "presets set converter and UPS units only")
            raise ContractError(f"{where}: preset {name!r} does not apply to a {kind}: {why}")
        return spec, None
    if "control" in spec and spec["control"] != block.get("control", spec["control"]):
        raise ContractError(
            f"{where}: control {spec['control']!r} conflicts with preset {name!r} ({block['control']!r}); "
            "drop the control, or set \"preset\": null to keep the unit's own block")
    if block.get("control") == "gfl_fw":
        missing = [f for f in FFR_SIZED if f not in spec]
        if missing:
            raise ContractError(
                f"{where}: preset {name!r} needs {missing}, which scale with the unit's rating; "
                "ffr_sizing(rating_mw) makes them, or set \"preset\": null")
    return {**block, **spec}, name


def _tag(where, field, entry):
    """``(value, source)`` of a ``{value, source}`` entry, checked."""
    if not isinstance(entry, dict) or set(entry) != {"value", "source"}:
        raise ContractError(f"{where}.{field} must be a mapping with 'value' and 'source', got {entry!r}")
    if entry["source"] not in SOURCES:
        raise ContractError(f"{where}.{field} has unknown source {entry['source']!r}; allowed {sorted(SOURCES)}")
    return entry["value"], entry["source"]


def _number(where, field, v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ContractError(f"{where}.{field} value must be a finite number, got {v!r}")
    return float(v)


def _scalar(where, field, entry):
    v, src = _tag(where, field, entry)
    v = _number(where, field, v)
    check, wording = _SCALAR[field]
    if not check(v):
        raise ContractError(f"{where}.{field} must be {wording}, got {v:g}")
    return v, src


def _points(where, field, entry, x_check, y_check, wording):
    v, src = _tag(where, field, entry)
    if not isinstance(v, list) or len(v) < 2 or not all(isinstance(p, list) and len(p) == 2 for p in v):
        raise ContractError(f"{where}.{field} must be a list of at least two [x, y] pairs, got {v!r}")
    pts = [[_number(where, field, x), _number(where, field, y)] for x, y in v]
    xs = [p[0] for p in pts]
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise ContractError(f"{where}.{field}: x must be strictly increasing, got {xs}")
    if not all(x_check(x) and y_check(y) for x, y in pts):
        raise ContractError(f"{where}.{field}: {wording}, got {pts}")
    return pts, src


def _number_list(where, field, entry, check, wording):
    v, src = _tag(where, field, entry)
    if not isinstance(v, list) or not v:
        raise ContractError(f"{where}.{field} must be a non-empty list of numbers, got {v!r}")
    out = [_number(where, field, x) for x in v]
    if not all(check(x) for x in out):
        raise ContractError(f"{where}.{field}: every entry must be {wording}, got {out}")
    return out, src


def _value(where, field, entry):
    if field == "qu_points":
        return _points(where, field, entry, lambda x: x > 0, lambda y: -1 <= y <= 1,
                       "voltages must be positive pu and Q within [-1, 1] pu of S_n")
    if field == "cosphi_p_points":
        return _points(where, field, entry, lambda x: 0 <= x <= 1, lambda y: 0 < abs(y) <= 1,
                       "P must be in [0, 1] pu and |cos phi| in (0, 1] (negative = under-excited)")
    if field == "pickup_blocks":
        return _number_list(where, field, entry, _pos, "positive MW")
    if field == "i_max_sensitivity_pu":
        return _number_list(where, field, entry, lambda x: x >= 1, "at least 1 pu")
    return _scalar(where, field, entry)


def _allowed(where, spec, allowed):
    if not isinstance(spec, dict):
        raise ContractError(f"{where}: expected a mapping, got {type(spec).__name__}")
    unknown = sorted(set(spec) - set(allowed))
    if unknown:
        raise ContractError(f"{where}: unknown field(s) {unknown}; allowed {sorted(allowed)}")


def _require(where, spec, fields):
    missing = [f for f in fields if f not in spec]
    if missing:
        raise ContractError(f"{where}: missing {missing}")


def _enum(where, field, v, allowed):
    if v not in allowed:
        raise ContractError(f"{where}.{field} is {v!r}; allowed {list(allowed)}")
    return v


def _flag(where, field, spec, default=False):
    v = spec.get(field, default)
    if not isinstance(v, bool):
        raise ContractError(f"{where}.{field} must be true or false, got {v!r}")
    return v


def _names(where, field, v):
    if not isinstance(v, list) or not v or not all(isinstance(x, str) and x for x in v):
        raise ContractError(f"{where}.{field} must be a non-empty list of names, got {v!r}")
    return list(v)


def _collect(where, spec, fields, out):
    for f in fields:
        if f in spec:
            out["values"][f], out["sources"][f] = _value(where, f, spec[f])


def validate_unit_island(where, kind, spec, sidecar=False, preset=None) -> dict:
    """One unit's island block, checked. ``kind`` is the unit's campus kind.
    ``sidecar`` allows the fields only the hub sidecar carries. ``preset`` is
    the project's control preset; the unit's own ``preset`` overrides it
    (see "control presets" above). The block carries ``preset`` only when
    one applied."""
    if kind not in UNIT_KINDS:
        raise ContractError(f"{where}: unknown kind {kind!r}; allowed {list(UNIT_KINDS)}")
    spec, applied = _expand_preset(where, kind, spec, preset)
    extra = _SIDECAR_ONLY.get(kind, ()) if sidecar else ()
    out = {"kind": kind, "values": {}, "sources": {}}
    if applied is not None:
        out["preset"] = applied

    if kind in CONVERTER_KINDS:
        if not isinstance(spec, dict) or "control" not in spec:
            raise ContractError(f"{where}: missing 'control'; allowed {list(CONTROLS)}")
        control = _enum(where, "control", spec["control"], CONTROLS)
        needs = _CONTROL_FIELDS[control]
        pf_fields = _PF_CHOICE if control == "gfl_pf" else ()
        _allowed(where, spec, ("control", "pf_rated", *needs, *pf_fields, *_CONVERTER_OPTIONAL))
        _require(where, spec, ("pf_rated", *needs))
        if pf_fields and sum(f in spec for f in pf_fields) != 1:
            raise ContractError(f"{where}: gfl_pf needs exactly one of {list(pf_fields)}")
        _collect(where, spec, ("pf_rated", *needs, *pf_fields, *_CONVERTER_OPTIONAL), out)
        out["control"] = control
        if control in GFM_CONTROLS and out["values"]["k_gfm_min"] > out["values"]["i_max_pu"]:
            raise ContractError(
                f"{where}.k_gfm_min ({out['values']['k_gfm_min']:g}) must not exceed "
                f"i_max_pu ({out['values']['i_max_pu']:g})")

    elif kind == "genset":
        _allowed(where, spec, (*_GENSET_REQUIRED, *_GENSET_OPTIONAL, *_GENSET_ENUMS, *_GENSET_FLAGS, *extra))
        _require(where, spec, (*_GENSET_REQUIRED, *_GENSET_ENUMS))
        for f, allowed in _GENSET_ENUMS.items():
            out[f] = _enum(where, f, spec[f], allowed)
        for f in _GENSET_FLAGS:
            out[f] = _flag(where, f, spec)
        if out["governor"] == "droop" and "droop_pct" not in spec:
            raise ContractError(f"{where}: a droop governor needs droop_pct")
        if (out["neutral_earthing"] == "resistance") != ("r_n_ohm" in spec):
            raise ContractError(f"{where}: r_n_ohm is needed with resistance earthing, and only then")
        _collect(where, spec, (*_GENSET_REQUIRED, *_GENSET_OPTIONAL, *extra), out)
        if out["spinning"] and not out["values"].get("p_min_pu", 0) > 0:
            raise ContractError(f"{where}: a spinning genset needs p_min_pu above 0, or spinning costs no fuel")

    elif kind == "ups":
        _allowed(where, spec, (*_UPS_REQUIRED, "it_load", "transfers_on_islanding"))
        _require(where, spec, (*_UPS_REQUIRED, "it_load"))
        out["it_load"] = _names(where, "it_load", spec["it_load"])
        out["transfers_on_islanding"] = _flag(where, "transfers_on_islanding", spec)
        _collect(where, spec, _UPS_REQUIRED, out)

    else:                                                   # load
        _allowed(where, spec, ("critical", *_LOAD_OPTIONAL))
        out["critical"] = _flag(where, "critical", spec)
        _collect(where, spec, _LOAD_OPTIONAL, out)
    return out


def validate_requirements(spec, where="requirements") -> dict:
    """The study requirements, checked, with defaults filled and tagged
    ``assumed``."""
    _allowed(where, spec, (*_REQ_REQUIRED, *_REQ_OPTIONAL, *_REQ_STRUCTURAL))
    _require(where, spec, (*_REQ_REQUIRED, "scenarios", "frequency_limits"))
    out = {"values": {}, "sources": {}}
    _collect(where, spec, (*_REQ_REQUIRED, *_REQ_OPTIONAL), out)
    for f, default in REQ_DEFAULTS.items():
        if f not in out["values"]:
            out["values"][f], out["sources"][f] = default, "assumed"
    out["values"]["genset_redundancy_n"] = int(out["values"]["genset_redundancy_n"])

    sc = spec["scenarios"]
    if not isinstance(sc, list) or not sc or not set(sc) <= set(SCENARIOS) or len(set(sc)) != len(sc):
        raise ContractError(f"{where}.scenarios must be a non-empty list of distinct {list(SCENARIOS)}, got {sc!r}")
    out["scenarios"] = list(sc)
    if "ups_bridged" in sc and "pickup_blocks" not in spec:
        raise ContractError(f"{where}: the ups_bridged scenario needs pickup_blocks")

    fl = spec["frequency_limits"]
    _allowed(f"{where}.frequency_limits", fl, LIMIT_SETS)
    out["frequency_limits"] = {}
    for name in LIMIT_SETS:
        w = f"{where}.frequency_limits.{name}"
        if name not in fl:
            raise ContractError(f"{where}.frequency_limits: missing {name!r}")
        _allowed(w, fl[name], _LIMIT_FIELDS)
        _require(w, fl[name], _LIMIT_FIELDS)
        lim = {"values": {}, "sources": {}}
        _collect(w, fl[name], _LIMIT_FIELDS, lim)
        if not lim["values"]["f_min_hz"] < lim["values"]["f_max_hz"]:
            raise ContractError(f"{w}: f_min_hz must be below f_max_hz")
        out["frequency_limits"][name] = lim

    out["ride_through_storage"] = (_names(where, "ride_through_storage", spec["ride_through_storage"])
                                   if "ride_through_storage" in spec else None)
    out["linearised_uc"] = _flag(where, "linearised_uc", spec)
    out["gfl_min_case"] = _enum(where, "gfl_min_case", spec.get("gfl_min_case", "drop"), GFL_MIN_CASES)
    return out


def check_horizon(requirements, period_hours):
    """The sustained ride-through must fit in the period it is checked over
    (plan I2a: the windows wrap cyclically within one period)."""
    d = requirements["values"]["sustained_h"]
    if d > period_hours:
        raise ContractError(f"requirements.sustained_h ({d:g} h) is longer than the period ({period_hours:g} h)")


def check_cross(units, requirements, where="island", strict=True):
    """The checks that need every unit at once: the names a UPS or the
    requirements point at are of the right kind, and the genset redundancy
    leaves at least one genset. Fills the default ``ride_through_storage``
    (every bess).

    ``strict`` (a campus file, which lists every unit) also refuses a name
    that is not a unit. The hub sidecar lists only the units it has data
    for, so it is checked with ``strict=False``: a name it does not list is
    left for the campus draft to check against the project."""
    kinds = {name: u["kind"] for name, u in units.items()}

    def wrong(names, kind):
        return [x for x in names if (strict or x in kinds) and kinds.get(x) != kind]

    for name, u in units.items():
        if u["kind"] == "ups":
            bad = wrong(u["it_load"], "load")
            if bad:
                raise ContractError(f"{where}.{name}.it_load names {bad}, which are not loads")
    rts = requirements["ride_through_storage"]
    if rts is None:
        requirements["ride_through_storage"] = sorted(n for n, k in kinds.items() if k == "bess")
    else:
        bad = wrong(rts, "bess")
        if bad:
            raise ContractError(f"{where}.requirements.ride_through_storage names {bad}, which are not bess units")
    n_gen = sum(k == "genset" for k in kinds.values())
    k = requirements["values"]["genset_redundancy_n"]
    if n_gen and k >= n_gen:
        raise ContractError(
            f"{where}.requirements.genset_redundancy_n ({k}) must be below the number of gensets ({n_gen}), "
            "or no genset is left to carry the load")


def check_island(cfg, critical_peak_mw):
    """The pickup blocks must cover the peak critical load, or the gensets
    never carry all of it under ``ups_bridged``."""
    req = cfg["requirements"]
    if "ups_bridged" in req["scenarios"]:
        total = sum(req["values"]["pickup_blocks"])
        if total < critical_peak_mw - 1e-9:
            raise ContractError(
                f"requirements.pickup_blocks add up to {total:g} MW, below the peak critical load "
                f"{critical_peak_mw:g} MW")


def validate_island_config(cfg) -> dict:
    """The hub sidecar ``island_config.json``: ``{"units": {pypsa_name:
    {"kind", ...}}, "requirements": {...}}``."""
    _allowed("island_config", cfg, ("units", "requirements", "preset"))
    _require("island_config", cfg, ("units", "requirements"))
    preset = _check_preset_name("island_config", cfg.get("preset"))
    if not isinstance(cfg["units"], dict):
        raise ContractError("island_config.units must be a mapping of PyPSA names")
    units = {}
    for name, spec in cfg["units"].items():
        where = f"island_config.units.{name}"
        if not isinstance(spec, dict) or "kind" not in spec:
            raise ContractError(f"{where}: missing 'kind'; allowed {list(UNIT_KINDS)}")
        body = {k: v for k, v in spec.items() if k != "kind"}
        units[str(name)] = validate_unit_island(where, spec["kind"], body, sidecar=True, preset=preset)
    req = validate_requirements(cfg["requirements"], "island_config.requirements")
    check_cross(units, req, "island_config", strict=False)
    out = {"units": units, "requirements": req}
    if preset is not None:
        out["preset"] = preset
    return out


# ── what a project offers the grid, and selecting a preset (ticket 04) ────

#: The services each control offers, as the hub island LP credits them. A
#: grid former and its inertia count on a bess only (the LP's E2/E3 former
#: set and its M); a GFM on another kind is listed as not credited.
_CONTROL_SERVICES = {
    "gfm_droop": ("grid_forming", "synthetic_inertia", "frequency_containment", "voltage_control"),
    "gfm_vsm": ("grid_forming", "synthetic_inertia", "frequency_containment", "voltage_control"),
    "gfl_fw": ("fast_frequency_response",),
    "gfl_qu": ("voltage_control",),
    "gfl_pf": (),
    "gfl_pq": (),
}
_GFM_NOT_CREDITED = "the hub island LP credits a grid former on a bess only"


def offered_services(cfg) -> dict:
    """The grid services a project's converters offer, from a validated
    sidecar (``validate_island_config``) or campus island (``{"units"}``).

    ``{"preset", "units": {name: {"kind", "control", "preset", "services",
    "not_credited"}}, "services"}``. Services follow each unit's effective
    control, so a sidecar without a preset answers too; ``services`` is the
    union over units. ``not_credited`` lists ``{"service", "reason"}`` for a
    mode the island study does not count (a GFM on pv, wind or a fuel cell).
    Generic across templates: it reads kinds and controls, nothing else."""
    units, union = {}, set()
    for name, u in cfg["units"].items():
        if u["kind"] not in CONVERTER_KINDS:
            continue
        offered = list(_CONTROL_SERVICES[u["control"]])
        not_credited = []
        if u["control"] in GFM_CONTROLS and u["kind"] != "bess":
            not_credited = [{"service": x, "reason": _GFM_NOT_CREDITED} for x in offered]
            offered = []
        units[name] = {"kind": u["kind"], "control": u["control"], "preset": u.get("preset"),
                       "services": offered, "not_credited": not_credited}
        union |= set(offered)
    return {"preset": cfg.get("preset"), "units": units, "services": [x for x in SERVICES if x in union]}


def has_grid_former(cfg) -> bool:
    """True when the island has something to form its voltage: a bess with a
    GFM control (what the LP credits) or a genset."""
    return any((u["kind"] == "bess" and u.get("control") in GFM_CONTROLS) or u["kind"] == "genset"
               for u in cfg["units"].values())


#: The fields a preset governs, per unit kind group: selecting a preset
#: clears them from a unit so the preset fills them. Everything else (a
#: UPS's it_load, a converter's neg_seq) stays.
_CONVERTER_GOVERNED = frozenset({"control", "pf_rated", "cos_phi", "cosphi_p_points",
                                 *(f for fs in _CONTROL_FIELDS.values() for f in fs)}) - set(FFR_SIZED)
_UPS_GOVERNED = frozenset({"walk_in_s", "f_window_hz", "transfers_on_islanding"})


def select_preset(raw, preset, ratings=None) -> tuple[dict, list[str]]:
    """The raw sidecar ``raw`` with project preset ``preset`` selected (None
    clears it), and the units it left out with the reason.

    Each converter of a kind the preset applies to, and each UPS, loses its
    own preset and the fields the preset governs, so the preset fills them.
    Under ``fast_frequency_response`` a bess without its rating-scaled
    fields gets them from ``ffr_sizing(ratings[name])``; with no positive
    rating (an extendable unit before sizing) it keeps its own block,
    marked ``"preset": null``, and is listed. Pure: ``raw`` is not changed."""
    _check_preset_name("island_config", preset)
    out = copy.deepcopy(raw)
    ratings = ratings or {}
    skipped = []
    if preset is None:
        out.pop("preset", None)
        return out, skipped
    out["preset"] = preset
    for name, u in out.get("units", {}).items():
        kind = u.get("kind")
        block = preset_block(preset, kind) if kind in (*CONVERTER_KINDS, "ups") else None
        if block is None:
            continue
        governed = _UPS_GOVERNED if kind == "ups" else _CONVERTER_GOVERNED
        if block.get("control") == "gfl_fw" and not all(f in u for f in FFR_SIZED):
            rating = ratings.get(name)
            if rating is None or not rating > 0:
                u["preset"] = None
                skipped.append(f"{name}: {preset} needs the unit's rating, and it has none yet "
                               "(an extendable unit before sizing); it keeps its own block and offers no FFR")
                continue
            u.update(ffr_sizing(rating))
        for f in governed:
            u.pop(f, None)
        u.pop("preset", None)
        if block.get("control") != "gfl_fw":
            for f in FFR_SIZED:
                u.pop(f, None)
    return out, skipped


# ── the hourly island contracts (plan I3a; owner option A, 2026-10-13) ──────
#
# gridspine owns the column sets and the validators; pypsa-gui produces the
# values from its solved hub LP (``services/solver/island.py``) and writes
# them beside ``campus_hourly.csv``. ``period`` and ``hour`` are keyed as in
# ``schema/campus.py`` (the investment period, the snapshot's position in
# it). Validators take a DataFrame and use only its own methods, so this
# module stays pandas-free.

#: One row per (period, hour, unit_id): gensets, bess and UPS units.
#: ``soc_start_mwh`` is e(t−1), the energy held when an outage starts in hour
#: t (storage only). ``online`` is u_g(t) under linearised UC, else the
#: ``spinning`` flag (gensets), 1 for a bess, empty on a UPS.
#: ``p_ups_it_mw`` is the IT load a UPS protects, empty on other units; a
#: UPS row has no headroom.
ISLAND_UNIT_HOURLY_COLUMNS = ("period", "hour", "unit_id", "soc_start_mwh", "online",
                              "headroom_up_mw", "headroom_down_mw", "p_ups_it_mw")
#: One row per (period, hour, scenario): the steps ΔP⁺ and ΔP⁻ (MW, positive
#: when there is a step that way), M (MW·s), ΣK (MW/Hz) and the critical load.
ISLAND_HOURLY_COLUMNS = ("period", "hour", "scenario", "dp_up_mw", "dp_down_mw",
                         "inertia_mws", "k_mw_per_hz", "critical_load_mw")
ISLAND_UNIT_HOURLY_CSV = "island_unit_hourly.csv"
ISLAND_HOURLY_CSV = "island_hourly.csv"


def _empty(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _fixed_columns(df, cols, where):
    have = [str(c) for c in df.columns]
    missing = [c for c in cols if c not in have]
    if missing:
        raise ContractError(f"{where} missing columns: {missing}")
    extra = [c for c in have if c not in cols]
    if extra:
        raise ContractError(f"{where} has columns the contract does not carry: {extra}")
    return df[list(cols)].reset_index(drop=True).copy()


def _keys(out, key, where):
    for col in ("period", "hour"):
        vals = []
        for v in out[col].tolist():
            if isinstance(v, bool) or _empty(v) or not isinstance(v, (int, float)) or float(v) % 1:
                raise ContractError(f"{where}: {col} must be integral, got {v!r}")
            if v < 0:
                raise ContractError(f"{where}: {col} must be non-negative, got {v!r}")
            vals.append(int(v))
        out[col] = vals
    rows = list(zip(*(out[c].tolist() for c in key)))
    seen, dup = set(), []
    for r in rows:
        if r in seen:
            dup.append(r)
        seen.add(r)
    if dup:
        raise ContractError(f"{where}: duplicate {key} rows: {dup[:5]}")


def _numbers(out, col, where, *, required):
    vals = []
    for i, v in enumerate(out[col].tolist()):
        if _empty(v):
            if required(i):
                raise ContractError(f"{where}: {col} is empty on row {i}")
            vals.append(math.nan)
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ContractError(f"{where}: {col} must be a finite number, got {v!r} on row {i}")
        vals.append(float(v))
    out[col] = vals
    return vals


def validate_island_unit_hourly(df):
    """``island_unit_hourly.csv``, checked: exactly its columns, unique
    (period, hour, unit_id), finite numbers, ``online`` in [0, 1], and
    headroom on every row but a UPS's (the rows that carry ``p_ups_it_mw``)."""
    where = "island unit hourly table"
    out = _fixed_columns(df, ISLAND_UNIT_HOURLY_COLUMNS, where)
    if any(_empty(v) for v in out["unit_id"].tolist()):
        raise ContractError(f"{where}: null unit_id")
    out["unit_id"] = [str(v) for v in out["unit_id"].tolist()]
    _keys(out, ("period", "hour", "unit_id"), where)
    ups = [not _empty(v) for v in out["p_ups_it_mw"].tolist()]
    _numbers(out, "p_ups_it_mw", where, required=lambda i: False)
    _numbers(out, "soc_start_mwh", where, required=lambda i: False)
    for col in ("headroom_up_mw", "headroom_down_mw"):
        _numbers(out, col, where, required=lambda i: not ups[i])
    for i, v in enumerate(_numbers(out, "online", where, required=lambda i: False)):
        if not math.isnan(v) and not 0.0 <= v <= 1.0 + 1e-9:
            raise ContractError(f"{where}: online must be in [0, 1], got {v!r} on row {i}")
    return out


def validate_island_hourly(df):
    """``island_hourly.csv``, checked: exactly its columns, unique
    (period, hour, scenario), a known scenario, and every value finite."""
    where = "island hourly table"
    out = _fixed_columns(df, ISLAND_HOURLY_COLUMNS, where)
    bad = sorted({str(v) for v in out["scenario"].tolist() if v not in SCENARIOS})
    if bad:
        raise ContractError(f"{where}: scenario must be one of {list(SCENARIOS)}, got {bad}")
    _keys(out, ("period", "hour", "scenario"), where)
    for col in ISLAND_HOURLY_COLUMNS[3:]:
        _numbers(out, col, where, required=lambda i: True)
    return out
