"""Dynamic parameter templates — the assumptions ledger's mechanism.

Every value carries a provenance tag, ``measured | datasheet | assumed``, and
in the v2 form the tag is PER FIELD. The classic IEEE 39-bus dynamic set
(Athay et al. 1979 / Pai 1989) is a two-axis dataset: it gives H, Xd, Xq,
X'd, X'q, Xl, T'do and T'qo. It gives NO subtransient reactances or time
constants and NO saturation coefficients — yet a GENROU record needs them.
Those are assumed. A single unit-level tag would launder ``assumed`` into
``datasheet`` across a record, which is exactly what the report appendix
exists to make impossible, so a v2 unit with a unit-level ``source`` is
rejected outright.

Two views of one file:

``load_unit_templates(path)`` -> ``UnitTemplates``
    ``units``: index ``unit_id``; columns ``model``, ``mbase_mva``,
    ``include_in_inertia``. ``params``: long form, one row per
    ``(unit_id, param)`` with ``value`` and ``source``. Every unit, every
    field, every tag. This is what the .dyr writer and the ledger README read.

``load_unit_params(path)`` -> DataFrame
    The increment-2 H-parameter frame, unchanged in shape: index ``unit_id``
    over the SYNCHRONOUS units only; ``h_s``, ``mbase_mva``,
    ``include_in_inertia`` and ``source`` — where ``source`` is the
    provenance of ``h_s`` — plus the model class and the wide numeric
    columns. Inverters are absent on purpose: an inverter has no H, and
    ``ranking.metrics`` fills an absent unit with zero inertia, which is the
    correct physics rather than a fallback. This is what ``ranking/`` and the
    driver's "unit H params" ledger line read.

Model classes and their required parameters are in ``MODEL_PARAMS``. A unit
missing a field its class requires RAISES — a reactance defaulted to zero
would import into PSS/E cleanly and produce a plausible, wrong swing curve.
A field the class does not know is also rejected: a typo'd name would
otherwise be silently dropped while the required one was reported missing.

The v1 flat form ``{h_s, mbase_mva, source, include_in_inertia}`` stays legal
and loads as model ``legacy``. A flat unit carries exactly one parameter, so
its one tag is already per field.

yaml/pandas only — no engine import, and never the unsafe full ``yaml.load``.
"""
import dataclasses
import math
from pathlib import Path

import pandas as pd
import yaml

from gridspine.schema.contracts import ContractError

_DEFAULT = Path(__file__).parent / "data" / "case39_units.yaml"
SOURCES = frozenset({"measured", "datasheet", "assumed"})

# PSS/E record field sets. GENSAL is the salient-pole machine: no q-axis
# transient (no X'q, no T'qo). Inverters carry the two short-circuit
# parameters IEC 60909 needs for a converter-interfaced source — the
# max-current ratio Ik''/In and the R/X ratio — named to map onto pandapower's
# sgen ``k`` and ``rx`` columns (task 6 consumes them).
MODEL_PARAMS = {
    "GENROU": (
        "h_s", "d", "xd", "xq", "xd_p", "xq_p", "xd_pp", "xl",
        "t_do_p", "t_qo_p", "t_do_pp", "t_qo_pp", "s1", "s12",
    ),
    "GENSAL": (
        "h_s", "d", "xd", "xq", "xd_p", "xd_pp", "xl",
        "t_do_p", "t_do_pp", "t_qo_pp", "s1", "s12",
    ),
    "inverter": ("k_sc", "rx_sc"),
    "legacy": ("h_s",),
}
SYNCHRONOUS_MODELS = frozenset({"GENROU", "GENSAL", "legacy"})

#: Parameters a model MAY carry beyond its dynamic record: the IEC 60909 data
#: for synchronous machines (R/X ratio, rated power factor). Optional here so
#: the .dyr layout and its fixtures are untouched; ``static/shortcircuit`` asserts
#: their presence itself, element by element, before it solves.
MODEL_OPTIONAL_PARAMS = {
    "GENROU": ("rx_sc", "cos_phi"),
    "GENSAL": ("rx_sc", "cos_phi"),
    "inverter": (),
    "legacy": (),
}
_V2_MODELS = frozenset(MODEL_PARAMS) - {"legacy"}

