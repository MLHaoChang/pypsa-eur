import subprocess
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.config_manager import get_active_config, get_project_root

ROOT = get_project_root()

# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("🗄️ Data & Cache")
st.caption(
    "PyPSA-Eur downloads input data once and caches it in `resources/`. "
    "After the initial download, all scenario runs are fully offline."
)

cfg = get_active_config()

# ---------------------------------------------------------------------------
# Architecture info box
# ---------------------------------------------------------------------------

st.info(
    "**How it works:** The simulation engine (PyPSA + HiGHS) runs entirely on your "
    "machine. The only internet access is the one-time data retrieval step below. "
    "Once cached, re-runs skip all downloads automatically."
)

# ---------------------------------------------------------------------------
# SSL certificate status
# ---------------------------------------------------------------------------

st.subheader("1 — SSL Certificate")

env_file = ROOT / ".env"
env_content = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
ssl_configured = "SSL_CERT_FILE" in env_content

col1, col2 = st.columns([2, 1])
with col1:
    if ssl_configured:
        st.success("`.env` contains `SSL_CERT_FILE` — corporate CA is configured.")
    else:
        st.warning(
            "`SSL_CERT_FILE` not set in `.env`. Downloads will fail on corporate networks."
        )
        with st.expander("How to fix this"):
            st.markdown(
                """
**Step 1 — Export the corporate root CA from Windows:**
```
Win+R → certmgr.msc
→ Trusted Root Certification Authorities → Certificates
→ Find the Hitachi Energy / ABB root CA
→ Right-click → All Tasks → Export
→ Format: Base-64 encoded X.509 (.cer)
→ Save to e.g. C:\\certs\\hitachi-ca.cer
```

**Step 2 — Enter the path below and click Save.**
                """
            )

with col2:
    cert_path = st.text_input(
        "Certificate path",
        value="C:/certs/hitachi-ca.cer",
        placeholder="C:/certs/hitachi-ca.cer",
        disabled=ssl_configured,
    )
    if not ssl_configured and st.button("Save to .env", type="primary"):
        lines = [
            f"SSL_CERT_FILE={cert_path}",
            f"REQUESTS_CA_BUNDLE={cert_path}",
            f"CURL_CA_BUNDLE={cert_path}",
        ]
        with open(env_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        st.success("Saved. Restart the app for the new setting to take effect.")
        st.rerun()

st.divider()

# ---------------------------------------------------------------------------
# Data cache status
# ---------------------------------------------------------------------------

st.subheader("2 — Cached Data")

TRACKED_DIRS = {
    "resources/": "All downloaded and built intermediate files",
    "cutouts/": "ERA5 weather cutouts (largest files, ~GB each)",
}

total_size = 0
rows = []
for rel, desc in TRACKED_DIRS.items():
    d = ROOT / rel
    if d.exists():
        files = list(d.rglob("*"))
        file_count = sum(1 for f in files if f.is_file())
        dir_size = sum(f.stat().st_size for f in files if f.is_file())
        total_size += dir_size
        status = "✅ Present" if file_count > 0 else "⬜ Empty"
        rows.append(
            {
                "Directory": rel,
                "Description": desc,
                "Files": file_count,
                "Size (MB)": round(dir_size / 1e6, 1),
                "Status": status,
            }
        )
    else:
        rows.append(
            {
                "Directory": rel,
                "Description": desc,
                "Files": 0,
                "Size (MB)": 0.0,
                "Status": "⬜ Not created",
            }
        )

import pandas as pd

st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
st.caption(f"Total cached: **{total_size / 1e9:.2f} GB**")

st.divider()

# ---------------------------------------------------------------------------
# One-time download
# ---------------------------------------------------------------------------

st.subheader("3 — Download Input Data")

st.markdown(
    "Run the Snakemake **retrieve** rules to download all input data for your "
    "selected configuration. This is only needed once — subsequent runs reuse "
    "the cache automatically."
)

# Config options for the retrieval run
col1, col2 = st.columns(2)
with col1:
    use_test_config = st.checkbox(
        "Use test config (Belgium, 5 nodes — fast)",
        value=True,
        help="Downloads a small subset of data. Good for validating connectivity before a full download.",
    )
with col2:
    cores = st.slider("Cores", 1, 8, 2, key="data_cores")

configfile = "config/test/config.electricity.yaml" if use_test_config else "config/config.yaml"

retrieve_targets = [
    "retrieve_electricity_demand_opsd",
    "retrieve_electricity_demand_entsoe",
    "retrieve_powerplants",
    "retrieve_natura",
    "build_cutout",
]
cmd = (
    ["pixi", "run", "snakemake", "-call"]
    + retrieve_targets
    + ["--configfile", configfile, f"--cores={cores}"]
)

st.code(" ".join(cmd), language="bash")

col_dl, col_dry = st.columns(2)
with col_dl:
    run_download = st.button(
        "⬇️ Download Data",
        type="primary",
        use_container_width=True,
        disabled=not ssl_configured,
        help="SSL_CERT_FILE must be configured before downloading." if not ssl_configured else "",
    )
with col_dry:
    dry_run = st.button("🔍 Dry run (preview only)", use_container_width=True)

if run_download or dry_run:
    run_cmd = cmd + (["-n"] if dry_run else [])
    log_area = st.empty()
    status_area = st.empty()
    output_lines: list[str] = []

    status_area.info("⏳ Running…")
    try:
        with subprocess.Popen(
            run_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(ROOT),
            bufsize=1,
        ) as proc:
            for line in proc.stdout:
                output_lines.append(line)
                log_area.code("".join(output_lines[-80:]), language="text")
            proc.wait()

        if proc.returncode == 0:
            status_area.success(
                "✅ Done! Data is cached in `resources/`. Future runs will be fully offline."
            )
        else:
            status_area.error(f"❌ Failed — exit code {proc.returncode}")
    except FileNotFoundError:
        status_area.error("`pixi` not found. Run from within `pixi shell` or check PATH.")
    except Exception as exc:
        status_area.error(f"Error: {exc}")
