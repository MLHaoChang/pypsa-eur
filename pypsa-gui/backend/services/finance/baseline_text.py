"""The site-today baseline's flags in plain words (`services.results.baseline`
plans the baseline; the finance report states its flags, so the wording lives
here, on the finance side of the tripwire)."""
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
    # H2e's inputs act on the analytic comparison only (merge of #302 into #305).
    "reference_purchases_not_applied": "The reference purchases (grey hydrogen, say) are not "
                                       "applied: the site as it stands today is re-solved, not "
                                       "assumed to buy them.",
    "co2_price_not_applied": "The CO2 price is not applied to this comparison, so the avoided "
                             "CO2 cost is not established.",
}


def flag_text(flag: str) -> str | None:
    """A baseline flag in plain words for the IC view; None for any other flag."""
    code, _sep, asset = flag.partition(":")
    tpl = _PLAIN.get(code)
    return tpl.format(a=asset) if tpl else None
