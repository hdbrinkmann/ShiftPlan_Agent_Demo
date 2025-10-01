# Shift Planning Sample (LangGraph)

This sample wires a LangGraph agentic workflow for generating a shift plan.
It uses stubbed nodes and services so you can focus on the orchestration first and then replace stubs with real logic (Excel parsing, OR-Tools solver, audits, KPIs).

Quickstart
1) Install (Codespaces runs postCreateCommand to install deps automatically)
2) Run API
   uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000
3) Open the forwarded port (Codespaces will prompt) and call:
   GET /
   POST /run with body: {"auto_approve": true}

How to extend
- Replace services/ingest.py with real Excel parsing (pandas/openpyxl).
- Replace services/solver.py with an OR-Tools CP-SAT model.
- Expand services/audit.py with hard rule checks and severity tags.
- Expand services/kpi.py with costs, utilization, coverage, fairness.
- Add a persistent checkpointer (SQLite/Postgres) and artifact storage.

Human-in-the-loop
- The sample uses an auto_approve flag in /run body to pass the "human gate."
- For production, add a checkpointer and expose an endpoint to resume the graph after a pause/approval.