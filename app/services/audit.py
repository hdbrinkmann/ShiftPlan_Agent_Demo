from typing import Dict, Any, List

def check(solution: Dict[str, Any], constraints: Dict[str, Any], demand: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Audit shift plan assignments against demand requirements.
    Checks for under-coverage and constraint violations.
    """
    violations = []
    
    # Get assignments - handle both old and new format
    assignments = solution.get("assignments", [])
    if not assignments and "assignments_raw" in solution:
        assignments = solution.get("assignments_raw", [])
    
    # Build coverage count by (day, time, role)
    coverage = {}
    for a in assignments:
        try:
            day = str(a.get("day", "")).strip()
            time = _normalize_time_format(a.get("time", ""))
            role = str(a.get("role", "")).strip()
            
            if not day or not time or not role:
                continue
            
            key = (day, time, role)
            coverage[key] = coverage.get(key, 0) + 1
        except Exception as e:
            print(f"[AUDIT] Error processing assignment: {e}")
            continue
    
    # Check demand coverage
    for need in demand:
        try:
            day = str(need.get("day", "")).strip()
            time = _normalize_time_format(need.get("time", ""))
            role = str(need.get("role", "")).strip()
            qty = int(need.get("qty", 0) or 0)
            
            if not day or not time or not role:
                continue
            
            key = (day, time, role)
            cov = coverage.get(key, 0)
            
            if cov < qty:
                violations.append({
                    "type": "under_coverage",
                    "day": day,
                    "time": time,
                    "role": role,
                    "required": qty,
                    "actual": cov,
                    "severity": "medium" if qty - cov == 1 else "high",
                })
        except Exception as e:
            print(f"[AUDIT] Error checking demand: {e}")
            continue
    
    print(f"[AUDIT] Checked {len(assignments)} assignments against {len(demand)} demand entries")
    print(f"[AUDIT] Found {len(violations)} violations")
    
    return {"violations": violations}


def _normalize_time_format(time_str: str) -> str:
    """
    Normalize time format to be consistent.
    Converts "09:00:00-17:00:00" to "09:00-17:00" for comparison.
    """
    if not time_str:
        return ""
    
    try:
        # If format is HH:MM:SS-HH:MM:SS, convert to HH:MM-HH:MM
        if time_str.count(":") >= 4:  # Has seconds
            parts = time_str.split("-")
            if len(parts) == 2:
                start = ":".join(parts[0].split(":")[:2])  # Take HH:MM only
                end = ":".join(parts[1].split(":")[:2])
                return f"{start}-{end}"
        return time_str
    except Exception:
        return time_str
