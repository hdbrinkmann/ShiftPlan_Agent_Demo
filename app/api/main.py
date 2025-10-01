from fastapi import FastAPI
from pydantic import BaseModel
from app.graph.build import build_graph

app = FastAPI(title="Shift Planning Sample (LangGraph)")

@app.get("/")
def root():
    return {"ok": True, "message": "Shift Planning Sample (LangGraph). POST /run to execute."}

class RunRequest(BaseModel):
    auto_approve: bool = True
    budget: float | None = None

@app.post("/run")
def run(req: RunRequest):
    graph = build_graph()
    # Initial input/state seed
    initial_state = {
        "status": "INIT",
        "needs_approval": False,
        "awaiting_approval": False,
        "kpis": {"budget": req.budget} if req.budget is not None else {},
        "logs": [],
    }
    final_state = graph.invoke(initial_state, config={"auto_approve": req.auto_approve})
    return final_state