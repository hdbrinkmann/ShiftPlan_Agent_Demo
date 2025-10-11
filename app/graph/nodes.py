from app.graph.state import PlanState
from app.services import ingest as ingest_svc
from app.data import store
from app.services import solver_optimal as solver_svc  # Using OR-Tools optimal solver
from app.services import audit as audit_svc
from app.services import kpi as kpi_svc
from app.services import demand_processor
from app.services import shift_formatter

def log(state: PlanState, message: str) -> None:
    state.setdefault("logs", []).append(message)

def ingest_node(state: PlanState) -> PlanState:
    # Load data from store
    employees, absences = ingest_svc.parse_sources()
    
    # IMPORTANT: If state already has absences (e.g., from chat modifications),
    # merge them instead of overwriting
    existing_absences = state.get("absences") or []
    
    print(f"[INGEST_NODE] Store absences: {len(absences)}")
    print(f"[INGEST_NODE] State absences: {len(existing_absences)}")
    
    if existing_absences:
        # Use existing absences from state (includes chat modifications)
        final_absences = existing_absences
        log_msg = f"Ingested employees. Using existing absences from state. emp={len(employees)}, abs={len(final_absences)}"
        print(f"[INGEST_NODE] Using state absences (from chat)")
        # Show last 3 for debug
        for a in final_absences[-3:]:
            print(f"[INGEST_NODE] Absence: emp={a.get('employee_id')}, day={a.get('day')}, time={a.get('time')}")
    else:
        # No existing absences in state, use what we loaded
        final_absences = absences
        log_msg = f"Ingested employees and absences from store. emp={len(employees)}, abs={len(final_absences)}"
        print(f"[INGEST_NODE] Using store absences")
    
    new_state: PlanState = {
        **state,
        "status": "INGESTED",
        "employees": employees,
        "absences": final_absences,
    }
    log(new_state, log_msg)
    return new_state

def rules_node(state: PlanState) -> PlanState:
    constraints = {
        "hard": {
            "max_hours_per_day": 8,
            "max_hours_per_week": 37.5,  # Default weekly limit for employees
            "min_rest_hours": 11,
            "require_skill_match": True,
        },
        "soft": {
            "fair_weekends": {"weight": 2.0},
            "avoid_overtime": {"weight": 5.0},
        },
    }
    new_state: PlanState = {**state, "status": "CONSTRAINED", "constraints": constraints}
    log(new_state, "Formalized rules into constraints.")
    return new_state

def demand_node(state: PlanState) -> PlanState:
    # If uploaded demand available and non-empty, use it; otherwise stub
    uploaded_demand = []
    if store.has_any():
        try:
            uploaded_demand = store.get_data()[2] or []
        except Exception:
            uploaded_demand = []
    demand = uploaded_demand if uploaded_demand else [
        {"day": "Mon", "time": "09:00-13:00", "role": "cashier", "qty": 2},
        {"day": "Mon", "time": "13:00-18:00", "role": "cashier", "qty": 2},
        {"day": "Mon", "time": "09:00-18:00", "role": "sales", "qty": 1},
    ]
    
    # New solver works directly with original demand blocks (no hourly splitting needed)
    # It will generate optimal shift templates internally
    
    new_state: PlanState = {
        **state, 
        "demand": demand,
        "demand_original": demand,  # Keep for reference
    }
    log(new_state, f"Loaded demand requirements. rows={len(demand)} (uploaded={'yes' if uploaded_demand else 'no'})")
    return new_state

def solve_node(state: PlanState) -> PlanState:
    employees = state.get("employees", [])
    solution = solver_svc.solve(
        employees=employees,
        absences=state.get("absences", []),
        constraints=state.get("constraints", {}),
        demand=state.get("demand", []),
    )
    
    # Consolidate assignments into employee-centric shifts
    raw_assignments = solution.get("assignments", [])
    consolidated_shifts = shift_formatter.consolidate_shifts(raw_assignments, employees)
    
    # Store both formats
    solution["shifts"] = consolidated_shifts
    solution["assignments_raw"] = raw_assignments
    
    new_state: PlanState = {**state, "status": "SOLVED", "solution": solution}
    log(new_state, f"Solved schedule. {len(raw_assignments)} assignments -> {len(consolidated_shifts)} shifts.")
    return new_state

