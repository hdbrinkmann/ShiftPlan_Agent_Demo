# ShiftPlan Agent Demo

This demo shows how a small "agent swarm" collaboratively creates a shift plan – from input data (Excel) to the finished result in the browser. The target audience is non-technical users: everything is intentionally explained in a simple and comprehensible way.

## What the Application Does – In One Sentence

It loads employee, absence, and opening hours requirements from Excel, cost-effectively assigns the right employees to time blocks (Store Manager including Assistant as backup, Sales), checks rules, and displays the result as a table in the browser.

## How to Start the Demo

1) Install Prerequisites
    - Python 3.11
    - Create a virtual environment in the project folder and install dependencies (see requirements.txt)

2) Start Server
    - Start from project root: `python3 -m uvicorn app.api.main:app --host 127.0.0.1 --port 7001`

3) Open Browser
    - UI at `http://127.0.0.1:7001/ui/`
    - Upload the Excel file there and then execute "Run".

## The Agents – Who Does What?

The logic is implemented as a chain of "agents" (nodes). Each agent has a clearly defined task:

- Ingest Agent (`ingest_node`)
   - Task: Load input data. When you upload an Excel file, employees, absences, and requirements (headcount per time block) are extracted from it.
   - Result: A clean list of employees (including cost per hour and roles/skills), absences, and requirement rows.

- Rules Agent (`rules_node`)
   - Task: Define simple rules, e.g., max. hours per day, rest time between days, skill requirements.
   - Result: A "constraints" package that all subsequent agents know about.

- Demand Agent (`demand_node`)
   - Task: Compile the requirements. For an "Opening Hours" sheet, roles are read as columns (e.g., "Store Manager", "Sales") – the numbers are headcount.
   - Result: A list of rows like: day, time span, role, quantity.

- Solver Agent (`solve_node`)
   - Task: Assign employees to demand peaks – cost-effectively and rule-compliant.
   - Approach (simplified):
      - For "Store Manager", real Store Managers are assigned first, then Assistant/Deputy as backup.
      - For "Sales", Sales profiles are used; optionally "Cashier" can help out.
      - Candidates are sorted by (match quality, cost) and a small fairness factor. Slight rotation prevents always taking the same ones first.
      - Double-booking of the same person in the identical time block is prevented. Absences and simple max-hours/rest-time rules are considered.
   - Result: A list of "assignments" with day, time, role, employee, hours, and cost/h.

- Audit Agent (`audit_node`)
   - Task: Checks whether the demand per block is actually covered (e.g., headcount fulfilled).
   - Result: List of deviations (e.g., undercoverage) for later evaluation.

- KPI Agent (`kpi_node`)
   - Task: Calculate simple metrics – primarily total cost and coverage rate.
   - Result: Display KPIs so you can see if the solution is "good enough".

- Triage Agent (`triage_node`) and Human Gate (`human_gate_node`)
   - Task: If rules are violated or budget exceeded, triage suggests small relaxations (e.g., +0.5 hours max/day). The Human Gate decides: auto-approve (demo) or wait for approval.

- Export Agent (`export_node`)
   - Task: Completion of planning (in the demo just an "ok" – here an export to Excel/CSV/ERP could follow).

The UI shows live which agent is currently active and what it's doing (SSE telemetry).

## How Is This Implemented with LangGraph?

Think of the agents as stations on a chain. LangGraph allows you to clearly define and connect these stations:

- We build a graph with fixed nodes (Ingest → Rules → Demand → Solver → Audit → KPI …).
- Simple data packages ("State") flow between nodes. Each node reads what it needs (e.g., employees, demand) and appends its result.
- After the KPI agent, a simple "switch" decides: If everything fits, go directly to Export. If not, go via Triage and (optionally) Human Gate back to Solve.
- Each node reports status information (via events) to the UI so you can follow the progress live.

This sounds technical, but at its core it's simple: a pipeline of work steps, each enriching its partial result, together achieving a goal: a practical shift plan.

## Excel Upload – What to Watch Out For?

