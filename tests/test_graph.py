from app.graph.build import build_graph

def test_graph_runs():
    graph = build_graph()
    state = graph.invoke({"status": "INIT"}, config={"auto_approve": True})
    assert state["status"] in ("FINALIZED", "VALIDATED", "SOLVED")
    assert "logs" in state
    
    # Check that logs mention either Excel or stub demand
    logs_str = " ".join(state.get("logs", []))
    assert ("from Excel" in logs_str or "Expanded core requirements into demand" in logs_str), \
        "Logs should mention either Excel ingestion or stub demand"