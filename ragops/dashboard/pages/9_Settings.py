"""Project settings, API keys and audit log."""

import json

import httpx
import pandas as pd
import streamlit as st

from ragops.dashboard.common import pget, setup

setup("Settings", "Sampling and judge budget, privacy, retention, auto-promotion of failures, API keys and the audit log.")
try:
    s = pget("/settings")
except Exception:
    st.error("Settings need an admin or viewer key for this project.")
    st.stop()


def patch(body: dict) -> None:
    with httpx.Client(base_url=st.session_state.api_url, timeout=30,
                      headers={"Authorization": f"Bearer {st.session_state.api_key}"}) as c:
        r = c.patch(f"/v1/projects/{st.session_state.project}/settings", json=body)
    if r.status_code >= 400:
        st.error(r.json().get("detail", r.text))
    else:
        st.cache_data.clear()
        st.success("Saved.")


with st.form("settings"):
    st.markdown("**Online evaluation**")
    c = st.columns(2)
    rate = c[0].slider("Share of normal traffic to evaluate", 0.0, 1.0, float(s["sample_rate"]), 0.05)
    budget = c[1].number_input("Daily judge budget (USD)", 0.0, 10_000.0, float(s["daily_eval_budget_usd"]))
    neg = st.checkbox("Always evaluate requests with negative user feedback", s["always_eval_negative_feedback"])
    st.markdown("**Privacy & retention**")
    fields = st.multiselect("Redact PII in", ["query", "answer", "chunk_text"], s["redact_fields"])
    ents = st.multiselect("PII types", ["EMAIL", "PHONE", "CREDIT_CARD", "SSN", "IP_ADDRESS", "IBAN"], s["redaction_entities"])
    raw = st.checkbox("Store raw text (off = keep salted hashes only)", s["store_raw_text"])
    ret = st.number_input("Delete traces after (days)", 1, 3650, int(s["retention_days"]))
    st.markdown("**Failure → golden set**")
    ag = s.get("auto_golden") or {"enabled": False, "min_confidence": 0.75, "golden_set": "production-failures"}
    agen = st.checkbox("Automatically add confidently diagnosed failures to a golden set", ag.get("enabled", False))
    agset = st.text_input("Golden set", ag.get("golden_set", "production-failures"))
    agconf = st.slider("Minimum diagnosis confidence", 0.5, 1.0, float(ag.get("min_confidence", 0.75)), 0.05)
    if st.form_submit_button("Save settings"):
        patch({"sample_rate": rate, "daily_eval_budget_usd": budget, "always_eval_negative_feedback": neg,
               "redact_fields": fields, "redaction_entities": ents, "store_raw_text": raw, "retention_days": int(ret),
               "auto_golden": {**ag, "enabled": agen, "golden_set": agset, "min_confidence": agconf}})

with st.expander("Raw settings JSON"):
    st.code(json.dumps(s, indent=2), language="json")
try:
    st.subheader("API keys")
    st.dataframe(pd.DataFrame(pget("/keys")), hide_index=True, width='stretch')
    st.caption("Roles: **ingest** (write-only, safe to embed in services), **viewer**, **editor**, **admin**. "
               "Create keys with `POST /v1/projects/{project}/keys`.")
    st.subheader("Audit log")
    st.dataframe(pd.DataFrame(pget("/audit")), hide_index=True, width='stretch')
except Exception:
    st.caption("Keys and audit log need an admin key.")
