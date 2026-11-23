"""Overview page: is the RAG system healthy, and if not, which stage is breaking?"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ragops.dashboard.common import LABEL_COLORS, health_banner, nice, num, pct, pget, setup, style

setup("Overview", "Is your RAG system healthy right now, and if not, which pipeline stage is breaking?")
days = st.session_state.days
ov = pget("/overview", days=days)
health_banner(ov["health"])

c = st.columns(5)
c[0].metric("Requests", f"{ov['traces']:,}")
c[1].metric("Evaluated", f"{ov['evaluated']:,}", help="Requests scored by the judge. Sampling keeps judge cost "
            "inside the daily budget; negative feedback is always evaluated.")
c[1].caption(f"{pct(ov['eval_coverage'])} of traffic")
c[2].metric("Failure rate", pct(ov["failure_rate"]), help="Share of evaluated requests that failed a quality "
            "threshold, got negative feedback, or used outdated documents.")
c[3].metric("Latency p95", "–" if ov["latency_p95_ms"] is None else f"{ov['latency_p95_ms']:.0f} ms")
c[4].metric("Judge spend", f"${ov['judge_cost_usd']:.2f}", help=f"LLM spend in the window: ${ov['llm_cost_usd']:.2f}")

left, right = st.columns([3, 2])
with left:
    st.subheader("Where failures come from")
    st.caption("Every failed request is assigned to the pipeline stage that broke, so you know what to fix first.")
    rc = pget("/root-causes", days=days)
    if rc["breakdown"]:
        df = pd.DataFrame(rc["breakdown"]).iloc[::-1]
        fig = go.Figure(go.Bar(
            x=df["count"], y=[nice(x) for x in df["label"]], orientation="h",
            marker_color=[LABEL_COLORS.get(x, "#888") for x in df["label"]],
            text=[f"{s:.0%}" for s in df["share"]], textposition="outside", cliponaxis=False,
            customdata=df["fix"], hovertemplate="<b>%{y}</b>: %{x} failures<br>%{customdata}<extra></extra>"))
        st.plotly_chart(style(fig, 300, legend=False).update_layout(hovermode="closest"), width='stretch')
        top = rc["breakdown"][0]
        st.info(f"**Fix first: {nice(top['label'])}** ({top['share']:.0%} of failures). {top['fix']}", icon="🛠️")
    else:
        st.success("No diagnosed failures in this window.")

with right:
    st.subheader("Service-level objectives")
    st.caption("Error budget = how many bad answers you can still afford this window.")
    if not ov["slos"]:
        st.write("No SLOs defined yet. Add one on the **SLOs & Alerts** page.")
    for s in ov["slos"]:
        icon = {"ok": "✅", "at_risk": "⚠️", "breached": "🚨", "no_data": "⏳"}[s["status"]]
        with st.container(border=True):
            st.markdown(f"{icon} **{s['name']}** · `{s['metric']} ≥ {s['threshold']}` on "
                        f"{s['target']:.0%} of requests ({s['window_days']}d)")
            if s["n"]:
                budget = s["budget_remaining"]
                st.progress(max(0.0, min(1.0, budget or 0.0)),
                            text=f"Actual {s['good_ratio']:.1%} · budget left {pct(budget)} · "
                                 f"24h burn rate {num(s['burn_rate_24h'], 1)}×")

st.subheader("Quality over time, with every change you shipped")
st.caption("Vertical markers are config deploys and document changes, so you can see which change moved quality.")
tl = pget("/timeline", days=max(days, 14))
fig = go.Figure()
for i, (m, color) in enumerate(zip(["faithfulness", "answer_relevance", "context_relevance"],
                                   ["#2a78d6", "#eb6834", "#1baf7a"])):
    pts = tl["quality"].get(m, [])
    if pts:
        fig.add_trace(go.Scatter(x=[p["day"] for p in pts], y=[p["mean"] for p in pts], name=nice(m),
                                 mode="lines+markers", line_color=color, marker_size=8))
for e in tl["events"]:
    if e["kind"] == "config":
        fig.add_vline(x=e["time"][:10], line_dash="dot", line_color="rgba(128,128,128,0.6)")
        fig.add_annotation(x=e["time"][:10], y=1.04, yref="paper", text=f"⚙ {e['label']}", showarrow=False,
                           font_size=11, xanchor="left")
    elif e["kind"] == "corpus" and ("changed" in e["label"] or "deleted" in e["label"]):
        fig.add_vline(x=e["time"][:10], line_dash="dash", line_color="rgba(235,104,52,0.6)")
        fig.add_annotation(x=e["time"][:10], y=0.02, yref="paper", text=f"📄 {e['label']}", showarrow=False,
                           font_size=11, xanchor="left")
fig.update_yaxes(range=[0, 1.08], title="mean score")
st.plotly_chart(style(fig, 360), width='stretch')

with st.expander("New here? How to read this dashboard"):
    st.markdown("""
1. **Health banner**: one sentence on whether anything needs attention, and the top reason.
2. **Where failures come from**: each failing request is assigned to *one* stage: the knowledge base
   (gap, stale, chunking), retrieval (miss, ranking), or the prompt and model (overload, generation).
3. **SLOs**: your promises about answer quality, with an error budget like an uptime SLO.
4. **Timeline**: quality lines with your deploys on top. A drop right after a marker points at the cause.

Next: open **Root causes** to see example requests per stage, or **Traces** to inspect a single request.
""")
