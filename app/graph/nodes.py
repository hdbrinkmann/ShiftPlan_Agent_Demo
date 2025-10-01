from app.graph.state import PlanState
from app.services import ingest as ingest_svc
from app.services import solver as solver_svc
from app.services import audit as audit_svc
from app.services import kpi as kpi_svc

def log(state: PlanState, message: str) -> None:
    state.setdefault("logs", []).append(message)

def ingest_node(state: PlanState) -> PlanState:
    employees, absences = ingest_svc.parse_sources()
    new_state: PlanState = {
        **state,
        "status": "INGESTED",
        "employees": employees,
        "absences": absences,
    }
    log(new_state, "Ingested employees and absences.")
    return new_state

def rules_node(state: PlanState) -> PlanState:
    constraints = {
        "hard": {
            "max_hours_per_day": 8,
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
    # Stub: one store, two roles, simple day demand
    demand = [
        {"day": "Mon", "time": "09:00-13:00", "role": "cashier", "qty": 2},
        {"day": "Mon", "time": "13:00-18:00", "role": "cashier", "qty": 2},
        {"day": "Mon", "time": "09:00-18:00", "role": "sales", "qty": 1},
    ]
    new_state: PlanState = {**state, "demand": demand}
    log(new_state, "Expanded core requirements into demand.")
    return new_state

def solve_node(state: PlanState) -> PlanState:
    solution = solver_svc.solve(
        employees=state.get("employees", []),
        absences=state.get("absences", []),
        constraints=state.get("constraints", {}),
        demand=state.get("demand", []),
    )
    new_state: PlanState = {**state, "status": "SOLVED", "solution": solution}
    log(new_state, "Solved schedule (stub).")
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

