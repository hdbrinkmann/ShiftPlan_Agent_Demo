from langgraph.graph import StateGraph, END
from app.graph.state import PlanState
from app.graph.nodes import (
    ingest_node,
    rules_node,
    demand_node,
    solve_node,
    audit_node,
    kpi_node,
    triage_node,
    human_gate_node,
    export_node,
    decide_after_kpi,
)

def build_graph():
    graph = StateGraph(PlanState)

    graph.add_node("ingest", ingest_node)
    graph.add_node("rules", rules_node)
    graph.add_node("demand", demand_node)
    graph.add_node("solve", solve_node)
    graph.add_node("audit", audit_node)
    graph.add_node("kpi", kpi_node)
    graph.add_node("triage", triage_node)
    # human_gate needs access to config (auto_approve)
    def human_gate_with_cfg(state: PlanState, config=None):
        auto_approve = False
        if config and "configurable" in config:
            auto_approve = config["configurable"].get("auto_approve", False)
        return human_gate_node(state, auto_approve=auto_approve)
    graph.add_node("human_gate", human_gate_with_cfg)
    graph.add_node("export", export_node)

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "rules")
    graph.add_edge("rules", "demand")
    graph.add_edge("demand", "solve")
    graph.add_edge("solve", "audit")
    graph.add_edge("audit", "kpi")
    graph.add_conditional_edges("kpi", decide_after_kpi, {"triage": "triage", "export": "export"})
    # If triage sets needs_approval, go to human_gate; else export (done trying)
    def after_triage(state: PlanState) -> str:
        return "human_gate" if state.get("needs_approval") else "export"
    graph.add_conditional_edges("triage", after_triage, {"human_gate": "human_gate", "export": "export"})
    # After human_gate, either loop back to solve (if approved) or end waiting:
    def after_gate(state: PlanState) -> str:
        return "solve" if not state.get("awaiting_approval") else END
    graph.add_conditional_edges("human_gate", after_gate, {"solve": "solve", END: END})
    graph.add_edge("export", END)

    return graph.compile()