_STRICTLY_POSITIVE = frozenset({
    "xd", "xq", "xd_p", "xq_p", "xd_pp", "xl",
    "t_do_p", "t_qo_p", "t_do_pp", "t_qo_pp", "k_sc",
})
_NON_NEGATIVE = frozenset({"d", "s1", "s12", "rx_sc"})
_UNIT_INTERVAL = frozenset({"cos_phi"})  # 0 < value <= 1

_V1_REQUIRED = ("h_s", "mbase_mva", "source", "include_in_inertia")
_V2_KEYS = frozenset({"model", "mbase_mva", "include_in_inertia", "params"})


@dataclasses.dataclass
class UnitTemplates:
    units: pd.DataFrame    # index unit_id; model, mbase_mva, include_in_inertia
    params: pd.DataFrame   # unit_id, param, value, source


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _parse_v1(uid, raw):
    missing = [k for k in _V1_REQUIRED if k not in raw or raw[k] is None]
    if missing:
        raise ContractError(f"unit params missing/null columns: {missing} (unit {uid})")
    if raw["source"] not in SOURCES:
        raise ContractError(
            f"unknown source tags {[raw['source']]}; allowed {sorted(SOURCES)} (unit {uid})"
        )
    if not _is_number(raw["h_s"]):
        raise ContractError(f"unit {uid}: h_s must be a finite number, got {raw['h_s']!r}")
    return "legacy", {"h_s": (float(raw["h_s"]), raw["source"])}


def _parse_v2(uid, raw):
    model = raw.get("model")
    if model not in _V2_MODELS:
        raise ContractError(
            f"unit {uid}: unknown model {model!r}; allowed {sorted(_V2_MODELS)}"
        )
    if "source" in raw:
        raise ContractError(
            f"unit {uid}: 'source' at unit level is not allowed; provenance is "
            "tagged per field in 'params'"
        )
    stray = sorted(set(raw) - _V2_KEYS)
    if stray:
        raise ContractError(
            f"unit {uid}: fields outside 'params' carry no provenance tag: {stray}"
        )
    params = raw.get("params")
    if not isinstance(params, dict) or not params:
        raise ContractError(f"unit {uid}: 'params' must be a non-empty mapping")
    required = MODEL_PARAMS[model]
    missing = [p for p in required if p not in params]
    if missing:
        raise ContractError(f"unit {uid} ({model}) missing required params: {missing}")
    allowed = set(required) | set(MODEL_OPTIONAL_PARAMS[model])
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ContractError(f"unit {uid} ({model}) has params not in the model: {unknown}")
    parsed = {}
    for name, spec in params.items():
        if not isinstance(spec, dict) or set(spec) != {"value", "source"}:
            raise ContractError(
                f"unit {uid}: param {name} must be {{value, source}}, got {spec!r}"
            )
        if spec["source"] not in SOURCES:
            raise ContractError(
                f"unit {uid}: param {name} has unknown source {spec['source']!r}; "
                f"allowed {sorted(SOURCES)}"
            )
        if not _is_number(spec["value"]):
            raise ContractError(
                f"unit {uid}: param {name} must be a finite number, got {spec['value']!r}"
            )
        parsed[name] = (float(spec["value"]), spec["source"])
    return model, parsed


