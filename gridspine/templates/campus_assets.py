"""The campus asset library: what the electrical study may buy, and what it
costs (plan C7).

The library is a catalogue of transformers, cables, capacitor banks, shunt
reactors, STATCOMs and switchgear. Every entry has a unique ``id``, its
ratings, and a capex, a fixed opex (``opex_frac`` of capex per year) and a
lifetime. The library has one ``discount_rate``, a currency and a price year,
so an asset's yearly cost is the one the capacity expansion uses: capex
annualised, plus opex.

**Every number is ``{value, source}``**, with ``source`` one of ``measured``,
``datasheet`` or ``assumed`` (optionally a ``note``), as in a campus file
(``ingest/campus.py``) and for the same reason: a report must say how sure it
is of each figure. An untagged number is refused rather than defaulted.
The shipped file tags every figure ``assumed``; its costs are
order-of-magnitude placeholders.

Refused at load, each with a message naming the entry and the field:

* an unknown kind, entry field or tag key, or a missing field;
* an untagged value, an unknown source, a value that is not a finite number;
* a non-positive rating or cost (``opex_frac`` may be 0, and so may the
  no-load losses ``pfe_kw`` and ``i0_percent`` and the cable's ``c_nf_per_km``,
  as in a campus file), a lifetime under one year;
* a discount rate outside [0, 1);
* a duplicate id, across all kinds;
* a transformer whose hv_kv is not above its lv_kv, or whose vkr_percent is
  not below its vk_percent;
* a capacitor bank whose ``steps`` is not a whole number.

``annuity`` is the same formula as pypsa-gui's ``_annuity``; gridspine does
not import from pypsa-gui, so a test restates it instead.

yaml only, like the rest of ``templates/``; never the unsafe full ``yaml.load``.
"""
import math
from pathlib import Path

import yaml

from gridspine.schema.contracts import ContractError

DEFAULT_PATH = Path(__file__).parent / "data" / "campus_assets.yaml"
SOURCES = frozenset({"measured", "datasheet", "assumed"})

_COST = ("capex_eur", "opex_frac", "lifetime_a")
_FIELDS = {
    "transformers": ("hv_kv", "lv_kv", "s_mva", "vk_percent", "vkr_percent", "pfe_kw", "i0_percent"),
    "cables": ("vn_kv", "cross_section_mm2", "r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka"),
    "capacitor_banks": ("vn_kv", "q_mvar", "steps"),
    "shunt_reactors": ("vn_kv", "q_mvar"),
    "statcoms": ("vn_kv", "q_mvar", "losses_percent"),
    "switchgear": ("vn_kv", "ik_rated_ka", "ip_rated_ka"),
}
#: Every field an entry of each kind carries. A cable is priced per km.
REQUIRED = {k: (*f, *(("capex_eur_per_km", *_COST[1:]) if k == "cables" else _COST)) for k, f in _FIELDS.items()}
KINDS = tuple(_FIELDS)
_MAY_BE_ZERO = frozenset({"pfe_kw", "i0_percent", "c_nf_per_km", "opex_frac"})
_TOP = frozenset({"discount_rate", "currency", "price_year", *KINDS})
_TAG_KEYS = frozenset({"value", "source", "note"})


def value(field):
    """The number of a tagged ``{value, source}`` field."""
    return field["value"]


def _tagged(where, field, spec):
    if field not in spec:
        raise ContractError(f"{where}: missing {field}")
    t = spec[field]
    if not isinstance(t, dict) or "value" not in t or "source" not in t:
        raise ContractError(f"{where}.{field} must be a mapping with 'value' and 'source', got {t!r}")
    unknown = sorted(set(t) - _TAG_KEYS)
    if unknown:
        raise ContractError(f"{where}.{field}: unknown key(s) {unknown}; allowed {sorted(_TAG_KEYS)}")
    if t["source"] not in SOURCES:
        raise ContractError(f"{where}.{field} has unknown source {t['source']!r}; allowed {sorted(SOURCES)}")
    v = t["value"]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ContractError(f"{where}.{field} value must be a finite number, got {v!r}")
    return v


