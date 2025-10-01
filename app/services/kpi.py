from typing import Dict, Any, List

def compute(solution: Dict[str, Any], employees: List[Dict[str, Any]], demand: List[Dict[str, Any]], constraints: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    assignments = solution.get("assignments", [])
    cost = sum(a["hours"] * a["cost_per_hour"] for a in assignments)
    # Simple coverage: sum min(actual, required) / sum(required)
    # Build coverage
    needed = 0
    covered = 0
    actual_map = {}
    for a in assignments:
        key = (a["day"], a["time"], a["role"])
        actual_map[key] = actual_map.get(key, 0) + 1
    for need in demand:
        key = (need["day"], need["time"], need["role"])
        req = need["qty"]
        act = actual_map.get(key, 0)
        needed += req
        covered += min(req, act)
    coverage = (covered / needed) if needed else 1.0

    return {
        "cost": round(cost, 2),
        "coverage": round(coverage, 3),
        **current,
    }