from typing import List, Dict, Any

def solve(employees: List[Dict[str, Any]], absences: List[Dict[str, Any]], constraints: Dict[str, Any], demand: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Stub “solver”: greedily assign cheapest qualified employees to cover demand
    # Replace with OR-Tools CP-SAT
    by_skill = {}
    for e in employees:
        for s in e.get("skills", []):
            by_skill.setdefault(s, []).append(e)
    for lst in by_skill.values():
        lst.sort(key=lambda x: x.get("hourly_cost", 0))

    assignments = []
    for need in demand:
        role = need["role"]
        qty = need["qty"]
        pool = by_skill.get(role, [])
        for i in range(min(qty, len(pool))):
            e = pool[i % len(pool)]
            assignments.append({
                "employee_id": e["id"],
                "role": role,
                "day": need["day"],
                "time": need["time"],
                "hours": _span_hours(need["time"]),
                "cost_per_hour": e["hourly_cost"],
            })
    return {"assignments": assignments}

def _span_hours(span: str) -> float:
    # "09:00-13:00" -> 4.0
    start, end = span.split("-")
    sh, sm = [int(x) for x in start.split(":")]
    eh, em = [int(x) for x in end.split(":")]
    return (eh + em/60) - (sh + sm/60)