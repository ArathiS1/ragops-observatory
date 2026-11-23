"""Knowledge-base health: what to write, what to delete, and which chunks keep causing failures."""

import pandas as pd
import streamlit as st

from ragops.dashboard.common import pget, setup

setup("Knowledge base health", "Turns failures into a to-do list for your docs team: missing topics, dead chunks, "
                               "chunks that keep appearing in bad answers, and contradictions.")
kb = pget("/kb-health", days=max(st.session_state.days, 14))
st.caption("`" + " · ".join(f"{k}: {v}" for k, v in pget("/corpus/stats").items()) + "`")
c = st.columns(4)
c[0].metric("Health score", f"{kb['health_score']:.2f}", help="1.0 = no gaps, no dead weight, no contradictions.")
c[1].metric("Coverage gaps", len(kb["coverage_gaps"]))
c[2].metric("Never-retrieved chunks", f"{kb['never_retrieved_share']:.0%}")
c[3].metric("Contradictions", len(kb["contradictions"]))

st.subheader("📝 Content to write")
if kb["coverage_gaps"]:
    for g in kb["coverage_gaps"]:
        with st.container(border=True):
            st.markdown(f"**{g['topic']}** · {g['query_count']} unanswered questions")
            st.caption(g["suggestion"])
            for ex in g["examples"][:3]:
                st.markdown(f"- {ex}")
else:
    st.success("No coverage gaps found.")

a, b = st.columns(2)
with a:
    st.subheader("🔥 Hot chunks")
    st.caption("Chunks that show up again and again in failed answers. Rewrite or split them.")
    st.dataframe(pd.DataFrame(kb["hot_chunks"]), hide_index=True, width='stretch')
with b:
    st.subheader("💤 Never retrieved")
    st.caption("Chunks no query has used in this window: dead weight that adds noise to the index.")
    st.dataframe(pd.DataFrame({"chunk_id": kb["never_retrieved"][:200]}), hide_index=True, width='stretch')

st.subheader("⚔️ Duplicates and contradictions")
st.dataframe(pd.DataFrame(kb["contradictions"] + kb["duplicates"]), hide_index=True, width='stretch')
