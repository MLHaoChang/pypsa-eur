import io
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.config_manager import get_active_config, get_project_root

ROOT = get_project_root()

# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("📈 Results")
st.caption("Explore optimisation results from solved PyPSA networks.")

cfg = get_active_config()
run_name = cfg["run"].get("name") or ""

# ---------------------------------------------------------------------------
# Network file selector
# ---------------------------------------------------------------------------

# Search current run first, then all results
results_root = ROOT / "results"
search_paths = []
if run_name:
    search_paths.append(results_root / run_name)
search_paths.append(results_root)

nc_files: list[Path] = []
for sp in search_paths:
    if sp.exists():
        nc_files = sorted(sp.glob("**/*.nc"))
        if nc_files:
            break

if not nc_files:
    st.warning(
        f"No solved networks found in `results/`. "
        "Complete a workflow run first, then come back here."
    )
    st.stop()

col1, col2 = st.columns([3, 1])
with col1:
    selected_nc = st.selectbox(
        "Select network",
        options=nc_files,
        format_func=lambda p: str(p.relative_to(ROOT)),
    )
with col2:
    load_btn = st.button("Load", type="primary", use_container_width=True)

if load_btn or "network" not in st.session_state:
    if load_btn or "network" not in st.session_state:
        with st.spinner(f"Loading {selected_nc.name}…"):
            try:
                import pypsa

                n = pypsa.Network(str(selected_nc))
                st.session_state["network"] = n
                st.session_state["network_path"] = str(selected_nc)
                st.success(f"Loaded **{selected_nc.name}**")
            except Exception as exc:
                st.error(f"Failed to load network: {exc}")
                st.stop()

if "network" not in st.session_state:
    st.info("Select a network file above and click **Load**.")
    st.stop()

n = st.session_state["network"]

# ---------------------------------------------------------------------------
# Top-level metrics
# ---------------------------------------------------------------------------

st.divider()
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Buses", len(n.buses))
col2.metric("Lines / Links", f"{len(n.lines)} / {len(n.links)}")
col3.metric("Generators", len(n.generators))
col4.metric("Storage units", len(n.storage_units))
col5.metric("Snapshots", len(n.snapshots))

st.divider()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_costs, tab_cap, tab_dispatch, tab_map = st.tabs(
    ["💰 System Costs", "⚡ Capacity Mix", "📉 Dispatch", "🗺️ Network Map"]
)

# ============================================================
# Tab 1 — System Costs
# ============================================================

