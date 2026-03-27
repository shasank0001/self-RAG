from app.pipeline.state import GraphState

__all__ = [
    "GraphExecutionResult",
    "GraphRuntime",
    "GraphState",
    "build_graph_runtime",
    "run_self_rag_graph",
]


def __getattr__(name: str):
    if name in {"GraphExecutionResult", "GraphRuntime", "build_graph_runtime", "run_self_rag_graph"}:
        from app.pipeline.graph import (
            GraphExecutionResult,
            GraphRuntime,
            build_graph_runtime,
            run_self_rag_graph,
        )

        exports = {
            "GraphExecutionResult": GraphExecutionResult,
            "GraphRuntime": GraphRuntime,
            "build_graph_runtime": build_graph_runtime,
            "run_self_rag_graph": run_self_rag_graph,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
