"""
A process's recipe as Guided section 2 shows it (template-e2e gap 03): the
inputs per unit of product, read from the Link's efficiencies.

A recipe Link is a process unit (``eh_process``) or an electrolyser
(``eh_role == "electrolyser"``). bus0 carries the electricity, bus1 the
product, bus2..bus4 the inputs it draws (a negative ``efficiencyN``), as
``eh_ammonia`` builds ``haber_bosch`` and ``utils/processUnit.ts`` creates one:

    electricity per unit of product = 1 / efficiency
    input N per unit of product     = -efficiencyN / efficiency

A product bus that counts in tonnes (or carries ``mwh_per_t``) is shown per
tonne, with electricity in kWh/t; any other product per MWh, electricity in
MWh/MWh. Display only: the writer is the Link's own asset update.
"""

from __future__ import annotations

import math
import re

import numpy as np

from services.process_products import bus_unit, t_per_unit

_PORTS = (2, 3, 4)


def _num(value) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _cell(df, row, col):
    return df.at[row, col] if col in df.columns and row in df.index else None


def _flag(value) -> bool:
    """A flag counts only when exactly true (the #1083 rule)."""
    return isinstance(value, (bool, np.bool_)) and bool(value)


def is_recipe_link(n, link: str) -> bool:
    return (
        _flag(_cell(n.links, link, "eh_process"))
        or str(_cell(n.links, link, "eh_role") or "") == "electrolyser"
    )


def _carrier_label(n, carrier: str) -> str:
    nice = str(_cell(n.carriers, carrier, "nice_name") or "").strip()
    return nice if nice and nice != "nan" else carrier


def _bus_carrier(n, bus: str) -> str:
    c = str(_cell(n.buses, bus, "carrier") or "").strip()
    return c if c and c != "nan" else bus


def recipe_label(n, link: str) -> str:
    """The row's name in plain words, never the Link's id: an electrolyser is
    "Electrolyser" (with its build year where the id carries one), any other
    process its carrier's display name."""
    if str(_cell(n.links, link, "eh_role") or "") == "electrolyser":
        year = re.search(r"_(\d{4})$", link)
        return f"Electrolyser, built {year.group(1)}" if year else "Electrolyser"
    carrier = str(_cell(n.links, link, "carrier") or "").strip()
    return _carrier_label(n, carrier) if carrier and carrier != "nan" else "Process"


def link_recipe(n, link: str) -> dict | None:
    """The recipe, or None where the Link has none a card can show: not a
    recipe Link, an efficiency that is not above 0, or a second output (a
    positive port efficiency)."""
    if link not in n.links.index or not is_recipe_link(n, link):
        return None
    eff = _num(_cell(n.links, link, "efficiency"))
    if eff is None or eff <= 0:
        return None
    product_bus = str(n.links.at[link, "bus1"])
    tpu = t_per_unit(n, product_bus)
    per_t = tpu is not None
    # Units of the product bus per unit the recipe counts in.
    product_per = 1.0 / tpu if per_t else 1.0
    inputs = []
    for i in _PORTS:
        bus = str(_cell(n.links, link, f"bus{i}") or "").strip()
        e = _num(_cell(n.links, link, f"efficiency{i}"))
        if not bus or bus == "nan" or e is None or e == 0:
            continue
        if e > 0:
            return None
        carrier = _bus_carrier(n, bus)
        inputs.append(
            {
                "port": i,
                "carrier": carrier,
                "label": _carrier_label(n, carrier),
                "value": -e * product_per / eff,
                "unit": bus_unit(n, bus) or "MWh",
            }
        )
    product_carrier = _bus_carrier(n, product_bus)
    el = product_per / eff
    return {
        "label": recipe_label(n, link),
        "product": {
            "carrier": product_carrier,
            "label": _carrier_label(n, product_carrier),
        },
        "per": "t" if per_t else "MWh",
        # Units of the product bus per recipe unit: what the writer turns rates back into efficiencies with.
        "product_per": product_per,
        "electricity": {
            "value": el * 1000.0 if per_t else el,
            "unit": "kWh" if per_t else "MWh",
        },
        "inputs": inputs,
    }


def recipe_links(n) -> list[str]:
    return [str(x) for x in n.links.index if link_recipe(n, str(x)) is not None]
