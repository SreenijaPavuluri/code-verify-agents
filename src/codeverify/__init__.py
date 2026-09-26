"""codeverify — an autonomous code-verification & bug-patching multi-agent system.

The public entry points are:

* :func:`codeverify.graph.build_graph` — compile the LangGraph state machine.
* :func:`codeverify.graph.run_task` — run one bug-fix task end to end.
* :mod:`codeverify.api.app` — FastAPI service with streaming + human review.
* :mod:`codeverify.eval.harness` — the automated benchmark harness.
"""

__version__ = "0.1.0"
