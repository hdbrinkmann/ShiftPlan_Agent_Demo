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

## Using your own Excel

The application reads shift planning data from `./datafiles/Simple_Shift_Plan_Request.xlsx` with the following schema:

### Employees Sheet (Required)
- **id** (optional): Employee identifier. Auto-generated from row number if blank.
- **name** (required): Employee name.
- **hourly_cost** or **hourly_rate** (required): Cost per hour (flexible column name).
- **max_hours_week** (optional): Maximum weekly hours for the employee.
- **skills** (optional): Comma or semicolon-separated list of skills (e.g., "cashier,sales").

### Absences Sheet (Optional)
- **employee_id** or **name**: Employee identifier (matches Employees sheet).
- **date** (required): Absence date in YYYY-MM-DD format.
- **start_time** (required): Start time in HH:MM format.
- **end_time** (required): End time in HH:MM format.
- **type** (optional): Type of absence (e.g., "vacation", "sick").

### Demand Sheet (Required)
- **day** (required): Day of week (Mon, Tue, Wed, etc.) or date in YYYY-MM-DD format.
- **start_time** (required): Shift start time in HH:MM format.
- **end_time** (required): Shift end time in HH:MM format.
- **role** (required): Required role/skill for the shift.
- **qty** or **required** (required): Number of employees needed (flexible column name).

### Fallback Behavior
If the Excel file or required sheets are missing or malformed, the application automatically falls back to built-in stub data and continues to run. Logs will indicate whether data came from Excel or stub sources.

How to extend
- Replace services/solver.py with an OR-Tools CP-SAT model.
- Expand services/audit.py with hard rule checks and severity tags.
- Expand services/kpi.py with costs, utilization, coverage, fairness.
- Add a persistent checkpointer (SQLite/Postgres) and artifact storage.

Human-in-the-loop
- The sample uses an auto_approve flag in /run body to pass the "human gate."
- For production, add a checkpointer and expose an endpoint to resume the graph after a pause/approval.