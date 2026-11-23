"""Shared helpers for the Streamlit dashboard: API client, sidebar connection settings, chart styling."""

from __future__ import annotations

import os

import httpx
import plotly.graph_objects as go
import streamlit as st

# Fixed colour per root-cause label (colour follows the entity on every page, never its rank).
LABEL_ORDER = ["knowledge_gap", "stale_knowledge", "chunking_defect", "retrieval_miss", "ranking_failure",
               "context_overload", "generation_failure"]
LABEL_COLORS = dict(zip(LABEL_ORDER, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"]))
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
NICE = {
    "knowledge_gap": "Knowledge gap", "stale_knowledge": "Stale knowledge", "chunking_defect": "Chunking defect",
    "retrieval_miss": "Retrieval miss", "ranking_failure": "Ranking failure", "context_overload": "Context overload",
    "generation_failure": "Generation failure", "ok": "OK", "unknown": "Unknown",
}


def nice(label: str | None) -> str:
    return NICE.get(label or "", (label or "").replace("_", " ").capitalize())


def setup(title: str, caption: str) -> None:
    st.set_page_config(page_title=f"{title} · RAGOps Observatory", page_icon="🔭", layout="wide")
    sidebar()
    st.title(title)
    st.caption(caption)


def sidebar() -> None:
    ss = st.session_state
    ss.setdefault("api_url", os.environ.get("RAGOPS_API_URL", "http://localhost:8000"))
    ss.setdefault("api_key", os.environ.get("RAGOPS_API_KEY", os.environ.get("RAGOPS_ADMIN_KEY", "ragops-admin-dev-key")))
    with st.sidebar:
        st.markdown("### 🔭 RAGOps Observatory")
        with st.expander("Connection", expanded=False):
            ss.api_url = st.text_input("API URL", ss.api_url)
            ss.api_key = st.text_input("API key", ss.api_key, type="password")
        try:
            projects = [p["name"] for p in get("/v1/projects")]
        except Exception as e:  # noqa: BLE001
            st.error(f"Cannot reach the API at {ss.api_url}: {e}")
            st.stop()
        if not projects:
            st.info("No projects yet. Run `ragops demo` or create one with `ragops create-project NAME`.")
            st.stop()
        default = os.environ.get("RAGOPS_PROJECT")
        idx = projects.index(ss.get("project", default)) if ss.get("project", default) in projects else 0
        ss.project = st.selectbox("Project", projects, index=idx)
        ss.days = st.select_slider("Time window", options=[1, 3, 7, 14, 30], value=ss.get("days", 7),
                                   format_func=lambda d: f"last {d} day{'s' if d > 1 else ''}")


def _client() -> httpx.Client:
    return httpx.Client(base_url=st.session_state.api_url, timeout=60,
                        headers={"Authorization": f"Bearer {st.session_state.api_key}"})


@st.cache_data(ttl=20, show_spinner=False)
def _cached_get(url: str, key: str, path: str, params: tuple) -> object:
    with httpx.Client(base_url=url, timeout=60, headers={"Authorization": f"Bearer {key}"}) as c:
        r = c.get(path, params=dict(params))
        r.raise_for_status()
        return r.json() if "json" in r.headers.get("content-type", "") else r.text


def get(path: str, **params) -> object:
    return _cached_get(st.session_state.api_url, st.session_state.api_key, path, tuple(sorted(params.items())))


def pget(path: str, **params) -> object:
    """GET a project-scoped endpoint."""
    return get(f"/v1/projects/{st.session_state.project}{path}", **params)


def ppost(path: str, json: dict | None = None) -> dict:
    with _client() as c:
        r = c.post(f"/v1/projects/{st.session_state.project}{path}", json=json or {})
        if r.status_code >= 400:
            raise RuntimeError(r.json().get("detail", r.text))
        _cached_get.clear()
        return r.json()


def style(fig: go.Figure, height: int = 320, legend: bool = True) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=28, b=8), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", bargap=0.35, showlegend=legend, hovermode="x unified",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None),
                      font=dict(size=13))
    fig.update_xaxes(showgrid=False, linecolor="rgba(128,128,128,0.4)")
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)", zeroline=False)
    fig.update_traces(selector=dict(type="bar"), marker_line_width=0)
    fig.update_traces(selector=dict(type="scatter"), line_width=2)
    return fig


def pct(x) -> str:
    return "–" if x is None else f"{x:.0%}"


def num(x, d: int = 2) -> str:
    return "–" if x is None else f"{x:.{d}f}"


HEALTH = {"critical": st.error, "warning": st.warning, "healthy": st.success, "info": st.info}
HEALTH_ICON = {"critical": "🚨", "warning": "⚠️", "healthy": "✅", "info": "ℹ️"}


def health_banner(h: dict) -> None:
    HEALTH.get(h["level"], st.info)(f"**{h['level'].upper()}**: {h['message']}", icon=HEALTH_ICON.get(h["level"]))
