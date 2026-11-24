"""
GC-08e shot data: the real backend's scorecard (G.grid_readiness) and pack tables (sources.read "GC-08") for
the six states, built with the GC-08e test helpers, dumped to JSON. Run from pypsa-gui/backend:
    python <this> <out.json>
"""

import json
import math
import sys
import tempfile
from pathlib import Path

from pytest import MonkeyPatch

sys.path.insert(0, str(Path.cwd()))

from tests.test_grid_compliance_ssti_island import (  # noqa: E402
    _grid,
    _island,
    _island_meta,
    _islanded,
    _open_tie,
    _pack,
    _project,
    _site,
    _uif_table,
)
from tests.test_grid_readiness_ssti import _card, _uif_meta, _write  # noqa: E402
from tests.test_grid_readiness_voltage_stability import _template  # noqa: E402


def _clean(o):
    """JSON as the API sends it: NaN/inf become null."""
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def _scorecard(d):
    return json.loads(json.dumps(_clean(_card(d)[1]), default=str))


def _table(t):
    return t.model_dump(mode="json")


def main(out):
    res = {"scorecard": {}, "pack": {}}
    tmp = lambda: Path(tempfile.mkdtemp(prefix="gc08e-"))  # noqa: E731

    d = tmp()
    _grid(
        d,
        _island_meta("clear", 0.08, "flag", 0.5),
        _uif_table(0.08) + _island(0.5, bus="LV"),
    )
    res["scorecard"]["S1"] = _scorecard(d)

    d = tmp()
    _grid(
        d, _island_meta("clear", 0.08, "clear", 0.06), _uif_table(0.08) + _island(0.06)
    )
    res["scorecard"]["S2"] = _scorecard(d)

    d = tmp()
    _grid(
        d,
        _island_meta("clear", 0.08, "not evaluated", None),
        _uif_table(0.08) + _island(math.nan, bus="LV"),
    )
    res["scorecard"]["S5"] = _scorecard(d)

    d = tmp()  # saved before GC-08e: the record has no islanded verdict
    _template(d, "strong_grid")
    _write(
        d, uif=_uif_meta("clear", worst=0.08), uif_rows=_uif_table(0.08) + _island(0.5)
    )
    res["scorecard"]["S6"] = _scorecard(d)

    mp = MonkeyPatch()
    try:
        d = tmp()
        rows = _uif_table(0.08) + _island(0.5, bus="MV") + _island(math.nan, bus="LV")
        _grid(d, _island_meta("clear", 0.08, "flag", 0.5), rows)
        res["pack"]["P1"] = _table(_pack(_project(mp, d))[1]["ssti_uif"])
        mp.undo()

        d = tmp()
        _islanded(
            d,
            _island_meta("flag", 0.25, "clear", 0.06),
            _uif_table(0.25) + _island(0.06),
        )
        res["pack"]["P2"] = _table(
            _pack(_project(mp, d, n=_open_tie(_site())))[1]["ssti_uif"]
        )
    finally:
        mp.undo()

    for k, v in res["scorecard"].items():
        r = next(r for r in v["rows"] if r["id"] == "ssti_uif")
        print(k, r["verdict"], r["project_value"], "|", r["note"])
    for k, v in res["pack"].items():
        print(k, "|", v["caption"])
    Path(out).write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
