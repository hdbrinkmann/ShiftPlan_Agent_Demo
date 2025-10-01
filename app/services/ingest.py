from typing import List, Dict, Any

def parse_sources() -> tuple[list[dict], list[dict]]:
    # Replace with pandas/openpyxl parsing from uploaded Excel sheets
    employees: List[Dict[str, Any]] = [
        {"id": "E1", "name": "Alice", "hourly_cost": 18.0, "skills": ["cashier", "sales"], "max_hours_week": 30},
        {"id": "E2", "name": "Bob", "hourly_cost": 20.0, "skills": ["cashier"], "max_hours_week": 20},
        {"id": "E3", "name": "Cora", "hourly_cost": 22.0, "skills": ["sales"], "max_hours_week": 35},
    ]
    absences: List[Dict[str, Any]] = [
        # {"employee_id": "E2", "day": "Mon", "time": "09:00-13:00", "type": "vacation"}
    ]
    return employees, absences