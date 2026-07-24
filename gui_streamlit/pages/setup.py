import datetime
import sys
from pathlib import Path

import streamlit as st
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.config_manager import get_active_config, load_user_config, save_user_config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALL_COUNTRIES = [
    "AL", "AT", "BA", "BE", "BG", "CH", "CZ", "DE", "DK", "EE", "ES", "FI",
    "FR", "GB", "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "ME", "MK",
    "NL", "NO", "PL", "PT", "RO", "RS", "SE", "SI", "SK", "XK",
]

COUNTRY_LABELS = {
    "AL": "Albania", "AT": "Austria", "BA": "Bosnia & Herzegovina", "BE": "Belgium",
    "BG": "Bulgaria", "CH": "Switzerland", "CZ": "Czech Republic", "DE": "Germany",
    "DK": "Denmark", "EE": "Estonia", "ES": "Spain", "FI": "Finland", "FR": "France",
    "GB": "Great Britain", "GR": "Greece", "HR": "Croatia", "HU": "Hungary",
    "IE": "Ireland", "IT": "Italy", "LT": "Lithuania", "LU": "Luxembourg",
    "LV": "Latvia", "ME": "Montenegro", "MK": "North Macedonia", "NL": "Netherlands",
    "NO": "Norway", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
    "RS": "Serbia", "SE": "Sweden", "SI": "Slovenia", "SK": "Slovakia", "XK": "Kosovo",
}

CLUSTER_OPTIONS = ["5", "10", "25", "50", "100", "128", "200", "all", "adm"]
PLANNING_HORIZONS = [2020, 2025, 2030, 2035, 2040, 2045, 2050]
SOLVER_OPTIONS = ["highs", "gurobi", "glpk", "scip"]

# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("⚙️ Model Setup")
st.caption("Configure your PyPSA-Eur run. Changes are written to `config/config.yaml`.")

cfg = get_active_config()

with st.form("setup_form"):

    # --- Run Settings -------------------------------------------------------
    st.subheader("Run Settings")
    col1, col2 = st.columns(2)
    with col1:
        run_name = st.text_input(
            "Run name",
            value=cfg["run"].get("name") or "my-run",
            help="Subdirectory under results/ where outputs are stored.",
        )
    with col2:
        foresight_opts = ["overnight", "myopic", "perfect"]
        foresight = st.radio(
            "Foresight mode",
            foresight_opts,
            index=foresight_opts.index(cfg.get("foresight", "overnight")),
            horizontal=True,
            help="overnight: single-year greenfield | myopic: rolling years | perfect: all years jointly",
        )

    scope_opts = ["Electricity only", "Sector-coupled"]
    model_scope = st.radio(
        "Model scope",
        scope_opts,
        horizontal=True,
        help=(
            "Electricity only: optimise the power grid only. "
            "Sector-coupled: add heating, gas, H₂, transport and industry."
        ),
    )

    st.divider()

    # --- Geographic Scope ---------------------------------------------------
    st.subheader("Geographic Scope")

    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        if st.form_submit_button("Select all countries", type="secondary"):
            st.session_state["_countries_select_all"] = True
    with col2:
        if st.form_submit_button("Clear countries", type="secondary"):
            st.session_state["_countries_select_all"] = False

    default_countries = cfg.get("countries", ALL_COUNTRIES)
    countries = st.multiselect(
        "Countries",
        options=ALL_COUNTRIES,
        default=default_countries,
        format_func=lambda c: f"{c} — {COUNTRY_LABELS.get(c, c)}",
    )

    st.divider()

    # --- Scenario -----------------------------------------------------------
    st.subheader("Scenario")
    col1, col2 = st.columns(2)
    with col1:
        raw_clusters = cfg.get("scenario", {}).get("clusters", [50])
        default_clusters = [str(c) for c in raw_clusters if str(c) in CLUSTER_OPTIONS]
        clusters = st.multiselect(
            "Network clusters",
            options=CLUSTER_OPTIONS,
            default=default_clusters,
            help="Number of nodes the network is spatially aggregated to. Lower = faster solve, less detail.",
        )
    with col2:
        raw_horizons = cfg.get("scenario", {}).get("planning_horizons", [2050])
        default_horizons = [y for y in raw_horizons if y in PLANNING_HORIZONS]
        planning_horizons = st.multiselect(
            "Planning horizons",
            options=PLANNING_HORIZONS,
            default=default_horizons,
            help="Investment target years. Multiple years only meaningful for myopic/perfect foresight.",
        )

    st.divider()

    # --- Temporal Scope -----------------------------------------------------
    st.subheader("Temporal Scope")
    snap = cfg.get("snapshots", {})
    col1, col2 = st.columns(2)
    with col1:
        snap_start = st.date_input(
            "Snapshot start",
            value=datetime.date.fromisoformat(snap.get("start", "2013-01-01")),
            help="First hour of the optimisation window.",
        )
    with col2:
        snap_end = st.date_input(
            "Snapshot end",
            value=datetime.date.fromisoformat(snap.get("end", "2014-01-01")),
            help="Last hour of the optimisation window (exclusive).",
        )

    st.divider()

    # --- Sector Options (shown only for sector-coupled) ---------------------
    if model_scope == "Sector-coupled":
        st.subheader("Sector Options")
        sector_cfg = cfg.get("sector", {})
        col1, col2, col3 = st.columns(3)
        with col1:
            gas_network = st.toggle("Gas network", value=sector_cfg.get("gas_network", False))
            h2_network = st.toggle("H₂ network", value=sector_cfg.get("H2_network", False))
            h2_retrofit = st.toggle("H₂ pipeline retrofit", value=sector_cfg.get("H2_retrofit", False))
        with col2:
            district_heating = st.toggle(
                "District heating", value=bool(sector_cfg.get("district_heating", {}).get("potential", 0.0))
            )
            co2_spatial = st.toggle("Spatial CO₂ tracking", value=sector_cfg.get("co2_spatial", False))
            co2_network = st.toggle("CO₂ network", value=sector_cfg.get("co2_network", False))
        with col3:
            ammonia = st.toggle("Ammonia", value=bool(sector_cfg.get("ammonia", False)))
            methanol = st.toggle(
                "Methanol", value=sector_cfg.get("methanol", {}).get("methanol_to_power", {}).get("ocgt", False)
            )
        st.divider()

    # --- Advanced -----------------------------------------------------------
    with st.expander("⚡ CO₂ constraint"):
        co2_enabled = st.checkbox(
            "Enable CO₂ limit",
            value=cfg.get("electricity", {}).get("co2limit_enable", False),
        )
        co2_limit_mt = st.number_input(
            "CO₂ limit (Mt CO₂/year)",
            value=float(cfg.get("electricity", {}).get("co2limit", 100e6)) / 1e6,
            min_value=0.0,
            step=10.0,
            disabled=not co2_enabled,
        )

    with st.expander("🔧 Solver"):
        solver_name = st.selectbox(
            "Solver",
            options=SOLVER_OPTIONS,
            index=SOLVER_OPTIONS.index(
                cfg.get("solving", {}).get("solver", {}).get("name", "highs")
            ),
        )
        mem_mb = st.number_input(
            "Memory limit (MB)",
            value=int(cfg.get("solving", {}).get("mem", 4000)),
            min_value=1000,
            step=1000,
        )

    with st.expander("📂 Shared Resources (data caching)"):
        st.caption(
            "Input data (grid topology, weather, costs) is downloaded once and cached. "
            "Shared resources let multiple scenario runs reuse the same cached data."
        )
        shared_policy_opts = {
            "Off — each run is fully independent": False,
            "Base — share downloaded data, keep solved networks per run (recommended)": "base",
            "All — share everything across runs": True,
        }
        current_policy = cfg.get("run", {}).get("shared_resources", {}).get("policy", False)
        # Map stored value back to display label
        reverse_map = {v: k for k, v in shared_policy_opts.items()}
        default_label = reverse_map.get(current_policy, list(shared_policy_opts.keys())[1])
        shared_policy_label = st.radio(
            "Shared resources policy",
            options=list(shared_policy_opts.keys()),
            index=list(shared_policy_opts.keys()).index(default_label),
        )
        shared_policy = shared_policy_opts[shared_policy_label]

    # --- Submit -------------------------------------------------------------
    submitted = st.form_submit_button(
        "💾 Save Configuration", type="primary", use_container_width=True
    )

