"""The investment case's flags in plain words: the site-today baseline's
(`services.results.baseline` plans the baseline; the finance report states its
flags, so the wording lives here, on the finance side of the tripwire) and the
growth placeholders of an example built for entered planning years (E1)."""
from __future__ import annotations

_PLAIN = {
    "baseline_typing_mismatch": "{a} has no typed cost; treated as a new investment. "
                                "Type overnight 0 if it already exists.",
    "baseline_free_capacity": "{a} adds capacity typed at no cost (overnight 0); the baseline "
                              "removes the addition, so the case gets its value for free. "
                              "Type its cost.",
    "baseline_island_rows_relaxed": "The site as it stands cannot meet the island requirements "
                                    "without the new builds, so it is compared without them: "
                                    "the island capability is part of what the investment buys.",
    "counterfactual_baseline_unserved": "The site as it stands today cannot serve {a} "
                                        "without the new builds, and no reference purchase "
                                        "prices it: the comparison is not established.",
    "counterfactual_baseline_shed_unknown": "Whether the site as it stands today serves {a} is "
                                            "not known (no record of its shed), so the "
                                            "comparison is not established.",
    "co2_price_not_applied": "The CO2 price cannot be applied to this comparison, so the "
                             "avoided CO2 cost is not established.",
}

#: ``growth_placeholder:<kind>_<year>`` (``routers.results._ic_growth_flags``):
#: the growth input in words.
_GROWTH_LABELS = {"it_mw": "The IT load", "energized_mw": "The energized import",
                  "h2_offtake_mw": "The H2 offtake", "load_scale": "The load growth",
                  "nh3_delivery_tpd": "The NH3 delivery"}
_GROWTH_TEXT = ("{label} in {year} is a placeholder (the example's value for the same stage, "
                "not set for this year): the plan and this case rest on it.")


def flag_text(flag: str) -> str | None:
    """A baseline or growth-placeholder flag in plain words for the IC view;
    None for any other flag."""
    code, _sep, asset = flag.partition(":")
    if code == "growth_placeholder":
        kind, _sep, year = asset.rpartition("_")
        return _GROWTH_TEXT.format(label=_GROWTH_LABELS.get(kind, kind), year=year)
    tpl = _PLAIN.get(code)
    return tpl.format(a=asset) if tpl else None
