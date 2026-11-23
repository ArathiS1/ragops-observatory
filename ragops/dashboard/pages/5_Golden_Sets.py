"""Golden sets: regression tests grown from real production failures, used by the CI release gate."""

import json

import pandas as pd
import streamlit as st

from ragops.dashboard.common import nice, pget, setup

setup("Golden sets & release gate", "Every production failure can become a regression test. The CI gate replays "
                                   "these cases on each pipeline change and blocks the ones that make things worse.")
sets = pget("/golden-sets")
if not sets:
    st.info("No golden sets yet. Promote a failed trace from the **Traces** page, or enable auto-promotion in "
            "**Settings**.")
    st.stop()
name = st.selectbox("Golden set", [s["name"] for s in sets],
                    format_func=lambda n: f"{n} (v{next(s['version'] for s in sets if s['name'] == n)})")
g = pget(f"/golden-sets/{name}/export")
df = pd.DataFrame(g["cases"])
c = st.columns(3)
c[0].metric("Cases", len(df))
c[1].metric("Version", g["version"])
c[2].metric("From production failures", int(df["source_trace_id"].notna().sum()) if len(df) else 0)
if len(df):
    df["came from"] = df["failure_label"].map(lambda x: nice(x) if x else "manual")
    st.dataframe(df[["query", "expected_doc_ids", "came from", "added_in_version"]], hide_index=True,
                 width='stretch')
    st.download_button("⬇️ Download golden set (JSON)", json.dumps(g, indent=2), f"{name}.json", "application/json")

st.subheader("Run the gate in CI")
st.code(f"""# .github/workflows/rag-gate.yml (excerpt)
- uses: ArathiS1/ragops-observatory@v0.5
  with:
    pipeline: my_app.rag:pipeline
    baseline: configs/prod.yaml
    candidate: configs/candidate.yaml
    server: ${{{{ secrets.RAGOPS_URL }}}}
    api-key: ${{{{ secrets.RAGOPS_API_KEY }}}}
    project: {st.session_state.project}
    golden-set: {name}""", language="yaml")
st.caption("Or locally: `ragops gate --pipeline my_app.rag:pipeline --baseline prod.yaml --candidate new.yaml "
           f"--server $RAGOPS_URL --project {st.session_state.project} --golden-set {name}`")
