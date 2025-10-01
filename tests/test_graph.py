from app.graph.build import build_graph

def test_graph_runs():
    graph = build_graph()
    state = graph.invoke({"status": "INIT"}, config={"auto_approve": True})
    assert state["status"] in ("FINALIZED", "VALIDATED", "SOLVED")
    assert "logs" in state