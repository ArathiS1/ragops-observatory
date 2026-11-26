"""RAGOps Observatory: production reliability for retrieval-augmented generation."""

__version__ = "0.10.1"

from ragops.sdk.client import RagopsClient, observe_rag  # noqa: E402,F401

__all__ = ["RagopsClient", "observe_rag", "__version__"]