- Employees: Columns like Name, a role/position specification (e.g., "Store Manager", "Assistant Store Manager", "Sales") and "Cost per hour in EUR" (automatically recognized). If the "Skills" column is missing, we interpret the position as a skill.
- Absences: optional, but helpful (day, from/to, type).
- Demand (e.g., "Opening Hours" sheet): Columns for date/day and "From/To" for time. Roles (Store Manager, Sales, …) as columns; the numbers are the headcount.

## Costs, Rules, and Metrics

- Costs: Sum of "hours in block × cost per hour" across all assignments (per employee). Hourly rates are robustly read from Excel (various notations are recognized).
- Rules: Kept simple but effective – skill match, no double-booking in the same time block, absences, max. hours per day/week (if set), and rest times between days.
- KPIs: Total cost and coverage rate (how much of the headcount per block was covered).

## Chat Function – Modify Plan During Runtime

After creating a plan, you can enter a message in the text field below, e.g.:
- "Knut is sick on 22.09.2025"
- "Stefan is sick until Friday"
- "Maria is sick from 01.10.2025 to 05.10.2025"

The system automatically processes your message:
1. Recognizes the employee (e.g., "Knut" → employee number 10118)
2. Recognizes the date or time period
3. Adds the absence
4. Recreates the plan considering the change

### How Does This Work?

The chat function uses two approaches:

**LLM-based (when activated):**
- An AI model analyzes your message in any language
- Automatically extracts employee ID, date, and type of absence
- Also works with more flexible formulations like "Knut is sick starting tomorrow for 3 days"

**Rule-based (Fallback):**
- If the LLM is not available, a simple rule-based parser kicks in
- Recognizes German sentences like "Name is sick on/until/from date"
- Less flexible, but reliable for standard cases

You can choose between both modes via the environment variable `SHIFTPLAN_USE_LLM_INTENTS` in the `.env` file:
- `SHIFTPLAN_USE_LLM_INTENTS=1` → LLM mode (more flexible, language-independent)
- `SHIFTPLAN_USE_LLM_INTENTS=0` → Only rule-based (simpler, offline-capable)

### Intelligent Replacement Planning

When a Store Manager is unavailable (e.g., Knut), the system automatically tries to:
1. Find **another Store Manager**
2. If not available: deploy an **Assistant Store Manager** as replacement
3. If that's also not possible: the Audit agent reports understaffing as a warning

The system considers:
- Who is available (no double-booking, no absences)
- Who is qualified (skills must match)
- Who is cost-effective (prefers cheaper employees)
- Who is fairly distributed (prevents overloading individual employees)

## LLM Integration (Optional)

A client for Scaleway LLM (or OpenAI-compatible APIs) is integrated. The LLM is used for two purposes:

1. **Chat Intent Recognition**: Understands your messages in natural language and extracts structured information
2. **Step Summaries**: Comments on individual agent steps in the UI (optional)

If no credentials are set, the demo continues offline with rule-based fallbacks. The LLM is not critical for core functionality.

## Endpoints & UI

- UI: `GET /ui/` → Upload, Start, Live Progress, Result Table, Chat Input.
- API:
   - `POST /upload` → Upload Excel.
   - `POST /run` → Execute graph (JSON body: `{ "auto_approve": true }`).
   - `POST /chat` → Send message to modify plan (JSON body: `{ "message": "Knut is sick on 22.09.2025", "run_id": "default", "auto_approve": true }`).
   - `POST /result` → Delivers result HTML with table.
   - `GET /inspect` → Shows loaded data (counts/samples).
   - `GET /llm_status` → Shows whether LLM is activated and which model is used.

## Demo Limitations and Outlook

- The solver is intentionally simple (greedy) but already delivers usable results. For complex plans, an optimizer (e.g., OR-Tools) can be integrated.
- The rules are minimal and can be extended (breaks, tariff rules, shift sequences, preferences …).
- Export is currently a placeholder – here files or system interfaces could be connected.

The strength of the solution lies in its clear structure: Each step is independent and comprehensible. This allows you to refine the logic step by step without complicating the overall system.
