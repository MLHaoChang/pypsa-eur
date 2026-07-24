import subprocess
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.config_manager import get_active_config, get_project_root, load_user_config

ROOT = get_project_root()

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------

st.title("▶️ Run Workflow")
st.caption("Launch Snakemake to build and solve the PyPSA-Eur model.")

cfg = get_active_config()
user_cfg = load_user_config()

if not user_cfg:
    st.warning("No configuration saved yet. Go to **Setup** first to configure your run.")

# ---------------------------------------------------------------------------
# Active config summary
# ---------------------------------------------------------------------------

with st.expander("📋 Active Configuration", expanded=True):
    col1, col2, col3, col4 = st.columns(4)
    snap = cfg.get("snapshots", {})
    scenario = cfg.get("scenario", {})

    col1.metric("Run name", cfg["run"].get("name") or "(default)")
    col1.metric("Foresight", cfg.get("foresight", "overnight"))

    clusters = scenario.get("clusters", [50])
    col2.metric("Clusters", ", ".join(str(c) for c in clusters))
    col2.metric("Countries", len(cfg.get("countries", [])))

    col3.metric("Snapshot start", snap.get("start", "—"))
    col3.metric("Snapshot end", snap.get("end", "—"))

    horizons = scenario.get("planning_horizons", [2050])
    col4.metric("Planning horizons", ", ".join(str(y) for y in horizons))
    col4.metric("Solver", cfg.get("solving", {}).get("solver", {}).get("name", "highs"))

st.divider()

# ---------------------------------------------------------------------------
# Run options
# ---------------------------------------------------------------------------

st.subheader("Workflow Options")

col1, col2, col3 = st.columns(3)

with col1:
    stored_scope = st.session_state.get("model_scope", "Electricity only")
    scope_opts = ["Electricity only", "Sector-coupled"]
    scope = st.radio(
        "Model scope",
        scope_opts,
        index=scope_opts.index(stored_scope),
        help="Determines the Snakemake target rule.",
    )
    st.session_state["model_scope"] = scope

with col2:
    cores = st.slider("Parallel cores", min_value=1, max_value=16, value=4)
    dry_run = st.checkbox(
        "Dry run (-n)",
        help="Print what Snakemake would execute without running anything.",
    )

with col3:
    use_config_yaml = st.checkbox(
        "Use saved config.yaml",
        value=bool(user_cfg),
        disabled=not bool(user_cfg),
        help="Pass the saved config/config.yaml to Snakemake. Uncheck to use defaults.",
    )
    force_rerun = st.checkbox(
        "Force rerun all (-F)",
        help="Re-execute all rules even if outputs exist.",
    )

# Determine target rule
foresight = cfg.get("foresight", "overnight")
if scope == "Electricity only":
    target_rule = "solve_elec_networks"
elif foresight == "overnight":
    target_rule = "solve_sector_networks"
else:
    target_rule = "all"

# Build command preview
cmd_parts = ["pixi", "run", "snakemake", "-call", target_rule, f"--cores={cores}"]
if use_config_yaml and user_cfg:
    cmd_parts += ["--configfile", "config/config.yaml"]
if dry_run:
    cmd_parts.append("-n")
if force_rerun:
    cmd_parts.append("-F")

st.info(f"Command: `{' '.join(cmd_parts)}`")

st.divider()

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

col_run, col_stop = st.columns([3, 1])

with col_run:
    run_clicked = st.button(
        "🚀 Run Workflow",
        type="primary",
        use_container_width=True,
        disabled=dry_run is False and "process_running" in st.session_state,
    )

with col_stop:
    if st.button("⏹ Cancel", use_container_width=True):
        proc: subprocess.Popen | None = st.session_state.pop("active_process", None)
        if proc:
            proc.terminate()
            st.warning("Process terminated.")
        else:
            st.info("No process running.")

if run_clicked:
    log_area = st.empty()
    status_area = st.empty()
    full_output: list[str] = []

    status_area.info("⏳ Workflow is running…")

    try:
        with subprocess.Popen(
            cmd_parts,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(ROOT),
            bufsize=1,
        ) as proc:
            st.session_state["active_process"] = proc

            for line in proc.stdout:
                full_output.append(line)
                log_area.code("".join(full_output[-100:]), language="text")

            proc.wait()
            st.session_state.pop("active_process", None)

        if proc.returncode == 0:
            status_area.success("✅ Workflow completed successfully!")
        else:
            status_area.error(f"❌ Workflow failed — exit code {proc.returncode}")

        # Keep full log in session for inspection
        st.session_state["last_log"] = full_output

    except FileNotFoundError:
        status_area.error(
            "`pixi` not found. Make sure pixi is installed and available in PATH, "
            "or run this app from within `pixi shell`."
        )
    except Exception as exc:
        status_area.error(f"Unexpected error: {exc}")

# ---------------------------------------------------------------------------
# Previous run log
# ---------------------------------------------------------------------------

if "last_log" in st.session_state and not run_clicked:
    with st.expander("📄 Last run log", expanded=False):
        st.code("".join(st.session_state["last_log"]), language="text")

        log_text = "".join(st.session_state["last_log"])
        st.download_button(
            "⬇️ Download log",
            data=log_text,
            file_name="snakemake_run.log",
            mime="text/plain",
        )

# ---------------------------------------------------------------------------
# Results location hint
# ---------------------------------------------------------------------------

st.divider()
run_name = cfg["run"].get("name") or ""
results_path = ROOT / "results" / run_name
nc_files = list(results_path.glob("**/*.nc")) if results_path.exists() else []

if nc_files:
    st.success(
        f"**{len(nc_files)} solved network(s)** found in `results/{run_name}/`. "
        "Go to **Results** to explore them."
    )
else:
    st.info(
        f"Results will appear in `results/{run_name}/` after a successful run."
    )
