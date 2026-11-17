"""
Background harmonic voltage measured at the point of connection (TRGC
13/14, power quality with the field background).

The harmonic screen (``static/harmonics.py``) gives the plant's own
contribution U_h at the PoC. The grid is not clean before the plant
connects: a field measurement gives the voltage harmonics already there.
This module reads that measurement and adds it to the plant's per IEC
61000-3-6's general summation law, the same law and exponents the screen
uses between units:

    U_h,total = (U_h,bg ** alpha + U_h,plant ** alpha) ** (1 / alpha)

with ``alpha`` = 1 below order 5, 1.4 from 5 to 10 and 2 above 10.

Input: a CSV in the style of an IEC 61000-4-30 power-quality report, one row
per order, ``order`` and ``u_h_pct_p95`` (the 95th-percentile 10-minute value
over the survey, in % of the nominal voltage). Every order the file names
must carry a finite value >= 0; an order is an integer 2..50, each once. An
order the file does not name is "not measured", never taken as zero: the
combined row says which orders had no background.

The record keeps where the numbers came from (``source``, ``measured_on``,
the file name and its sha256) so a combined row can cite them.

Pure pandas; no engine import.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import math
from pathlib import Path

import pandas as pd

from gridspine.schema.contracts import ContractError
from gridspine.static.harmonics import H_MAX, H_MIN, alpha

BACKGROUND_JSON = "background_harmonics.json"
ORDER_COL = "order"
VALUE_COL = "u_h_pct_p95"
STATISTIC = "95th percentile of 10-minute values (IEC 61000-4-30 style)"
WITH_BACKGROUND = "with background"


def parse_background(
    data, *, poc: str, source: str, measured_on, filename: str | None = None
) -> dict:
    """
    The validated measurement record from CSV ``data`` (bytes, text or a
    ``Path``; a ``str`` is always CSV text). ``source`` (who measured, which instrument) and ``measured_on``
    (an ISO date) are required: a background without provenance cannot be
    cited.
    """
    if isinstance(data, Path):
        filename = filename or Path(data).name
        data = Path(data).read_bytes()
    raw = data.encode() if isinstance(data, str) else bytes(data)
    if not str(source or "").strip():
        raise ContractError(
            "Background harmonics: the measurement's source is required"
        )
    if not str(poc or "").strip():
        raise ContractError("Background harmonics: the point of connection is required")
    try:
        date = dt.date.fromisoformat(str(measured_on))
    except ValueError:
        raise ContractError(
            f"Background harmonics: measured_on must be an ISO date, got {measured_on!r}"
        ) from None
    try:
        df = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        raise ContractError(
            f"Background harmonics: not a readable CSV ({exc})"
        ) from None
    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in (ORDER_COL, VALUE_COL) if c not in df.columns]
    if missing:
        raise ContractError(f"Background harmonics: CSV missing column(s) {missing}")
    if df.empty:
        raise ContractError("Background harmonics: the CSV has no rows")
    orders = {}
    for k, rec in enumerate(df[[ORDER_COL, VALUE_COL]].to_dict("records"), start=2):
        o, v = rec[ORDER_COL].strip(), rec[VALUE_COL].strip()
        if not o:
            raise ContractError(f"Background harmonics: line {k} has no order")
        try:
            h = int(o)
        except ValueError:
            raise ContractError(
                f"Background harmonics: line {k}: order {o!r} is not an integer"
            ) from None
        if not H_MIN <= h <= H_MAX:
            raise ContractError(
                f"Background harmonics: line {k}: order {h} is outside {H_MIN}..{H_MAX}"
            )
        if h in orders:
            raise ContractError(f"Background harmonics: order {h} is given twice")
        try:
            pct = float(v)
        except ValueError:
            raise ContractError(
                f"Background harmonics: order {h} has no value"
            ) from None
        if not math.isfinite(pct) or pct < 0:
            raise ContractError(
                f"Background harmonics: order {h}: {v} is not a finite value >= 0"
            )
        orders[h] = pct
    return {
        "poc": str(poc),
        "source": str(source).strip(),
        "measured_on": date.isoformat(),
        "statistic": STATISTIC,
        "filename": filename,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "orders_pct": dict(sorted(orders.items())),
    }


def combine(u_plant: dict, u_bg: dict, law: str = "iec") -> dict:
    """
    ``{order: U_h %}`` of the plant and the background summed per IEC
    61000-3-6 at every order either side names.
    """
    out = {}
    for h in sorted(set(u_plant) | set(u_bg)):
        a = alpha(h, law)
        out[h] = (u_bg.get(h, 0.0) ** a + u_plant.get(h, 0.0) ** a) ** (1.0 / a)
    return out


def citation(record: dict) -> str:
    """One line naming the measurement, for a combined row's note."""
    return f"background {record['statistic']} at {record['poc']}, {record['source']}, {record['measured_on']}"


def save_background(run_dir, record: dict) -> Path:
    path = Path(run_dir) / BACKGROUND_JSON
    tmp = path.with_suffix(".json.tmp")
    body = dict(record, orders_pct={str(h): v for h, v in record["orders_pct"].items()})
    tmp.write_text(json.dumps(body, indent=2) + "\n")
    tmp.replace(path)
    return path


def load_background(run_dir) -> dict | None:
    """The stored record, orders as ints; None when no background is stored."""
    path = Path(run_dir) / BACKGROUND_JSON
    if not path.is_file():
        return None
    try:
        rec = json.loads(path.read_text())
        rec["orders_pct"] = {int(h): float(v) for h, v in rec["orders_pct"].items()}
        missing = [
            k for k in ("poc", "source", "measured_on", "statistic") if k not in rec
        ]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ContractError(
            f"{path.name} is not a background-harmonics record ({exc}); import it again"
        ) from None
    if missing:
        raise ContractError(f"{path.name} lacks {missing}; import the background again")
    return rec
