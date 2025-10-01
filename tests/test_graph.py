from app.graph.build import build_graph

def test_graph_runs():
    graph = build_graph()
    config = {"configurable": {"auto_approve": True}}
    state = graph.invoke({"status": "INIT"}, config=config)
    assert state["status"] in ("FINALIZED", "VALIDATED", "SOLVED")
    assert "logs" in state