def audit_node(state: PlanState) -> PlanState:
    audit = audit_svc.check(
        solution=state.get("solution", {}),
        constraints=state.get("constraints", {}),
        demand=state.get("demand", []),
    )
    new_state: PlanState = {**state, "status": "VALIDATED", "audit": audit}
    log(new_state, f"Audit completed. Violations: {len(audit.get('violations', []))}.")
    return new_state

def kpi_node(state: PlanState) -> PlanState:
    kpis = kpi_svc.compute(
        solution=state.get("solution", {}),
        employees=state.get("employees", []),
        demand=state.get("demand", []),
        constraints=state.get("constraints", {}),
        current=state.get("kpis", {}),
    )
    new_state: PlanState = {**state, "kpis": kpis}
    log(new_state, f"KPIs computed. Cost={kpis.get('cost')}, Coverage={kpis.get('coverage')}.")
    return new_state

def triage_node(state: PlanState) -> PlanState:
    # Decide minimal relaxations if violations or over budget
    budget = state.get("kpis", {}).get("budget")
    violations = state.get("audit", {}).get("violations", [])
    over_budget = False
    if budget is not None:
        over_budget = (state.get("kpis", {}).get("cost", 0) or 0) > budget

    needs = bool(violations) or over_budget
    relaxations = []
    if needs:
        if violations:
            relaxations.append({"type": "allow_short_coverage", "limit": 1, "reason": "Minor coverage gap"})
        if over_budget:
            relaxations.append({"type": "increase_max_hours_per_day", "to": 8.5, "reason": "Reduce staffing peaks"})
    new_state: PlanState = {
        **state,
        "needs_approval": needs,
        "relaxations": relaxations if needs else [],
        # Warten erst nach Human-Gate, hier nur kennzeichnen
        "awaiting_approval": False,
        "status": "REVIEW" if needs else state.get("status", "VALIDATED"),
    }
    log(new_state, f"Triage done. needs_approval={needs}. relaxations={len(relaxations)}")
    return new_state

def _apply_relaxations_to_constraints(constraints: dict, relaxations: list[dict]) -> dict:
    # Defensive copy
    updated = {**constraints}
    hard = {**updated.get("hard", {})}
    for r in relaxations:
        if r.get("type") == "increase_max_hours_per_day" and "to" in r:
            hard["max_hours_per_day"] = r["to"]
        # Other relaxation types can be handled here
    if hard:
        updated["hard"] = hard
    return updated

def human_gate_node(state: PlanState, *, auto_approve: bool = False) -> PlanState:
    # If approval needed, either auto-approve and adjust constraints or pause awaiting approval
    needs = state.get("needs_approval", False)
    if not needs:
        log(state, "Human gate bypassed (no approval needed).")
        return state

    if auto_approve:
        relaxations = state.get("relaxations", [])
        constraints = state.get("constraints", {})
        if relaxations:
            constraints = _apply_relaxations_to_constraints(constraints, relaxations)
        new_state: PlanState = {
            **state,
            "constraints": constraints,
            "needs_approval": False,
            "awaiting_approval": False,
            # After applying relaxations, we will re-solve
            "status": "CONSTRAINED",
        }
        log(new_state, "Human gate auto-approved. Relaxations applied; returning to solve.")
        return new_state
    else:
        new_state: PlanState = {
            **state,
            "awaiting_approval": True,
            "status": "REVIEW",
        }
        log(new_state, "Awaiting human approval.")
        return new_state

def decide_after_kpi(state: PlanState) -> str:
    # Route to triage if violations present or budget exceeded; else export
    violations = state.get("audit", {}).get("violations", [])
    budget = state.get("kpis", {}).get("budget")
    cost = state.get("kpis", {}).get("cost")
    over_budget = False if budget is None else ((cost or 0) > budget)
    if violations or over_budget:
        return "triage"
    return "export"

def export_node(state: PlanState) -> PlanState:
    new_state: PlanState = {**state, "exported": True, "status": "FINALIZED"}
    log(new_state, "Exported plan (stub).")
    return new_state
