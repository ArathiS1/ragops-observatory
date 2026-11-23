"""Agentic RAG built with LangGraph: a StateGraph that retrieves, checks whether the context is good enough,
and then either answers or declines. Retrieval and generation reuse the LangChain components.

    retrieve --> grade --(relevant)--> generate --> END
                      \\--(not relevant)--> decline --> END

    pip install langgraph langchain-core langchain-openai langchain-text-splitters fastembed
    ragops test -f langgraph.yaml
"""
from __future__ import annotations

from typing import Any, TypedDict

from common import ABSTAIN
from langchain_rag import generate, retrieve, to_contexts
from langgraph.graph import END, StateGraph


class State(TypedDict, total=False):
    question: str
    config: dict
    hits: list[Any]
    relevant: bool
    answer: str


def node_retrieve(state: State) -> State:
    return {"hits": retrieve(state["question"], state["config"])}


def node_grade(state: State) -> State:
    """Decline when the best chunk is too weak: the graph's guard against answering from irrelevant context."""
    hits, min_score = state["hits"], state["config"].get("min_score")
    ok = bool(hits) and (min_score is None or hits[0][1] >= float(min_score))
    return {"relevant": ok}


def node_generate(state: State) -> State:
    return {"answer": generate(state["question"], state["hits"], state["config"])}


def node_decline(state: State) -> State:
    return {"answer": ABSTAIN}


def build_graph():
    g = StateGraph(State)
    g.add_node("retrieve", node_retrieve)
    g.add_node("grade", node_grade)
    g.add_node("generate", node_generate)
    g.add_node("decline", node_decline)
    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "grade")
    g.add_conditional_edges("grade", lambda s: "generate" if s["relevant"] else "decline",
                            {"generate": "generate", "decline": "decline"})
    g.add_edge("generate", END)
    g.add_edge("decline", END)
    return g.compile()


GRAPH = build_graph()


def answer(question: str, config: dict | None = None) -> dict:
    out = GRAPH.invoke({"question": question, "config": config or {}})
    return {"answer": out["answer"], "contexts": to_contexts(out["hits"])}


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(answer(" ".join(sys.argv[1:]) or "How long is the password reset link valid?"), indent=2))
