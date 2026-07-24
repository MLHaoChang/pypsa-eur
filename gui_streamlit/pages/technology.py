import io
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.config_manager import get_active_config, get_project_root

ROOT = get_project_root()
DATA_DIR = ROOT / "data"
CUSTOM_COSTS_PATH = DATA_DIR / "custom_costs.csv"

# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("📊 Technology & Costs")
st.caption(
    "View and edit technology cost assumptions. "
    "`data/custom_costs.csv` overrides any retrieved default values."
)

tab_custom, tab_reference = st.tabs(["✏️ Custom Cost Overrides", "📋 Reference Cost Data"])

# ===========================================================================
# Tab 1 — Custom costs (data/custom_costs.csv)
# ===========================================================================

with tab_custom:
    st.markdown(
        "**`data/custom_costs.csv`** — These values override the default cost database "
        "for any technology/parameter/year combination you specify. "
        "Leave blank rows for technologies you don't want to override."
    )

    @st.cache_data(show_spinner=False)
    def load_custom_costs(path: str) -> pd.DataFrame:
        return pd.read_csv(path, dtype=str)

    df_custom = load_custom_costs(str(CUSTOM_COSTS_PATH))

    # ---- Filter ---
    col1, col2 = st.columns(2)
    with col1:
        all_techs = ["All"] + sorted(df_custom["technology"].dropna().unique().tolist())
        sel_tech = st.selectbox("Filter by technology", all_techs, key="custom_tech")
    with col2:
        all_params = ["All"] + sorted(df_custom["parameter"].dropna().unique().tolist())
        sel_param = st.selectbox("Filter by parameter", all_params, key="custom_param")

    view = df_custom.copy()
    if sel_tech != "All":
        view = view[view["technology"] == sel_tech]
    if sel_param != "All":
        view = view[view["parameter"] == sel_param]

    st.dataframe(view, use_container_width=True, height=350, hide_index=True)

    st.divider()
    st.subheader("Export / Import")

    col_dl, col_ul = st.columns(2)

    # Download
    with col_dl:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df_custom.to_excel(writer, sheet_name="custom_costs", index=False)
        st.download_button(
            "⬇️ Download as Excel",
            data=buf.getvalue(),
            file_name="custom_costs.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    # Upload
    with col_ul:
        uploaded = st.file_uploader(
            "⬆️ Upload modified Excel",
            type=["xlsx", "xls"],
            key="custom_upload",
            help="Must have the same columns: planning_horizon, technology, parameter, value, unit, source, further description",
        )

    if uploaded:
        try:
            df_new = pd.read_excel(uploaded, dtype=str)
            required = {"planning_horizon", "technology", "parameter", "value", "unit"}
            missing = required - set(df_new.columns)
            if missing:
                st.error(f"Missing required columns: {missing}")
            else:
                st.success(f"Preview — {len(df_new)} rows loaded from upload.")
                st.dataframe(df_new.head(20), use_container_width=True, hide_index=True)
                if st.button("✅ Apply to custom_costs.csv", type="primary"):
                    df_new.to_csv(CUSTOM_COSTS_PATH, index=False)
                    st.success("Saved to `data/custom_costs.csv`. Changes take effect on next workflow run.")
                    st.cache_data.clear()
                    st.rerun()
        except Exception as exc:
            st.error(f"Could not read file: {exc}")

# ===========================================================================
# Tab 2 — Reference cost data (downloaded costs_{year}.csv files)
# ===========================================================================

with tab_reference:
    st.markdown(
        "Default cost database downloaded during the workflow. "
        "These files are read-only — use the **Custom Cost Overrides** tab to change values."
    )

    # Discover cost files anywhere under the project
    cost_files = sorted(ROOT.glob("**/costs_*.csv"))
    cost_files = [f for f in cost_files if ".pixi" not in str(f)]

    if not cost_files:
        st.info(
            "No cost files found yet. Run the workflow first "
            "(Snakemake will download `costs_{year}.csv` for each planning horizon)."
        )
        st.stop()

    year_map = {f.stem.replace("costs_", ""): f for f in cost_files}
    selected_year = st.selectbox("Planning horizon", sorted(year_map.keys(), reverse=True))
    ref_path = year_map[selected_year]

    @st.cache_data(show_spinner=False)
    def load_ref_costs(path: str) -> pd.DataFrame:
        return pd.read_csv(path, index_col=[0, 1])

    df_ref = load_ref_costs(str(ref_path))

    # Filter
    col1, col2 = st.columns(2)
    with col1:
        techs = ["All"] + sorted(df_ref.index.get_level_values(0).unique().tolist())
        sel_tech_ref = st.selectbox("Filter by technology", techs, key="ref_tech")
    with col2:
        params = ["All"] + sorted(df_ref.index.get_level_values(1).unique().tolist())
        sel_param_ref = st.selectbox("Filter by parameter", params, key="ref_param")

    view_ref = df_ref.copy()
    if sel_tech_ref != "All":
        view_ref = view_ref.xs(sel_tech_ref, level=0, drop_level=False)
    if sel_param_ref != "All":
        view_ref = view_ref.xs(sel_param_ref, level=1 if sel_tech_ref == "All" else 0, drop_level=False)

    st.dataframe(view_ref, use_container_width=True, height=400)

    # Download reference as Excel
    buf_ref = io.BytesIO()
    with pd.ExcelWriter(buf_ref, engine="openpyxl") as writer:
        df_ref.to_excel(writer, sheet_name=f"costs_{selected_year}")
    st.download_button(
        f"⬇️ Download costs_{selected_year}.xlsx",
        data=buf_ref.getvalue(),
        file_name=f"costs_{selected_year}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
