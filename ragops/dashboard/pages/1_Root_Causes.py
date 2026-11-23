"""Root causes: which stage breaks, how it trends, and example requests for each."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ragops.dashboard.common import LABEL_COLORS, LABEL_ORDER, nice, pget, setup, style

setup("Root causes", "Each failed request is assigned to the pipeline stage that broke. Fix the biggest bar first.")
rc = pget("/root-causes", days=st.session_state.days)
if not rc["total_failures"]:
    st.success("No diagnosed failures in this window.")
    st.stop()

daily = pd.DataFrame(rc["daily"])
st.subheader("Failures per day by stage")
fig = go.Figure()
for lab in LABEL_ORDER:
    d = daily[daily["label"] == lab]
    if len(d):
        fig.add_trace(go.Bar(x=d["day"], y=d["count"], name=nice(lab), marker_color=LABEL_COLORS[lab]))
fig.update_layout(barmode="stack")
st.plotly_chart(style(fig, 340), width='stretch')

st.subheader("What each stage means and how to fix it")
for b in rc["breakdown"]:
    with st.expander(f"{nice(b['label'])}: {b['count']} failures ({b['share']:.0%})", expanded=b is rc["breakdown"][0]):
        st.markdown(f"**What to do:** {b['fix']}")
        ex = rc["examples"].get(b["label"], [])
        if ex:
            st.dataframe(pd.DataFrame(ex)[["query", "confidence", "trace_id"]], hide_index=True, width='stretch')
            st.caption("Copy a trace id into the **Traces** page to see the evidence behind the diagnosis.")
