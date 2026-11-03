"""LC-03 slice 2: why a run's section was kept as a `not_kept` stub.

One constant per reason. The digest that cannot be built returns it live, the
capture stores it in the stub, and every past-run read names it, so the live
failure and the past-run refusal say the same thing (GK B condition (j)).
"""
from __future__ import annotations

NOT_KEPT = 'not_kept'

# A digest (results/dispatch_digest, results/prices_digest) of a multi-period network.
MULTI_PERIOD_NOT_DIGESTED = 'multi_period_not_digested'
# The prices digest when the solve left no prices.
NO_PRICES = 'no_prices'
# A section larger than run_evidence.MAX_SECTION_BYTES; values are never trimmed.
OVER_SECTION_CAP = 'over_section_cap'

SECTION_CAP_KB = 16

# Each reason as a read says it, after "kept no ... for this run: ".
WHY: dict[str, str] = {
    MULTI_PERIOD_NOT_DIGESTED: "a multi-period run's dispatch and prices are not digested",
    NO_PRICES: 'the solve left no prices to keep',
    OVER_SECTION_CAP: (f'the section was over the {SECTION_CAP_KB} KB cap a run keeps, '
                       'and its values are never trimmed'),
}


def why(reason: str | None) -> str:
    return WHY.get(reason or '', 'it could not be kept')