def _check_entry(kind, i, e, seen):
    if not isinstance(e, dict):
        raise ContractError(f"{kind}[{i}]: expected a mapping, got {type(e).__name__}")
    eid = e.get("id")
    if not isinstance(eid, str) or not eid:
        raise ContractError(f"{kind}[{i}]: needs a non-empty string id")
    where = f"{kind}[{eid}]"
    if eid in seen:
        raise ContractError(f"{where}: duplicate id {eid!r} (also in {seen[eid]})")
    seen[eid] = where
    unknown = sorted(set(e) - set(REQUIRED[kind]) - {"id", "note"})
    if unknown:
        raise ContractError(f"{where}: unknown field(s) {unknown}; allowed {sorted(set(REQUIRED[kind]) | {'id', 'note'})}")
    v = {f: _tagged(where, f, e) for f in REQUIRED[kind]}
    for f, x in v.items():
        if x < 0 or (x == 0 and f not in _MAY_BE_ZERO):
            raise ContractError(f"{where}.{f} must be {'non-negative' if f in _MAY_BE_ZERO else 'positive'}, got {x}")
    if v["lifetime_a"] < 1:
        raise ContractError(f"{where}.lifetime_a must be at least 1 year, got {v['lifetime_a']}")
    if kind == "transformers":
        if not v["hv_kv"] > v["lv_kv"]:
            raise ContractError(f"{where}: hv_kv ({v['hv_kv']}) must be above lv_kv ({v['lv_kv']})")
        if not v["vkr_percent"] < v["vk_percent"]:
            raise ContractError(f"{where}: vkr_percent ({v['vkr_percent']}) must be below vk_percent ({v['vk_percent']})")
    if kind == "capacitor_banks" and not isinstance(v["steps"], int):
        raise ContractError(f"{where}.steps must be a whole number, got {v['steps']!r}")


def _check_library(data):
    unknown = sorted(set(data) - _TOP)
    if unknown:
        raise ContractError(f"asset library: unknown key(s) {unknown}; allowed {sorted(_TOP)}")
    rate = _tagged("library", "discount_rate", data)
    if not 0 <= rate < 1:
        raise ContractError(f"library.discount_rate must lie in [0, 1), got {rate}")
    cur = data.get("currency")
    if not isinstance(cur, str) or not cur:
        raise ContractError(f"library: currency must be a non-empty string, got {cur!r}")
    year = data.get("price_year")
    if isinstance(year, bool) or not isinstance(year, int) or year < 1:
        raise ContractError(f"library: price_year must be a positive whole number, got {year!r}")
    seen = {}
    for kind in KINDS:
        if kind not in data:
            raise ContractError(f"asset library: missing {kind!r}")
        if not isinstance(data[kind], list):
            raise ContractError(f"asset library: {kind} must be a list of entries, got {type(data[kind]).__name__}")
        for i, e in enumerate(data[kind]):
            _check_entry(kind, i, e, seen)


def load_asset_library(path=None) -> dict:
    """The library at ``path`` (default: the shipped one), validated. The
    tagged structure is returned as written, so ``value(entry["s_mva"])``
    reads a number and ``entry["s_mva"]["source"]`` its tag."""
    path = Path(path or DEFAULT_PATH)
    if not path.is_file():
        raise ContractError(f"asset library file not found: {path}")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ContractError("the asset library file must be a mapping")
    _check_library(data)
    return data


def annuity(rate: float, lifetime_a: float) -> float:
    """The capital recovery factor ``r / (1 - (1 + r)^-n)``, or ``1 / n`` at
    ``r == 0``: the yearly payment per unit of capex."""
    if lifetime_a <= 0:
        raise ValueError(f"lifetime must be positive, got {lifetime_a}")
    if not 0 <= rate < 1:
        raise ValueError(f"rate must lie in [0, 1), got {rate}")
    if rate == 0:
        return 1.0 / lifetime_a
    return rate / (1.0 - (1.0 + rate) ** -lifetime_a)


def annualised_cost(entry: dict, library: dict, length_km=None) -> float:
    """Capex x annuity + capex x ``opex_frac``, in the library's currency per
    year. A cable is priced per km, so it needs ``length_km``."""
    if "capex_eur_per_km" in entry:
        if length_km is None or not length_km > 0:
            raise ValueError(f"{entry['id']}: a cable needs a positive length_km, got {length_km!r}")
        capex = value(entry["capex_eur_per_km"]) * length_km
    else:
        capex = value(entry["capex_eur"])
    return capex * annuity(value(library["discount_rate"]), value(entry["lifetime_a"])) + capex * value(entry["opex_frac"])


def library_entries(library: dict, kind: str, **filters) -> list:
    """The entries of ``kind`` whose fields equal every filter, e.g.
    ``library_entries(lib, "transformers", hv_kv=132, lv_kv=33)``. A filter on
    a field the kind does not have is refused, not silently empty."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; allowed {list(KINDS)}")
    for f in filters:
        if f != "id" and f not in REQUIRED[kind]:
            raise ValueError(f"{kind} have no field {f!r}; allowed {['id', *REQUIRED[kind]]}")

    def match(e):
        return all(e["id"] == x if f == "id" else math.isclose(value(e[f]), x, rel_tol=1e-9)
                   for f, x in filters.items())
    return [e for e in library[kind] if match(e)]
