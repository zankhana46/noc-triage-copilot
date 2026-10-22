"""Wires the five Phase 3 nodes into the LangGraph triage pipeline:

    alarm_correlator -> topology_analyst -> diagnostician --+--> runbook_retriever
                              ^                             |        |
                              +----(low confidence, loop)---+        v
                                                           remediation_writer -> END

The Diagnostician/router loop is capped by state["max_diagnostic_loops"]
(see diagnostician.route_after_diagnostician).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from neo4j import Driver

from noc_copilot.agents.correlator import make_alarm_correlator_node
from noc_copilot.agents.diagnostician import diagnostician, route_after_diagnostician
from noc_copilot.agents.remediation_writer import remediation_writer
from noc_copilot.agents.runbook_retriever import make_runbook_retriever_node
from noc_copilot.agents.state import TriageState
from noc_copilot.agents.topology_analyst import make_topology_analyst_node
from noc_copilot.retrieval.hybrid import HybridRetriever


def build_triage_graph(
    driver: Driver, retriever: HybridRetriever, retrieval_top_k: int = 5
) -> CompiledStateGraph:
    graph = StateGraph(TriageState)
    # mypy struggles to match these closure-returned `TriageState -> dict`
    # callables against langgraph's heavily overloaded add_node signature
    # (plain `def` nodes below resolve fine); runtime behavior is
    # unaffected, verified via tests/test_agents.py.
    correlator_node = make_alarm_correlator_node(driver)
    topology_node = make_topology_analyst_node(driver)
    runbook_retriever_node = make_runbook_retriever_node(retriever, retrieval_top_k)
    graph.add_node("alarm_correlator", correlator_node)  # type: ignore
    graph.add_node("topology_analyst", topology_node)  # type: ignore
    graph.add_node("diagnostician", diagnostician)
    graph.add_node("runbook_retriever", runbook_retriever_node)  # type: ignore
    graph.add_node("remediation_writer", remediation_writer)

    graph.add_edge(START, "alarm_correlator")
    graph.add_edge("alarm_correlator", "topology_analyst")
    graph.add_edge("topology_analyst", "diagnostician")
    graph.add_conditional_edges(
        "diagnostician",
        route_after_diagnostician,
        {"topology_analyst": "topology_analyst", "runbook_retriever": "runbook_retriever"},
    )
    graph.add_edge("runbook_retriever", "remediation_writer")
    graph.add_edge("remediation_writer", END)

    return graph.compile()
