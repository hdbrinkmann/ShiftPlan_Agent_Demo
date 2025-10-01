from typing import List, Dict, Any
from app.data import store

def parse_sources() -> tuple[list[dict], list[dict]]:
    # Prefer uploaded data if available
    if store.has_any():
        employees, absences, _ = store.get_data()
        return employees, absences
    # Fallback stub data
    employees: List[Dict[str, Any]] = [
        {"id": "E1", "name": "Alice", "hourly_cost": 18.0, "skills": ["cashier", "sales"], "max_hours_week": 30},
        {"id": "E2", "name": "Bob", "hourly_cost": 20.0, "skills": ["cashier"], "max_hours_week": 20},
        {"id": "E3", "name": "Cora", "hourly_cost": 22.0, "skills": ["sales"], "max_hours_week": 35},
    ]
    absences: List[Dict[str, Any]] = []
    return employees, absences