# ---------------------------------------------------------------------------
# Validation & save
# ---------------------------------------------------------------------------

if submitted:
    errors = []
    if not run_name.strip():
        errors.append("Run name cannot be empty.")
    if not countries:
        errors.append("Select at least one country.")
    if not clusters:
        errors.append("Select at least one cluster size.")
    if not planning_horizons:
        errors.append("Select at least one planning horizon.")
    if snap_start >= snap_end:
        errors.append("Snapshot start must be before snapshot end.")

    if errors:
        for e in errors:
            st.error(e)
    else:
        # Parse cluster values (numeric string → int, "all"/"adm" stay as str)
        parsed_clusters = []
        for c in clusters:
            try:
                parsed_clusters.append(int(c))
            except ValueError:
                parsed_clusters.append(c)

        override: dict = {
            "run": {
                "name": run_name.strip(),
                "disable_progressbar": False,
                "shared_resources": {"policy": shared_policy, "exclude": []},
            },
            "foresight": foresight,
            "scenario": {
                "clusters": parsed_clusters,
                "opts": [""],
                "sector_opts": [""],
                "planning_horizons": planning_horizons,
            },
            "countries": countries,
            "snapshots": {
                "start": snap_start.isoformat(),
                "end": snap_end.isoformat(),
                "inclusive": "left",
            },
            "electricity": {
                "co2limit_enable": co2_enabled,
                "co2limit": co2_limit_mt * 1e6,
            },
            "solving": {
                "solver": {
                    "name": solver_name,
                    "options": f"{solver_name}-default",
                },
                "mem": mem_mb,
            },
        }

        if model_scope == "Sector-coupled":
            override["sector"] = {
                "gas_network": gas_network,
                "H2_network": h2_network,
                "H2_retrofit": h2_retrofit,
                "co2_spatial": co2_spatial,
                "co2_network": co2_network,
                "ammonia": ammonia,
            }

        # Persist scope choice for Run page
        st.session_state["model_scope"] = model_scope

        save_user_config(override)
        st.success("✅ Configuration saved to `config/config.yaml`")
        st.balloons()

# ---------------------------------------------------------------------------
# Read-only preview of saved config
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Current config/config.yaml")
user_cfg = load_user_config()
if user_cfg:
    st.code(
        yaml.dump(user_cfg, default_flow_style=False, allow_unicode=True, sort_keys=False),
        language="yaml",
    )
else:
    st.info("No user config saved yet — using all defaults.")
