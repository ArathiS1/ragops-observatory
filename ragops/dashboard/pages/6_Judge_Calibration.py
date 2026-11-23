"""Judge calibration: can you trust the automated scores?"""

import pandas as pd
import streamlit as st

from ragops.dashboard.common import nice, pget, setup

setup("Judge calibration", "An automated judge is only useful if it agrees with people. Label a few traces "
                           "(👍/👎 on the Traces page) and this page measures agreement with Cohen's κ.")
cal = pget("/calibration")
if not cal:
    st.info("No human labels yet. Open a trace on the **Traces** page and mark it 👍 Correct or 👎 Wrong.")
    st.stop()
for r in cal:
    with st.container(border=True):
        a, b, c, d = st.columns(4)
        a.markdown(f"**{nice(r['metric'])}**")
        b.metric("Agreement", f"{r['agreement']:.0%}")
        c.metric("Cohen's κ", "–" if r["kappa"] is None else f"{r['kappa']:.2f}")
        d.metric("Labels", r["n"])
        st.caption(f"Verdict: **{r['verdict']}** · judge pass / human pass {r['tp']}, judge fail / human fail "
                   f"{r['tn']}, judge pass / human fail {r['fp']}, judge fail / human pass {r['fn']}")
st.markdown("""
**How to read κ:** above 0.8 the judge can gate releases on its own; 0.6 to 0.8 is fine for alerts;
0.4 to 0.6 means use it for trends only and review its failures; below 0.4, fix the judge prompt or model.
Raw agreement alone can mislead when most answers pass, which is why κ corrects for chance agreement.
""")
st.dataframe(pd.DataFrame(cal), hide_index=True, width='stretch')