def _check_physics(uid, model, values, include_in_inertia, mbase):
    for name, v in values.items():
        if name in _STRICTLY_POSITIVE and v <= 0:
            raise ContractError(f"unit {uid}: {name} must be > 0, got {v}")
        if name in _NON_NEGATIVE and v < 0:
            raise ContractError(f"unit {uid}: {name} must be >= 0, got {v}")
        if name in _UNIT_INTERVAL and not (0 < v <= 1):
            raise ContractError(f"unit {uid}: {name} must lie in (0, 1], got {v}")
    if not _is_number(mbase):
        raise ContractError(f"unit {uid}: mbase_mva must be a finite number, got {mbase!r}")
    if model == "inverter":
        if include_in_inertia:
            raise ContractError(
                f"unit {uid}: an inverter cannot have include_in_inertia=True"
            )
        if mbase <= 0:
            raise ContractError(f"unit {uid}: mbase_mva must be > 0, got {mbase}")
        return
    needs_h = include_in_inertia or model in ("GENROU", "GENSAL")
    if needs_h and (values["h_s"] <= 0 or mbase <= 0):
        raise ContractError(
            f"h_s and mbase_mva must be positive for inertia-counted units (unit {uid})"
        )
    if model in ("GENROU", "GENSAL"):
        xd, xd_p, xd_pp, xl = values["xd"], values["xd_p"], values["xd_pp"], values["xl"]
        if not (xd > xd_p > xd_pp > xl > 0):
            raise ContractError(
                f"unit {uid}: reactance ordering xd > xd_p > xd_pp > xl > 0 violated: "
                f"xd={xd}, xd_p={xd_p}, xd_pp={xd_pp}, xl={xl}"
            )
        xq = values["xq"]
        if model == "GENROU":
            if not (xq > values["xq_p"] > xd_pp):
                raise ContractError(
                    f"unit {uid}: q-axis ordering xq > xq_p > xd_pp violated: "
                    f"xq={xq}, xq_p={values['xq_p']}, xd_pp={xd_pp}"
                )
        elif not (xq > xd_pp):
            raise ContractError(
                f"unit {uid}: xq must exceed xd_pp: xq={xq}, xd_pp={xd_pp}"
            )


def load_unit_templates(path=None) -> UnitTemplates:
    raw = yaml.safe_load(Path(path or _DEFAULT).read_text())
    units = raw.get("units") if isinstance(raw, dict) else None
    if not isinstance(units, dict) or not units:
        raise ContractError("unit params YAML has no 'units' mapping")

    unit_rows, param_rows = [], []
    for uid, spec in units.items():
        if not isinstance(spec, dict):
            raise ContractError(f"unit {uid}: expected a mapping, got {type(spec).__name__}")
        if "model" in spec or "params" in spec:
            model, parsed = _parse_v2(uid, spec)
        else:
            model, parsed = _parse_v1(uid, spec)
        if "include_in_inertia" not in spec or not isinstance(spec["include_in_inertia"], bool):
            raise ContractError(f"unit {uid}: include_in_inertia must be a bool")
        include = spec["include_in_inertia"]
        mbase = spec.get("mbase_mva")
        _check_physics(uid, model, {k: v for k, (v, _s) in parsed.items()}, include, mbase)
        unit_rows.append({
            "unit_id": uid, "model": model,
            "mbase_mva": float(mbase), "include_in_inertia": include,
        })
        for name, (value, source) in parsed.items():
            param_rows.append({"unit_id": uid, "param": name, "value": value, "source": source})

    units_df = pd.DataFrame(unit_rows).set_index("unit_id")
    if units_df.index.duplicated().any():
        raise ContractError(
            f"duplicate unit ids: {sorted(units_df.index[units_df.index.duplicated()])}"
        )
    params_df = pd.DataFrame(param_rows, columns=["unit_id", "param", "value", "source"])
    return UnitTemplates(units=units_df, params=params_df)


def load_unit_params(path=None) -> pd.DataFrame:
    """The synchronous-fleet H view — see the module docstring for why inverters are absent."""
    t = load_unit_templates(path)
    sync = t.units[t.units["model"].isin(SYNCHRONOUS_MODELS)]
    wide = (
        t.params[t.params["unit_id"].isin(sync.index)]
        .pivot(index="unit_id", columns="param", values="value")
    )
    h_source = (
        t.params[t.params["param"] == "h_s"].set_index("unit_id")["source"]
    )
    df = sync.join(wide, how="left")
    df["source"] = df.index.map(h_source)
    df.index.name = "unit_id"
    front = ["h_s", "mbase_mva", "source", "include_in_inertia", "model"]
    rest = [c for c in df.columns if c not in front]
    return df[front + rest]


def provenance_counts(templates: UnitTemplates) -> pd.Series:
    """How many (unit, param) values carry each tag — the ledger's headline."""
    counts = templates.params["source"].value_counts()
    return counts.reindex(sorted(SOURCES), fill_value=0).astype("int64")
