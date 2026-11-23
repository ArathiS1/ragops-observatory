"""Trace explorer: inspect one request end to end, with the evidence behind its diagnosis."""

import pandas as pd
import streamlit as st

from ragops.dashboard.common import nice, pget, ppost, setup

setup("Traces", "Inspect any request: what was retrieved, what the model saw, how it was scored and why it failed.")

f1, f2, f3 = st.columns([2, 2, 3])
labels = ["(any)", "knowledge_gap", "stale_knowledge", "chunking_defect", "retrieval_miss", "ranking_failure",
          "context_overload", "generation_failure"]
label = f1.selectbox("Root cause", labels, format_func=lambda x: x if x == "(any)" else nice(x))
status = f2.selectbox("Outcome", ["failed only", "all", "passed only"])
q = f3.text_input("Search queries")
params = {"limit": 200}
if label != "(any)":
    params["label"] = label
if status != "all":
    params["failed"] = status == "failed only"
if q:
    params["q"] = q
rows = pget("/traces", **params)
if not rows:
    st.info("No traces match these filters.")
    st.stop()
df = pd.DataFrame(rows)
df["root cause"] = df["label"].map(lambda x: nice(x) if x else "")
sel = st.dataframe(df[["created_at", "query", "root cause", "confidence", "eval_status", "latency_ms", "id"]],
                   hide_index=True, width='stretch', on_select="rerun", selection_mode="single-row", height=280)
picked = sel.selection.rows[0] if sel.selection.rows else 0
tid = st.text_input("Trace id", df.iloc[picked]["id"])
t = pget(f"/traces/{tid}")

st.divider()
st.markdown(f"#### ❓ {t['query']}")
st.markdown(f"**Answer:** {t['answer'] or '_(none)_'}")
meta = [f"config `{t['config']['label']}`" if t["config"] else None, f"{t['latency_ms']:.0f} ms" if t["latency_ms"] else None,
        f"feedback {t['user_feedback']:+.0f}" if t["user_feedback"] is not None else None, f"source {t['source']}"]
st.caption(" · ".join(m for m in meta if m))

dx = t["diagnosis"]
if dx and dx["label"] != "ok":
    st.error(f"**{nice(dx['label'])}** (confidence {dx['confidence']:.0%}). {dx['fix']}", icon="🩺")
    with st.expander("Evidence behind this diagnosis"):
        st.json(dx["evidence"])
elif dx:
    st.success("This request passed every check.", icon="✅")
else:
    st.info(f"Not evaluated ({t['eval_status']}). {t['metadata'].get('sample_reason', '')}")

c1, c2 = st.columns([3, 2])
with c1:
    st.markdown("**Chunks** (⭐ = the chunk that should have answered, ⚠️ = outdated document version)")
    for step, title in (("retrieve", "Retrieved candidates"), ("rerank", "After reranking"), ("context", "Sent to the LLM")):
        ch = [c for c in t["chunks"] if c["step"] == step]
        if not ch or (step == "retrieve" and len(ch) == len([c for c in t["chunks"] if c["step"] == "context"])
                      and [c["chunk_id"] for c in ch] == [c["chunk_id"] for c in t["chunks"] if c["step"] == "context"]):
            continue
        st.markdown(f"*{title}*")
        st.dataframe(pd.DataFrame([{"#": c["rank"], "": ("⭐" if c["is_gold"] else "") + ("⚠️" if c["stale"] else ""),
                                    "doc": c["doc_id"], "ver": c["doc_version"], "score": c["score"],
                                    "text": (c["text"] or "")[:220]} for c in ch]),
                     hide_index=True, width='stretch')
with c2:
    st.markdown("**Scores**")
    st.dataframe(pd.DataFrame([{"metric": nice(e["metric"]), "score": e["score"], "why": e["reasoning"]}
                               for e in t["evaluations"]]), hide_index=True, width='stretch')
    st.markdown("**Actions**")
    gs = st.text_input("Golden set", "production-failures")
    if st.button("➕ Add to golden set (regression test)", width='stretch'):
        try:
            r = ppost("/golden/promote", {"trace_id": tid, "golden_set": gs})
            st.success(f"Added. Expected docs: {r.get('expected_doc_ids')}" if r["added"] else r["reason"])
        except RuntimeError as e:
            st.error(str(e))
    lab_metric = st.selectbox("Human review: metric", ["faithfulness", "answer_relevance"])
    a, b = st.columns(2)
    for col, passed, txt in ((a, True, "👍 Correct"), (b, False, "👎 Wrong")):
        if col.button(txt, width='stretch', key=f"lab{passed}"):
            ppost("/labels", {"trace_id": tid, "metric": lab_metric, "passed": passed})
            st.toast("Label saved; it now counts toward judge calibration.")
