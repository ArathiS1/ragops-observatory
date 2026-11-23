"""SLOs and alerts."""

import pandas as pd
import streamlit as st

from ragops.dashboard.common import nice, pget, ppost, setup

setup("SLOs & alerts", "Promises about answer quality, with error budgets and alerts that name the failing stage.")
slos = pget("/slos")
if slos:
    df = pd.DataFrame(slos)
    df["status"] = df["status"].map({"ok": "✅ ok", "at_risk": "⚠️ at risk", "breached": "🚨 breached", "no_data": "⏳ no data"})
    st.dataframe(df[["name", "metric", "threshold", "target", "window_days", "n", "good_ratio", "budget_remaining",
                     "burn_rate_24h", "status"]], hide_index=True, width='stretch')
with st.expander("➕ New SLO"):
    with st.form("slo"):
        n = st.text_input("Name", "Grounded answers")
        m = st.selectbox("Metric", ["faithfulness", "answer_relevance", "context_relevance", "context_recall",
                                    "citation_correctness", "abstention_quality"], format_func=nice)
        th = st.slider("A request is good when the score is at least", 0.0, 1.0, 0.7, 0.05)
        tg = st.slider("Target share of good requests", 0.5, 0.999, 0.9, 0.005)
        w = st.number_input("Window (days)", 1, 90, 7)
        if st.form_submit_button("Create"):
            ppost("/slos", {"name": n, "metric": m, "threshold": th, "target": tg, "window_days": int(w)})
            st.rerun()
if st.button("Run SLO check now"):
    r = ppost("/slos/check")
    st.success(f"{len(r['alerts'])} new alert(s).")

st.subheader("Alert channels")
try:
    ch = pget("/alert-channels")
    st.dataframe(pd.DataFrame(ch), hide_index=True, width='stretch')
    with st.form("ch"):
        k = st.selectbox("Kind", ["slack", "webhook", "pagerduty", "email"])
        t = st.text_input("Target (webhook URL, PagerDuty routing key or email address)")
        if st.form_submit_button("Add channel") and t:
            ppost("/alert-channels", {"kind": k, "target": t})
            st.rerun()
except Exception:
    st.caption("Alert channels need an admin key.")

st.subheader("Recent alerts")
al = pget("/alerts")
if not al:
    st.write("No alerts.")
for a in al:
    fn = {"critical": st.error, "warning": st.warning}.get(a["severity"], st.info)
    fn(f"**{a['title']}** · {a['at'][:16]}  \n{a['body']}")
