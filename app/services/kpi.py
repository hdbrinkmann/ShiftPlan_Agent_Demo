from typing import Dict, Any, List

def compute(solution: Dict[str, Any], employees: List[Dict[str, Any]], demand: List[Dict[str, Any]], constraints: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute KPIs for shift plan solution.
    Returns cost, coverage, and other metrics.
    """
    # Get assignments - handle both old and new format
    assignments = solution.get("assignments", [])
    if not assignments and "assignments_raw" in solution:
        assignments = solution.get("assignments_raw", [])
    
    # Calculate total cost
    cost = 0
    for a in assignments:
        try:
            hours = float(a.get("hours", 0) or 0)
            cost_per_hour = float(a.get("cost_per_hour", 0) or 0)
            cost += hours * cost_per_hour
        except Exception as e:
            print(f"[KPI] Error calculating cost for assignment: {e}")
            continue
    
    # Calculate coverage
    needed = 0
    covered = 0
    actual_map = {}
    
    # Build actual staffing map
    for a in assignments:
        try:
            day = str(a.get("day", "")).strip()
            time = _normalize_time_format(a.get("time", ""))
            role = str(a.get("role", "")).strip()
            
            if not day or not time or not role:
                continue
            
            key = (day, time, role)
            actual_map[key] = actual_map.get(key, 0) + 1
        except Exception as e:
            print(f"[KPI] Error processing assignment: {e}")
            continue
    
    # Compare with demand
    for need in demand:
        try:
            day = str(need.get("day", "")).strip()
            time = _normalize_time_format(need.get("time", ""))
            role = str(need.get("role", "")).strip()
            req = int(need.get("qty", 0) or 0)
            
            if not day or not time or not role or req <= 0:
                continue
            
            key = (day, time, role)
            act = actual_map.get(key, 0)
            needed += req
            covered += min(req, act)
        except Exception as e:
            print(f"[KPI] Error processing demand: {e}")
            continue
    
    coverage = (covered / needed) if needed > 0 else 1.0
    
    # Count unique employees used
    unique_employees = set()
    for a in assignments:
        emp_id = str(a.get("employee_id", ""))
        if emp_id:
            unique_employees.add(emp_id)
    
    result = {
        "cost": round(cost, 2),
        "coverage": round(coverage, 3),
        "employees_used": len(unique_employees),
        "total_assignments": len(assignments),
    }
    
    # Merge with current if provided
    if current:
        result.update(current)
    
    print(f"[KPI] Cost: {result['cost']}, Coverage: {result['coverage']}, Employees: {result['employees_used']}")
    
    return result


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