with tab_costs:
    st.subheader("System Costs by Technology")

    try:
        has_opt = "p_nom_opt" in n.generators.columns

        # Capital costs — only meaningful for optimised (expanded) capacities
        if has_opt:
            cap_cost = (
                (n.generators.capital_cost * n.generators.p_nom_opt)
                .groupby(n.generators.carrier)
                .sum()
                / 1e9
            )
        else:
            cap_cost = pd.Series(dtype=float)

        # Operational costs
        weights = n.snapshot_weightings.generators
        op_cost = (
            n.generators_t.p.mul(weights, axis=0)
            .sum()
            .mul(n.generators.marginal_cost)
            .groupby(n.generators.carrier)
            .sum()
            / 1e9
        )

        costs_df = pd.DataFrame(
            {"Capital cost (B€)": cap_cost, "Operational cost (B€)": op_cost}
        ).fillna(0)
        costs_df = costs_df[costs_df.sum(axis=1) > 1e-6].sort_values(
            "Capital cost (B€)", ascending=False
        )

        fig = px.bar(
            costs_df,
            barmode="stack",
            title="Annual System Costs (B€)",
            labels={"value": "Cost (B€)", "variable": "Cost type", "index": "Technology"},
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig.update_layout(xaxis_title="Technology", yaxis_title="Cost (B€)", legend_title="")
        st.plotly_chart(fig, use_container_width=True)

        st.dataframe(
            costs_df.style.format("{:.4f}"),
            use_container_width=True,
        )

        # Storage costs
        if has_opt and not n.storage_units.empty:
            sto_cap_cost = (
                (n.storage_units.capital_cost * n.storage_units.p_nom_opt)
                .groupby(n.storage_units.carrier)
                .sum()
                / 1e9
            )
            sto_cap_cost = sto_cap_cost[sto_cap_cost > 1e-6]
            if not sto_cap_cost.empty:
                fig2 = px.bar(
                    sto_cap_cost,
                    title="Storage Capital Costs (B€)",
                    labels={"value": "Cost (B€)", "index": "Carrier"},
                )
                st.plotly_chart(fig2, use_container_width=True)

    except Exception as exc:
        st.warning(f"Could not compute costs: {exc}")

# ============================================================
# Tab 2 — Capacity Mix
# ============================================================

with tab_cap:
    st.subheader("Installed Capacity by Carrier")

    try:
        cap_col = "p_nom_opt" if "p_nom_opt" in n.generators.columns else "p_nom"

        gen_cap = n.generators.groupby("carrier")[cap_col].sum() / 1e3  # GW
        gen_cap = gen_cap[gen_cap > 0].sort_values(ascending=False)

        fig = px.bar(
            gen_cap,
            title=f"Generator Capacity — {cap_col} (GW)",
            labels={"value": "Capacity (GW)", "index": "Carrier"},
            color=gen_cap.index,
            color_discrete_sequence=px.colors.qualitative.Plotly,
        )
        fig.update_layout(showlegend=False, xaxis_title="Carrier", yaxis_title="GW")
        st.plotly_chart(fig, use_container_width=True)

        # Storage
        if not n.storage_units.empty:
            sto_col = "p_nom_opt" if "p_nom_opt" in n.storage_units.columns else "p_nom"
            sto_cap = n.storage_units.groupby("carrier")[sto_col].sum() / 1e3
            sto_cap = sto_cap[sto_cap > 0].sort_values(ascending=False)
            if not sto_cap.empty:
                fig2 = px.bar(
                    sto_cap,
                    title=f"Storage Capacity — {sto_col} (GW)",
                    labels={"value": "Capacity (GW)", "index": "Carrier"},
                )
                fig2.update_layout(showlegend=False, xaxis_title="Carrier", yaxis_title="GW")
                st.plotly_chart(fig2, use_container_width=True)

        # Country breakdown
        if "country" in n.buses.columns:
            st.subheader("Capacity by Country")
            country_map = n.buses["country"]
            gen_country = (
                n.generators.assign(country=n.generators.bus.map(country_map))
                .groupby(["country", "carrier"])[cap_col]
                .sum()
                .unstack(fill_value=0)
                / 1e3
            )
            fig3 = px.bar(
                gen_country,
                barmode="stack",
                title="Generator Capacity per Country (GW)",
                labels={"value": "GW", "country": "Country"},
            )
            fig3.update_layout(xaxis_title="Country", yaxis_title="GW")
            st.plotly_chart(fig3, use_container_width=True)

    except Exception as exc:
        st.warning(f"Could not compute capacity: {exc}")

# ============================================================
# Tab 3 — Dispatch timeseries
# ============================================================

with tab_dispatch:
    st.subheader("Generation Dispatch")

    try:
        dispatch_raw = (
            n.generators_t.p.T.groupby(n.generators.carrier).sum().T / 1e3
        )  # GW
        dispatch_raw = dispatch_raw.loc[:, (dispatch_raw.abs() > 0).any()]

        # Resample control
        n_hours = len(dispatch_raw)
        col1, col2 = st.columns(2)
        with col1:
            resample_opts = {"Hourly": "h", "Daily": "D", "Weekly": "W", "Monthly": "ME"}
            if n_hours > 8760:
                default_res = "Weekly"
            elif n_hours > 720:
                default_res = "Daily"
            else:
                default_res = "Hourly"
            resolution = st.selectbox("Time resolution", list(resample_opts.keys()), index=list(resample_opts.keys()).index(default_res))
        with col2:
            carriers = dispatch_raw.columns.tolist()
            sel_carriers = st.multiselect("Carriers", carriers, default=carriers[:min(10, len(carriers))])

        if sel_carriers:
            dispatch = dispatch_raw[sel_carriers]
            freq = resample_opts[resolution]
            if freq != "h":
                dispatch = dispatch.resample(freq).mean()

            fig = px.area(
                dispatch,
                title=f"Generation Dispatch — {resolution} average (GW)",
                labels={"value": "Power (GW)", "variable": "Carrier"},
                color_discrete_sequence=px.colors.qualitative.Set1,
            )
            fig.update_layout(
                xaxis_title="Time",
                yaxis_title="GW",
                hovermode="x unified",
            )
            st.plotly_chart(fig, use_container_width=True)

            # Load vs generation
            if not n.loads_t.p_set.empty:
                total_load = n.loads_t.p_set.sum(axis=1) / 1e3
                if freq != "h":
                    total_load = total_load.resample(freq).mean()
                fig.add_scatter(
                    x=total_load.index,
                    y=total_load.values,
                    mode="lines",
                    name="Load",
                    line=dict(color="black", width=2, dash="dash"),
                )
                st.plotly_chart(fig, use_container_width=True)

    except Exception as exc:
        st.warning(f"Could not compute dispatch: {exc}")

# ============================================================
# Tab 4 — Network Map
# ============================================================

with tab_map:
    st.subheader("Network Geographic View")

    try:
        buses = n.buses[["x", "y", "carrier", "v_nom"]].copy()
        if "country" in n.buses.columns:
            buses["country"] = n.buses["country"]

        buses = buses.dropna(subset=["x", "y"])
        buses = buses[(buses["x"].between(-30, 45)) & (buses["y"].between(30, 75))]

        if buses.empty:
            st.info("No bus coordinates available for mapping.")
        else:
            # Carrier filter for bus colouring
            cap_col = "p_nom_opt" if "p_nom_opt" in n.generators.columns else "p_nom"
            bus_cap = n.generators.groupby("bus")[cap_col].sum() / 1e3
            buses["total_capacity_gw"] = buses.index.map(bus_cap).fillna(0)

            fig_map = px.scatter_mapbox(
                buses.reset_index(),
                lat="y",
                lon="x",
                color="carrier" if buses["carrier"].nunique() > 1 else None,
                size="total_capacity_gw",
                size_max=20,
                hover_name="Bus",
                hover_data={"x": True, "y": True, "v_nom": True, "total_capacity_gw": ":.2f"},
                title="Network Buses (sized by total installed capacity)",
                mapbox_style="carto-positron",
                zoom=3,
                center={"lat": 52, "lon": 10},
                height=600,
            )

            # Draw transmission lines
            line_traces = []
            for _, row in n.lines.iterrows():
                b0 = n.buses.loc[row.bus0] if row.bus0 in n.buses.index else None
                b1 = n.buses.loc[row.bus1] if row.bus1 in n.buses.index else None
                if b0 is not None and b1 is not None:
                    line_traces.append(
                        dict(
                            type="scattermapbox",
                            lon=[b0.x, b1.x, None],
                            lat=[b0.y, b1.y, None],
                            mode="lines",
                            line=dict(width=0.8, color="rgba(100,100,200,0.4)"),
                            hoverinfo="skip",
                            showlegend=False,
                        )
                    )

            for trace in line_traces[:500]:  # cap at 500 lines for performance
                fig_map.add_trace(trace)

            st.plotly_chart(fig_map, use_container_width=True)

    except Exception as exc:
        st.warning(f"Could not render map: {exc}")

# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------

st.divider()
st.subheader("⬇️ Export Results to Excel")

if st.button("Generate Excel report", type="primary"):
    with st.spinner("Building Excel workbook…"):
        buf = io.BytesIO()
        cap_col = "p_nom_opt" if "p_nom_opt" in n.generators.columns else "p_nom"

        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            # Summary sheet
            summary = pd.DataFrame(
                {
                    "Metric": ["Buses", "Generators", "Lines", "Links", "Storage units", "Snapshots"],
                    "Value": [
                        len(n.buses), len(n.generators), len(n.lines),
                        len(n.links), len(n.storage_units), len(n.snapshots),
                    ],
                }
            )
            summary.to_excel(writer, sheet_name="Summary", index=False)

            # Generator capacities
            gen_cap_df = (
                n.generators.groupby("carrier")[
                    [c for c in [cap_col, "p_nom", "capital_cost", "marginal_cost"] if c in n.generators.columns]
                ]
                .sum()
            )
            gen_cap_df.to_excel(writer, sheet_name="Generator Capacity")

            # Storage capacities
            if not n.storage_units.empty:
                sto_cap_df = (
                    n.storage_units.groupby("carrier")[
                        [c for c in [cap_col, "p_nom", "capital_cost"] if c in n.storage_units.columns]
                    ]
                    .sum()
                )
                sto_cap_df.to_excel(writer, sheet_name="Storage Capacity")

            # Dispatch (daily mean)
            try:
                dispatch_xl = (
                    n.generators_t.p.T.groupby(n.generators.carrier).sum().T.resample("D").mean() / 1e3
                )
                dispatch_xl.to_excel(writer, sheet_name="Dispatch (GW, daily mean)")
            except Exception:
                pass

            # Line data
            n.lines[
                [c for c in ["bus0", "bus1", "length", "s_nom", "s_nom_opt", "carrier"] if c in n.lines.columns]
            ].to_excel(writer, sheet_name="Lines")

        stem = Path(st.session_state.get("network_path", "network")).stem
        st.download_button(
            "📥 Download Excel workbook",
            data=buf.getvalue(),
            file_name=f"pypsa_results_{stem}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
