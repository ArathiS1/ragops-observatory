"""Drift: what changed in user questions, retrieval, the corpus and answer quality."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ragops.dashboard.common import nice, pget, setup, style

setup("Drift", "Four layers: are users asking new things, is retrieval getting worse, did documents change, "
               "did quality shift? Each one says what changed, not just that something did.")
cur = st.sidebar.slider("Current window (days)", 1, 7, 3)
ref = st.sidebar.slider("Reference window (days before)", 3, 21, 7)
d = pget("/drift", current_days=cur, reference_days=ref)

def flag(ok: bool) -> str:
    return "🔴 drift" if ok else "🟢 stable"

q, r, c = d["query"], d["retrieval"], d["corpus"]
k = st.columns(4)
k[0].metric("User questions", flag(q.get("drift_detected", False)))
k[1].metric("Retrieval", flag(r.get("drift_detected", False)))
k[2].metric("Documents changed", len(c.get("changed", [])) + len(c.get("added", [])) + len(c.get("deleted", [])))
k[3].metric("Quality metrics shifted", sum(x["detected"] for x in d["quality"]))

st.subheader("1 · User questions")
st.write(q.get("summary", ""))
if q.get("new_topics"):
    df = pd.DataFrame(q["new_topics"])
    df["covered by docs"] = df["covered"].map({True: "yes", False: "❌ no", None: "?"})
    df["examples"] = df["examples"].map(lambda e: " | ".join(e[:2]))
    df["share"] = df["share"] * 100
    st.dataframe(df[["label", "share", "novelty", "corpus_coverage", "covered by docs", "examples"]], hide_index=True,
                 width='stretch',
                 column_config={"share": st.column_config.ProgressColumn("share of questions", format="%.0f%%",
                                                                         min_value=0, max_value=100),
                                "corpus_coverage": st.column_config.NumberColumn("median doc match", format="%.2f"),
                                "novelty": st.column_config.NumberColumn(format="%.2f")})
    st.caption("Novelty = how far a topic is from anything asked in the reference window. Uncovered new topics "
               "are content your knowledge base is missing.")

st.subheader("2 · Retrieval")
st.write(r.get("summary", ""))

st.subheader("3 · Corpus")
st.write(f"Changed: {', '.join(c.get('changed', [])) or 'none'} · added: {', '.join(c.get('added', [])) or 'none'} · "
         f"deleted: {', '.join(c.get('deleted', [])) or 'none'}")
if c.get("outdated_trace_count"):
    st.warning(f"{c['outdated_trace_count']} recent answers were built from document versions that have since "
               "changed. They may now be wrong; replay or re-check them.", icon="📄")

st.subheader("4 · Answer quality (change-point detection)")
tl = pget("/timeline", days=cur + ref + 14)
for x in d["quality"]:
    pts = tl["quality"].get(x["metric"], [])
    icon = "🔻" if x.get("direction") == "degraded" else "🔺" if x.get("direction") == "improved" else "•"
    st.markdown(f"{icon} {x['summary']}" + (f"  \n   ↳ likely cause: **{'; '.join(x['likely_causes'])}**"
                                            if x.get("likely_causes") else ""))
    if pts and x["detected"]:
        fig = go.Figure(go.Scatter(x=[p["day"] for p in pts], y=[p["mean"] for p in pts], mode="lines+markers",
                                   name=nice(x["metric"]), line_color="#2a78d6", marker_size=8))
        fig.add_vline(x=x["change_date"], line_dash="dot", line_color="#eb6834")
        fig.add_hline(y=x["before_mean"], line_dash="dot", line_color="rgba(128,128,128,.5)")
        fig.add_hline(y=x["after_mean"], line_dash="dot", line_color="rgba(235,104,52,.5)")
        st.plotly_chart(style(fig, 220, legend=False), width='stretch')
