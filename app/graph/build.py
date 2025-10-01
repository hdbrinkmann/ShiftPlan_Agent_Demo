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
from app.telemetry import publish_event
from app.services.llm import ScalewayLLM

def build_graph():
    graph = StateGraph(PlanState)

    llm = ScalewayLLM()

    def wrap(name, fn):
        def inner(state: PlanState, **kwargs):
            run_id = (state.get("kpis", {}) or {}).get("run_id") or state.get("run_id") or "default"
            publish_event(run_id, {"active_node": name, "message": f"Entering {name}"})
            # record step
            steps = list(state.get("steps", []))
            steps.append(name)
            state = {**state, "steps": steps}
            new_state = fn(state, **kwargs)
            # add a brief summary using llm for UI, but don't fail graph if LLM fails
            try:
                text = llm.chat(
                    system_prompt="Summarize the agent step in one short sentence.",
                    user_prompt=f"Node {name} executed. Keys: {list(new_state.keys())[:8]}"
                )
                publish_event(run_id, {"active_node": name, "message": text})
            except Exception:
                pass

            # Publish richer, node-specific runtime insights
            try:
                if name == "ingest":
                    emp = len(new_state.get("employees", []) or [])
                    absn = len(new_state.get("absences", []) or [])
                    publish_event(run_id, {"active_node": name, "message": f"Ingest: employees={emp}, absences={absn}"})
                elif name == "demand_step":
                    dem = len(new_state.get("demand", []) or [])
                    publish_event(run_id, {"active_node": name, "message": f"Demand: requirements={dem}"})
                elif name == "solve":
                    assigns = len((new_state.get("solution", {}) or {}).get("assignments", []) or [])
                    publish_event(run_id, {"active_node": name, "message": f"Solver: assignments={assigns}"})
                elif name == "audit_step":
                    viols = (new_state.get("audit", {}) or {}).get("violations", []) or []
                    high = sum(1 for v in viols if v.get("severity") == "high")
                    med = sum(1 for v in viols if v.get("severity") == "medium")
                    publish_event(run_id, {"active_node": name, "message": f"Audit: violations={len(viols)} (high={high}, medium={med})"})
                elif name == "kpi":
                    k = new_state.get("kpis", {}) or {}
                    cost = k.get("cost")
                    cov = k.get("coverage")
                    budget = k.get("budget")
                    over = (budget is not None and cost is not None and cost > budget)
                    tail = " over budget" if over else (" within budget" if budget is not None else "")
                    publish_event(run_id, {"active_node": name, "message": f"KPI: cost={cost}, coverage={cov}{tail}"})
                elif name == "triage":
                    needs = bool(new_state.get("needs_approval"))
                    relax = len(new_state.get("relaxations", []) or [])
                    publish_event(run_id, {"active_node": name, "message": f"Triage: needs_approval={needs}, relaxations={relax}"})
                elif name == "human_gate":
                    if new_state.get("awaiting_approval"):
                        publish_event(run_id, {"active_node": name, "message": "Human gate: awaiting manual approval"})
                    else:
                        publish_event(run_id, {"active_node": name, "message": "Human gate: approved -> re-solve"})
                elif name == "export":
                    publish_event(run_id, {"active_node": name, "message": "Export: plan finalized"})
            except Exception:
                # Never break the graph due to telemetry formatting issues
                pass
            return new_state
        return inner

    graph.add_node("ingest", wrap("ingest", ingest_node))
    graph.add_node("rules", wrap("rules", rules_node))
    graph.add_node("demand_step", wrap("demand_step", demand_node))
    graph.add_node("solve", wrap("solve", solve_node))
    graph.add_node("audit_step", wrap("audit_step", audit_node))
    graph.add_node("kpi", wrap("kpi", kpi_node))
    graph.add_node("triage", wrap("triage", triage_node))
    # human_gate needs access to config (auto_approve)
    def human_gate_with_cfg(state: PlanState, *, auto_approve: bool = False):
        return human_gate_node(state, auto_approve=auto_approve)
    graph.add_node("human_gate", wrap("human_gate", human_gate_with_cfg))
    graph.add_node("export", wrap("export", export_node))

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "rules")
    graph.add_edge("rules", "demand_step")
    graph.add_edge("demand_step", "solve")
    graph.add_edge("solve", "audit_step")
    graph.add_edge("audit_step", "kpi")
    graph.add_conditional_edges("kpi", decide_after_kpi, {"triage": "triage", "export": "export"})
    # If triage sets needs_approval:
    def after_triage(state: PlanState) -> str:
        return "human_gate" if state.get("needs_approval") else "solve"
    graph.add_conditional_edges("triage", after_triage, {"human_gate": "human_gate", "solve": "solve"})
    # After human_gate, either loop back to solve (if approved) or end waiting:
    def after_gate(state: PlanState) -> str:
        return "solve" if not state.get("awaiting_approval") else END
    graph.add_conditional_edges("human_gate", after_gate, {"solve": "solve", END: END})
    graph.add_edge("export", END)

    return graph.compile()