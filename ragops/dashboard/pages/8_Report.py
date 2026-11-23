"""Reliability report for managers."""

import streamlit as st

from ragops.dashboard.common import pget, setup

setup("Reliability report", "A weekly summary you can forward: status, numbers, top failure causes, quality "
                            "changes and knowledge-base gaps.")
md = pget("/report", days=st.session_state.days, format="md")
html = pget("/report", days=st.session_state.days, format="html")
a, b = st.columns(2)
a.download_button("⬇️ Markdown", md, f"{st.session_state.project}-report.md", "text/markdown", width='stretch')
b.download_button("⬇️ HTML (print to PDF)", html, f"{st.session_state.project}-report.html", "text/html",
                  width='stretch')
st.divider()
st.markdown(md)
