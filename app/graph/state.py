from typing import TypedDict, Any, Dict, List

class PlanState(TypedDict, total=False):
    # Lifecycle
    status: str  # INIT, INGESTED, CONSTRAINED, SOLVED, VALIDATED, REVIEW, FINALIZED
    logs: List[str]
    steps: List[str]

    # Data entities (use refs to large tables in real app)
    employees: List[Dict[str, Any]]
    absences: List[Dict[str, Any]]
    constraints: Dict[str, Any]
    demand: List[Dict[str, Any]]
    solution: Dict[str, Any]
    audit: Dict[str, Any]
    kpis: Dict[str, Any]

    # Control flags
    needs_approval: bool
    awaiting_approval: bool
    relaxations: List[Dict[str, Any]]
    exported: bool