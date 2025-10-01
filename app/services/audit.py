from typing import Dict, Any, List

def check(solution: Dict[str, Any], constraints: Dict[str, Any], demand: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Stub audit: flag under-coverage by comparing count of assignments per (day,time,role) to qty
    violations = []
    # Build coverage count
    coverage = {}
    for a in solution.get("assignments", []):
        key = (a["day"], a["time"], a["role"])
        coverage[key] = coverage.get(key, 0) + 1

    for need in demand:
        key = (need["day"], need["time"], need["role"])
        cov = coverage.get(key, 0)
        if cov < need["qty"]:
            violations.append({
                "type": "under_coverage",
                "day": need["day"],
                "time": need["time"],
                "role": need["role"],
                "required": need["qty"],
                "actual": cov,
                "severity": "medium" if need["qty"] - cov == 1 else "high",
            })

    return {"violations": violations}