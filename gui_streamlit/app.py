import streamlit as st

st.set_page_config(
    page_title="PyPSA-Eur",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

setup_page = st.Page("pages/setup.py", title="Setup", icon="⚙️")
run_page = st.Page("pages/run.py", title="Run Workflow", icon="▶️")
data_page = st.Page("pages/data.py", title="Data & Cache", icon="🗄️")
tech_page = st.Page("pages/technology.py", title="Technology & Costs", icon="📊")
results_page = st.Page("pages/results.py", title="Results", icon="📈")

pg = st.navigation(
    {
        "Model": [setup_page, run_page],
        "Data": [data_page, tech_page],
        "Analysis": [results_page],
    }
)

pg.run()
