"""Build the tiny cutout fixture for profile_gen atlite tests.

The netCDF fixture is BINARY, so it is not committed to git — it is built
deterministically from this script (closed forms, no RNG, no network):
  - at test time by the session fixture in test_profile_gen_atlite_source.py
    (into a pytest tmp dir), and
  - by hand via `python tools/make_profile_gen_fixture.py`, which ALSO
    re-records tests/fixtures/profile_gen/golden.json by running the real
    atlite conversion chain. Golden.json IS committed; the tests assert
    against it.

The fixture is a 3x3-cell x 48h ERA5-shaped dataset with enough variables
for atlite's wind and pv conversion chains (wnd100m+roughness; influx_direct/
influx_diffuse/influx_toa/albedo+temperature). Values are plausible, not
physical — tests assert against the recorded golden, not against meteorology.
"""
from __future__ import annotations

import json
import pathlib

import numpy as np
import xarray as xr

OUT = (
    pathlib.Path(__file__).resolve().parent.parent
    / "tests" / "fixtures" / "profile_gen" / "cutout_tiny.nc"
)

X = np.array([13.0, 13.25, 13.5])
Y = np.array([52.0, 52.25, 52.5])
TIME = np.arange(np.datetime64("2020-01-01"), np.datetime64("2020-01-03"),
                 np.timedelta64(1, "h"))  # 48 h


def build(out: pathlib.Path) -> pathlib.Path:
    """Write the deterministic fixture cutout to `out` and return the path.

    Pure numpy/xarray — no atlite import, no network — so tests can build it
    into a tmp dir offline."""
    nt, ny, nx = len(TIME), len(Y), len(X)
    hour = np.arange(nt, dtype=np.float64) % 24.0

    def field(fn):
        out = np.empty((nt, ny, nx))
        for t in range(nt):
            for j in range(ny):
                for i in range(nx):
                    out[t, j, i] = fn(hour[t], Y[j], X[i], j, i)
        return out

    # Wind at 100 m: diurnal wave + per-cell offset, always positive.
    wnd100m = field(lambda h, la, lo, j, i: 6.0 + 2.0 * np.sin(2 * np.pi * h / 24.0)
                    + 0.5 * i - 0.3 * j)
    roughness = field(lambda h, la, lo, j, i: 0.10 + 0.01 * (i + j))

    # Radiation: simple daytime bell peaking at 13:00 UTC, scaled by latitude.
    def bell(h, la):
        x = np.sin(np.pi * (h - 6.0) / 14.0)  # daylight ~06:00-20:00
        return max(0.0, x) * (1.0 - 0.002 * (la - 52.0) * 4)

    influx_toa = field(lambda h, la, lo, j, i: 600.0 * bell(h, la))
    influx_direct = field(lambda h, la, lo, j, i: 350.0 * bell(h, la))
    influx_diffuse = field(lambda h, la, lo, j, i: 120.0 * bell(h, la))
    albedo = field(lambda h, la, lo, j, i: 0.2)
    temperature = field(lambda h, la, lo, j, i: 278.0 + 4.0 * np.sin(2 * np.pi * (h - 9) / 24.0)
                        - 0.5 * (la - 52.0))

    lon2d, lat2d = np.meshgrid(X, Y)
    ds = xr.Dataset(
        {
            "wnd100m": (("time", "y", "x"), wnd100m),
            "roughness": (("time", "y", "x"), np.broadcast_to(
                roughness[:1], (nt, ny, nx)).copy()),
            "influx_toa": (("time", "y", "x"), influx_toa),
            "influx_direct": (("time", "y", "x"), influx_direct),
            "influx_diffuse": (("time", "y", "x"), influx_diffuse),
            "albedo": (("time", "y", "x"), albedo),
            "temperature": (("time", "y", "x"), temperature),
        },
        coords={
            "time": TIME,
            "y": Y,
            "x": X,
            "lon": (("y", "x"), lon2d),
            "lat": (("y", "x"), lat2d),
        },
        attrs={"module": "era5", "prepared_features": []},
    )
    for var, feat in [("wnd100m", "wind"), ("roughness", "wind"),
                      ("influx_toa", "influx"), ("influx_direct", "influx"),
                      ("influx_diffuse", "influx"), ("albedo", "influx"),
                      ("temperature", "temperature")]:
        ds[var].attrs.update(module="era5", feature=feat)

    out.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(out)
    return out


def main() -> None:
    build(OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")

    # Golden values: run the REAL atlite conversion chain on two cell-centre
    # points and record the output the tests will assert against. Rounded to
    # 6 dp; tests compare with atol=1e-6.
    import atlite

    turbine = {"V": [0, 3, 12, 25, 26], "POW": [0, 0.1, 2.0, 3.0, 0.0],
               "hub_height": 100, "P": 3.0}
    panel = {"model": "huld", "efficiency": 0.1, "c_temp_amb": 1,
             "c_temp_irrad": 0.035, "r_tamb": 293, "r_tmod": 298,
             "r_irradiance": 1000, "k_1": -0.017162, "k_2": -0.040289,
             "k_3": -0.004681, "k_4": 0.000148, "k_5": 0.000169,
             "k_6": 0.000005, "inverter_efficiency": 0.9}
    cutout = atlite.Cutout(path=str(OUT))
    wind = cutout.wind(turbine=turbine, aggregate_time=None)
    pv = cutout.pv(panel=panel, orientation={"slope": 35.0, "azimuth": 180.0},
                   clearsky_model="simple", aggregate_time=None)
    golden = {}
    for name, (lon, lat) in {"center": (13.25, 52.25), "corner": (13.0, 52.0)}.items():
        golden[name] = {
            "point": [lon, lat],
            "wind": [round(float(v), 6) for v in wind.sel(y=lat, x=lon, method="nearest").values],
            "pv": [round(float(v), 6) for v in pv.sel(y=lat, x=lon, method="nearest").values],
        }
    golden_path = OUT.with_name("golden.json")
    golden_path.write_text(json.dumps(golden, indent=1))
    print(f"wrote {golden_path}")


if __name__ == "__main__":
    main()
